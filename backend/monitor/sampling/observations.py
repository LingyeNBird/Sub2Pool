"""不可变原始观测与可选 FAST 区间的采集持久化。"""

from datetime import datetime, timedelta

from django.db import transaction

from .types import LocalBundle, WindowReference
from ..accounting.boundaries import RATE_METHOD, same_official_reset
from ..fast_correction import (
    FastCorrectionInterval,
    apply_fast_interval,
    fetch_fast_interval,
)
from ..integrations.sub2api import Sub2APIError, Sub2APIReader, WeeklyWindow
from ..models import AppSettings, Observation, ParticipantSnapshot


@transaction.atomic
def create_raw_observation(
    *,
    config: AppSettings,
    reference: WindowReference,
    window: WeeklyWindow,
    local: LocalBundle,
    source: str,
    fast_interval: FastCorrectionInterval | None = None,
    fast_error: str = "",
) -> Observation:
    """持久化不可变采样事实；派生字段先给安全初值，随后由重放器覆盖。"""

    selected_total = local.total.selected(config.cost_basis)
    observation = Observation.objects.create(
        account_id=reference.account_id,
        source=source,
        observed_at=local.checked_at,
        window_seconds=reference.window_seconds,
        upstream_resets_at=reference.reset_at,
        upstream_used_percent=window.used_percent,
        raw_selected_total_cost=selected_total,
        selected_total_cost=selected_total,
        total_standard_cost=local.total.total_cost,
        total_actual_cost=local.total.total_actual_cost,
        effective_usd_per_percent=config.initial_usd_per_percent,
        sample_note="等待派生计算",
        raw_window={
            "slot": window.slot,
            "window_seconds": window.window_seconds,
            "reset_after_seconds": window.reset_after_seconds,
            "reset_at": window.reset_at,
            "query_mode": config.quota_query_mode,
            "sampled_at": window.sampled_at,
            "rate_method": RATE_METHOD,
            **({"fast_correction_error": fast_error} if fast_error else {}),
        },
    )
    ParticipantSnapshot.objects.bulk_create(
        [
            ParticipantSnapshot(
                observation=observation,
                participant=row.participant,
                raw_selected_cost=row.selected_cost(config.cost_basis),
                selected_cost=row.selected_cost(config.cost_basis),
                current_balance_usd=row.balance.balance,
                remaining_share_percent=row.participant.share_percent,
            )
            for row in local.participants
        ]
    )
    if fast_interval is not None:
        apply_fast_interval(observation, fast_interval)
        observation.save(
            update_fields=[
                "fast_correction_started_at",
                "fast_correction_standard_cost",
                "fast_correction_actual_cost",
                "fast_correction_request_count",
            ]
        )
    return observation


def fetch_fast_correction(
    client: Sub2APIReader,
    config: AppSettings,
    reference: WindowReference,
    latest_raw: Observation | None,
    ended_at: datetime,
) -> tuple[FastCorrectionInterval | None, str]:
    """读取一个原始采样区间的 FAST 请求；失败不阻断核心百分比采样。"""

    if not config.fast_correction_enabled:
        return None, ""
    if not callable(getattr(client, "usage_logs", None)):
        return None, ""

    official_start = reference.reset_at - timedelta(
        seconds=reference.window_seconds
    )
    started_at = official_start
    if latest_raw is not None and same_official_reset(
        latest_raw.upstream_resets_at,
        reference.reset_at,
    ):
        started_at = latest_raw.observed_at
    started_at = min(started_at, ended_at)
    try:
        return (
            fetch_fast_interval(
                client,
                account_id=reference.account_id,
                started_at=started_at,
                ended_at=ended_at,
                timezone_name=config.timezone,
            ),
            "",
        )
    except (Sub2APIError, ValueError) as exc:
        return None, str(exc)[:500]
