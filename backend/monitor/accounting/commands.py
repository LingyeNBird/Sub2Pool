"""管理员触发的重放、排除、恢复与边界覆盖命令。"""

from datetime import datetime, timedelta

from django.utils import timezone

from .boundaries import (
    RESET_ROLLBACK_TOLERANCE,
    manual_start_interval_end_key,
    observation_key,
    same_official_reset,
)
from .contracts import ReplayResult
from .replay import _previous_included, _replay_anchor, rebuild_account
from ..history_state import LeaseGuard, fenced_fact_write
from ..models import AppSettings, Observation


def rebuild_current_interval(
    account_id: int,
    config: AppSettings | None = None,
) -> tuple[ReplayResult, datetime | None]:
    with fenced_fact_write(
        [account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        return _rebuild_current_interval(
            account_id,
            config,
            guard=guards[account_id],
        )


def _rebuild_current_interval(
    account_id: int,
    config: AppSettings | None = None,
    *,
    guard: LeaseGuard,
) -> tuple[ReplayResult, datetime | None]:
    """只重建当前归属区间的派生结果，并保留全部原始采样事实。"""

    latest = (
        Observation.objects.filter(account_id=account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return rebuild_account(account_id, config, guard=guard), None

    replay_from = _replay_anchor(
        latest,
        merge_previous=latest.is_manual_start,
    )
    return (
        rebuild_account(
            account_id,
            config,
            replay_from=replay_from,
            guard=guard,
        ),
        replay_from,
    )


def exclude_observation(
    observation: Observation,
    reason: str = "管理员手动排除",
) -> dict[str, int | bool | None]:
    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        return _exclude_observation(
            observation,
            reason,
            guard=guards[observation.account_id],
        )


def _exclude_observation(
    observation: Observation,
    reason: str,
    *,
    guard: LeaseGuard,
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
        guard=guard,
    )
    return {"already_excluded": already_excluded, **replay.as_dict()}


def restore_observation(
    observation: Observation,
) -> dict[str, int | bool | None]:
    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        return _restore_observation(
            observation,
            guard=guards[observation.account_id],
        )


def _restore_observation(
    observation: Observation,
    *,
    guard: LeaseGuard,
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
        observation.manual_start_end = observation
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
            "manual_start_end",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
        guard=guard,
    )
    observation.refresh_from_db()
    return {
        "already_included": already_included,
        "included": observation.excluded_at is None,
        "manual_start": observation.is_manual_start,
        **replay.as_dict(),
    }


def set_manual_start(
    observation: Observation,
    reason: str = "",
    *,
    end_observation: Observation | None = None,
) -> dict[str, int | bool | None]:
    end_observation = end_observation or observation
    if end_observation.account_id != observation.account_id:
        raise ValueError("管理员起点区间不能跨账号")
    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        return _set_manual_start(
            observation,
            end_observation,
            reason,
            guard=guards[observation.account_id],
        )


def _set_manual_start(
    observation: Observation,
    end_observation: Observation,
    reason: str,
    *,
    guard: LeaseGuard,
) -> dict[str, int | bool | None]:
    """设置闭区间人工起点，并把区间内已有人工起点合并到该区间。"""

    locked = {
        item.id: item
        for item in Observation.objects.select_for_update()
        .select_related("manual_start_end")
        .filter(pk__in={observation.pk, end_observation.pk})
    }
    observation = locked.get(observation.pk)
    end_observation = locked.get(end_observation.pk)
    if observation is None or end_observation is None:
        raise ValueError("管理员起点区间包含不存在的观测记录")
    if end_observation.account_id != observation.account_id:
        raise ValueError("管理员起点区间不能跨账号")

    start_key = observation_key(observation)
    end_key = observation_key(end_observation)
    if end_key < start_key:
        raise ValueError("管理员起点区间终点不能早于起点")

    contained_ids: list[int] = []
    existing_starts = (
        Observation.objects.select_for_update()
        .select_related("manual_start_end")
        .filter(
            account_id=observation.account_id,
            is_manual_start=True,
        )
        .exclude(pk=observation.pk)
    )
    for existing in existing_starts:
        existing_start = observation_key(existing)
        existing_end = manual_start_interval_end_key(existing)
        if existing_end is None:
            continue
        overlaps = existing_start <= end_key and start_key <= existing_end
        if not overlaps:
            continue
        if start_key <= existing_start and existing_end <= end_key:
            contained_ids.append(existing.id)
            continue
        raise ValueError("管理员起点区间与现有区间部分重叠")

    if contained_ids:
        Observation.objects.filter(pk__in=contained_ids).update(
            is_manual_start=False,
            manual_start_end=None,
            manual_start_reason="",
            manual_start_set_at=None,
        )

    already_set = bool(
        observation.is_manual_start
        and observation.manual_start_end_id == end_observation.id
    )
    observation.is_manual_start = True
    observation.manual_start_end = end_observation
    observation.manual_start_reason = reason.strip()[:255]
    observation.manual_start_set_at = timezone.now()
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_end",
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
        guard=guard,
    )
    return {
        "already_set": already_set,
        "end_observation_id": end_observation.id,
        "absorbed_manual_starts": len(contained_ids),
        **replay.as_dict(),
    }


def clear_manual_start(
    observation: Observation,
) -> dict[str, int | bool | None]:
    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        return _clear_manual_start(
            observation,
            guard=guards[observation.account_id],
        )


def _clear_manual_start(
    observation: Observation,
    *,
    guard: LeaseGuard,
) -> dict[str, int | bool | None]:
    """取消人工边界，并从它之前的有效区间重新连接后续数据。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    replay_from = _replay_anchor(observation, merge_previous=True)
    was_set = observation.is_manual_start
    observation.is_manual_start = False
    observation.manual_start_end = None
    observation.manual_start_reason = ""
    observation.manual_start_set_at = None
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_end",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
        guard=guard,
    )
    return {"was_set": was_set, **replay.as_dict()}
