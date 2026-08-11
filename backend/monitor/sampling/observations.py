"""上游百分比证据、Sub2API 成本快照与可选 FAST 区间的采集持久化。"""

from datetime import datetime, timedelta

from django.db import transaction

from .local_usage import observation_interval_costs
from .types import LocalBundle, WindowReference
from ..accounting.boundaries import same_official_reset
from ..fast_correction.domain import FastCorrectionInterval
from ..fast_correction.persistence import apply_fast_interval
from ..fast_correction.service import fetch_fast_interval
from ..integrations.sub2api import (
    Sub2APIError,
    Sub2APIReader,
    Sub2APIUsageLog,
    WeeklyWindow,
)
from ..models import AppSettings, Observation, ParticipantSnapshot


@transaction.atomic
def create_raw_observation(
    *,
    config: AppSettings,
    reference: WindowReference,
    window: WeeklyWindow,
    local: LocalBundle,
    source: str,
    latest_raw: Observation | None = None,
    interval_logs: list[Sub2APIUsageLog] | None = None,
    fast_interval: FastCorrectionInterval | None = None,
    fast_error: str = "",
) -> Observation:
    """保存采样证据；历史维护可从请求日志重取成本，随后统一重放。"""

    selected_total = local.total.selected(config.cost_basis)
    (
        interval_started_at,
        interval_standard_cost,
        interval_actual_cost,
        interval_source,
    ) = observation_interval_costs(
        latest_raw,
        reference,
        local,
        interval_logs,
    )
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
        cost_window_started_at=local.cost_window_started_at,
        cost_window_ended_at=local.cost_window_ended_at,
        interval_cost_started_at=interval_started_at,
        interval_standard_cost=interval_standard_cost,
        interval_actual_cost=interval_actual_cost,
        interval_cost_source=interval_source,
        effective_usd_per_percent=config.initial_usd_per_percent,
        sample_note="等待派生计算",
        raw_window={
            "slot": window.slot,
            "window_seconds": window.window_seconds,
            "reset_after_seconds": window.reset_after_seconds,
            "reset_at": window.reset_at,
            "query_mode": config.quota_query_mode,
            "sampled_at": window.sampled_at,
            "cost_window_started_at": local.cost_window_started_at.isoformat(),
            "cost_window_ended_at": local.cost_window_ended_at.isoformat(),
            "interval_cost_source": interval_source,
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
