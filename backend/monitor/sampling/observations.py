"""上游百分比证据、Sub2API 成本快照与不可变请求事实的采集持久化。"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction

from .local_usage import observation_interval_costs
from .types import LocalBundle, WindowReference
from ..accounting.boundaries import same_official_reset
from ..billing_correction.facts import validate_interval_logs
from ..billing_correction.persistence import persist_capture
from ..integrations.sub2api import (
    Sub2APIError,
    Sub2APIReader,
    Sub2APIUsageLog,
    WeeklyWindow,
)
from ..models import (
    AppSettings,
    MonitoredAccount,
    Observation,
    ParticipantSnapshot,
    UsageSamplePoint,
)


@dataclass(frozen=True)
class BillingCaptureInterval:
    """A complete half-open request-log interval attached to one observation."""

    started_at: datetime
    ended_at: datetime
    request_count: int
    logs: tuple[Sub2APIUsageLog, ...]


@transaction.atomic
def create_raw_observation(
    *,
    config: AppSettings,
    account: MonitoredAccount,
    reference: WindowReference,
    quota_query_mode: str,
    window: WeeklyWindow,
    local: LocalBundle,
    source: str,
    sample_point: UsageSamplePoint,
    latest_raw: Observation | None = None,
    interval_logs: list[Sub2APIUsageLog] | None = None,
    billing_capture: BillingCaptureInterval | None = None,
    billing_capture_error: str = "",
) -> Observation:
    """保存采样证据；历史维护不会根据后续请求日志替换来源成本。"""

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
        correction_source=(
            "upstream" if account.upstream_pricing_applied else "none"
        ),
        frozen_correction_policy={},
        pricing_epoch=account.pricing_epoch,
        account_id=reference.account_id,
        sample_point=sample_point,
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
            "query_mode": quota_query_mode,
            "sampled_at": window.sampled_at,
            "cost_window_started_at": local.cost_window_started_at.isoformat(),
            "cost_window_ended_at": local.cost_window_ended_at.isoformat(),
            "interval_cost_source": interval_source,
            **(
                {"billing_capture_error": billing_capture_error}
                if billing_capture_error
                else {}
            ),
        },
    )
    ParticipantSnapshot.objects.bulk_create(
        [
            ParticipantSnapshot(
                observation=observation,
                participant=row.participant,
                source_sub2api_user_id=row.participant.sub2api_user_id,
                share_percent=row.contract.share_percent,
                is_owner=row.participant.is_owner,
                quota_pool_id=row.contract.pool_id,
                quota_pool_name=row.contract.pool.name,
                pool_contract_revision=row.contract.pool.contract_revision,
                raw_selected_cost=row.selected_cost(config.cost_basis),
                selected_cost=row.selected_cost(config.cost_basis),
                current_balance_usd=row.balance.balance,
                remaining_share_percent=row.contract.share_percent,
            )
            for row in local.participants
        ]
    )
    if billing_capture is not None:
        persist_capture(observation, billing_capture)
    return observation


def fetch_billing_capture(
    client: Sub2APIReader,
    config: AppSettings,
    reference: WindowReference,
    latest_raw: Observation | None,
    ended_at: datetime,
    *,
    prefetched_logs: list[Sub2APIUsageLog] | None = None,
) -> tuple[BillingCaptureInterval | None, str]:
    """Read and validate the immutable request facts for one observation.

    ``prefetched_logs`` is the complete log superset already fetched to bridge a
    cumulative-snapshot coordinate change. Reusing it avoids a second upstream
    request while still persisting exactly this observation's half-open range.
    """

    fetch_logs = getattr(client, "usage_logs", None)
    if not callable(fetch_logs):
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
        if prefetched_logs is None:
            logs = (
                []
                if started_at == ended_at
                else fetch_logs(
                    account_id=reference.account_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    timezone_name=config.timezone,
                )
            )
        else:
            logs = [
                log
                for log in prefetched_logs
                if started_at <= log.created_at < ended_at
            ]
        validate_interval_logs(
            logs,
            account_id=reference.account_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        return (
            BillingCaptureInterval(
                started_at=started_at,
                ended_at=ended_at,
                request_count=len(logs),
                logs=tuple(logs),
            ),
            "",
        )
    except (Sub2APIError, ValueError) as exc:
        return None, str(exc)[:500]
