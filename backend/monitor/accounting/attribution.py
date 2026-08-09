"""参与者成本、百分比权益与建议余额的区间归属。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from .contracts import ReplaySegment
from ..models import AppSettings, Observation, ParticipantSnapshot

ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")


class ParticipantCorrectionPrefix(Protocol):
    def user_between(
        self,
        user_id: int,
        started_at: datetime,
        ended_at: datetime,
        *,
        observation_id: int,
    ) -> Decimal: ...


@dataclass(frozen=True)
class ParticipantAttribution:
    snapshots: list[ParticipantSnapshot]
    participant_ids: set[int]
    roster_changed: bool


def apply_participant_attribution(
    *,
    observation: Observation,
    previous_snapshots: dict[int, ParticipantSnapshot],
    has_previous: bool,
    segment: ReplaySegment,
    correction_prefix: ParticipantCorrectionPrefix,
    selected_total: Decimal,
    interval_percent: Decimal,
    delta_percent: Decimal | None,
    delta_cost: Decimal | None,
    valid_sample: bool,
    effective_rate: Decimal,
    config: AppSettings,
) -> ParticipantAttribution:
    """计算并持久化一个观测点内全部参与者的派生归属。"""

    snapshots = list(observation.participant_snapshots.all())
    participant_ids = {snapshot.participant_id for snapshot in snapshots}
    roster_changed = bool(
        has_previous and participant_ids != set(previous_snapshots)
    )

    participant_deltas: dict[int, Decimal | None] = {}
    for snapshot in snapshots:
        snapshot.selected_cost = max(
            ZERO,
            snapshot.raw_selected_cost
            - segment.participant_baselines.get(snapshot.participant_id, ZERO)
            + correction_prefix.user_between(
                snapshot.participant.sub2api_user_id,
                segment.started_at,
                observation.observed_at,
                observation_id=observation.id,
            ),
        )
        old = previous_snapshots.get(snapshot.participant_id)
        participant_deltas[snapshot.participant_id] = (
            snapshot.selected_cost - old.selected_cost
            if old is not None
            else None
        )

    if roster_changed:
        # 参与者中途加入或退出时，旧观测没有完整的逐用户快照，不能把新参与者
        # 的整周期累计成本误当成“本次增量”。改用当前累计成本重分摊当前百分比。
        participant_weights = {
            snapshot.participant_id: max(ZERO, snapshot.selected_cost)
            for snapshot in snapshots
        }
        attribution_total = selected_total
    else:
        participant_weights = {
            snapshot.participant_id: max(
                ZERO,
                (
                    snapshot.selected_cost
                    if not has_previous
                    else participant_deltas[snapshot.participant_id] or ZERO
                ),
            )
            for snapshot in snapshots
        }
        attribution_total = (
            selected_total
            if not has_previous
            else max(ZERO, delta_cost or ZERO)
        )
    positive_total = sum(participant_weights.values(), ZERO)
    denominator = max(attribution_total, positive_total)

    for snapshot in snapshots:
        old = previous_snapshots.get(snapshot.participant_id)
        participant_delta = participant_deltas[snapshot.participant_id]
        positive_delta = participant_weights[snapshot.participant_id]
        old_charged = old.charged_cycle_percent if old is not None else ZERO
        if roster_changed:
            charged = (
                interval_percent * positive_delta / denominator
                if denominator > 0
                else ZERO
            )
            charged_delta = charged - old_charged
        else:
            charged_delta = ZERO
            if denominator > 0:
                if not has_previous:
                    charged_delta = interval_percent * positive_delta / denominator
                elif valid_sample and delta_percent is not None:
                    charged_delta = delta_percent * positive_delta / denominator
            charged = max(ZERO, old_charged + charged_delta)
        remaining = max(
            ZERO,
            snapshot.participant.share_percent - charged,
        )
        recommended = (
            remaining * effective_rate * config.safety_factor
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        balance = snapshot.current_balance_usd
        difference = (
            (recommended - balance).quantize(CENT, rounding=ROUND_HALF_UP)
            if balance is not None
            else None
        )
        exhausted = bool(
            balance is not None and balance <= config.limit_warning_usd
        )
        needs_update = bool(
            difference is not None
            and (
                abs(difference) >= config.recommendation_change_usd
                or (exhausted and remaining > 0)
            )
        )
        if remaining <= 0:
            reason = "本上游周期的百分比权益已用尽"
        elif exhausted:
            reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
        elif needs_update:
            reason = "当前用户余额与最新测算建议差异较大"
        else:
            reason = "当前用户余额无需调整"

        snapshot.delta_cost = participant_delta
        snapshot.charged_delta_percent = charged_delta.quantize(
            PCT_PRECISION,
            rounding=ROUND_HALF_UP,
        )
        snapshot.charged_cycle_percent = charged.quantize(
            PCT_PRECISION,
            rounding=ROUND_HALF_UP,
        )
        snapshot.remaining_share_percent = remaining.quantize(
            PCT_PRECISION,
            rounding=ROUND_HALF_UP,
        )
        snapshot.recommended_balance_usd = recommended
        snapshot.balance_difference_usd = difference
        snapshot.needs_manual_update = needs_update
        snapshot.reason = reason
    if snapshots:
        ParticipantSnapshot.objects.bulk_update(
            snapshots,
            [
                "selected_cost",
                "delta_cost",
                "charged_delta_percent",
                "charged_cycle_percent",
                "remaining_share_percent",
                "recommended_balance_usd",
                "balance_difference_usd",
                "needs_manual_update",
                "reason",
            ],
        )
    return ParticipantAttribution(
        snapshots=snapshots,
        participant_ids=participant_ids,
        roster_changed=roster_changed,
    )
