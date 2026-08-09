"""是否需要读取上游额度快照的触发规则。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .types import LocalBundle, LocalParticipantData
from ..models import AppSettings, Observation, ParticipantSnapshot

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class SamplingTrigger:
    due: bool
    cost_progress: Decimal
    threshold_cost: Decimal
    cost_rolled_back: bool
    exhausted: bool
    active_too_long: bool
    reset_near: bool


def is_limit_exhausted(
    config: AppSettings,
    row: LocalParticipantData,
    previous: ParticipantSnapshot | None,
) -> bool:
    if previous is None or previous.remaining_share_percent <= 0:
        return False
    return row.balance.balance <= config.limit_warning_usd


def evaluate_sampling_trigger(
    *,
    config: AppSettings,
    local: LocalBundle,
    latest_raw: Observation,
    previous: Observation | None,
    now: datetime,
    force_upstream: bool,
    has_pending_rollback: bool,
) -> SamplingTrigger:
    previous_snapshots = (
        {
            item.participant_id: item
            for item in previous.participant_snapshots.all()
        }
        if previous
        else {}
    )
    selected_total = local.total.selected(config.cost_basis)
    cost_rolled_back = bool(
        previous
        and selected_total + CENT < previous.raw_selected_total_cost
    )
    cost_progress = (
        max(ZERO, selected_total - previous.raw_selected_total_cost)
        if previous
        else selected_total
    )
    effective_rate = (
        previous.effective_usd_per_percent
        if previous
        else config.initial_usd_per_percent
    )
    threshold_cost = effective_rate * config.progress_threshold_percent
    exhausted = any(
        is_limit_exhausted(
            config,
            row,
            previous_snapshots.get(row.participant.pk),
        )
        for row in local.participants
    )
    active_too_long = bool(
        previous
        and cost_progress > 0
        and now - previous.observed_at
        >= timedelta(hours=config.active_max_calibration_hours)
    )
    reset_near = now >= (
        latest_raw.upstream_resets_at
        - timedelta(minutes=config.reset_proximity_minutes)
    )
    due = bool(
        force_upstream
        or previous is None
        or cost_progress >= threshold_cost
        or cost_rolled_back
        or exhausted
        or active_too_long
        or reset_near
        or has_pending_rollback
    )
    return SamplingTrigger(
        due=due,
        cost_progress=cost_progress,
        threshold_cost=threshold_cost,
        cost_rolled_back=cost_rolled_back,
        exhausted=exhausted,
        active_too_long=active_too_long,
        reset_near=reset_near,
    )
