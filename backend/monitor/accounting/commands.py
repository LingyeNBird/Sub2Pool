"""管理员触发的重放、排除、恢复与边界覆盖命令。"""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .boundaries import (
    RESET_ROLLBACK_TOLERANCE,
    same_official_reset,
)
from .contracts import ReplayResult
from .replay import _previous_included, _replay_anchor, rebuild_account
from ..models import AppSettings, Observation


def rebuild_current_interval(
    account_id: int,
    config: AppSettings | None = None,
) -> tuple[ReplayResult, datetime | None]:
    """只重建当前归属区间的派生结果，并保留全部原始采样事实。"""

    latest = (
        Observation.objects.filter(account_id=account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return rebuild_account(account_id, config), None

    replay_from = _replay_anchor(
        latest,
        merge_previous=latest.is_manual_start,
    )
    return (
        rebuild_account(
            account_id,
            config,
            replay_from=replay_from,
        ),
        replay_from,
    )


@transaction.atomic
def exclude_observation(
    observation: Observation,
    reason: str = "管理员手动排除",
) -> dict[str, int | bool | None]:
    """排除原始点，并从其原区间或被移除的手动边界之前重放。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    replay_from = _replay_anchor(
        observation,
        merge_previous=observation.is_manual_start,
    )
    already_excluded = observation.exclusion_source == "manual"
    observation.excluded_at = timezone.now()
    observation.exclusion_source = "manual"
    observation.exclusion_reason = reason.strip()[:255] or "管理员手动排除"
    observation.valid_sample = False
    observation.sample_usd_per_percent = None
    observation.sample_note = f"已排除：{observation.exclusion_reason}"
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "valid_sample",
            "sample_usd_per_percent",
            "sample_note",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    return {"already_excluded": already_excluded, **replay.as_dict()}


@transaction.atomic
def restore_observation(
    observation: Observation,
) -> dict[str, int | bool | None]:
    """恢复排除记录；若恢复后形成同窗口回退，则由管理员确认它是新起点。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_included = observation.excluded_at is None
    previous = _previous_included(observation)
    confirms_rollback = bool(
        not already_included
        and previous is not None
        and same_official_reset(
            previous.upstream_resets_at,
            observation.upstream_resets_at,
        )
        and observation.upstream_used_percent + RESET_ROLLBACK_TOLERANCE
        < previous.upstream_used_percent
    )
    if confirms_rollback and not observation.is_manual_start:
        observation.is_manual_start = True
        observation.manual_start_reason = "管理员恢复同一官方窗口内的回退记录"
        observation.manual_start_set_at = timezone.now()
        replay_from = observation.observed_at
    else:
        replay_from = _replay_anchor(observation)
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    observation.refresh_from_db()
    return {
        "already_included": already_included,
        "included": observation.excluded_at is None,
        "manual_start": observation.is_manual_start,
        **replay.as_dict(),
    }


@transaction.atomic
def set_manual_start(
    observation: Observation,
    reason: str = "",
) -> dict[str, int | bool | None]:
    """把一个真实观测点设为最高优先级零基线，并重放其后缀。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_set = observation.is_manual_start
    observation.is_manual_start = True
    observation.manual_start_reason = reason.strip()[:255]
    observation.manual_start_set_at = timezone.now()
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=observation.observed_at,
    )
    return {"already_set": already_set, **replay.as_dict()}


@transaction.atomic
def clear_manual_start(
    observation: Observation,
) -> dict[str, int | bool | None]:
    """取消人工边界，并从它之前的有效区间重新连接后续数据。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    replay_from = _replay_anchor(observation, merge_previous=True)
    was_set = observation.is_manual_start
    observation.is_manual_start = False
    observation.manual_start_reason = ""
    observation.manual_start_set_at = None
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    return {"was_set": was_set, **replay.as_dict()}
