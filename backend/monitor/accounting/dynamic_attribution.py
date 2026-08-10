"""运行时变模型并把完整区间结果写回可重放账本。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import numpy as np

from .adaptive_range import AdaptiveRangeOutput, run_adaptive_range_filter
from .contracts import ReplaySegment
from .deterministic_bounds import project_attribution_to_bounds
from .model_inputs import (
    ALGORITHM_VERSION,
    build_dynamic_replay_input,
    stable_segment_seed,
)
from .particle_filter import QUANTIZER_NAMES, ParticleFilterConfig
from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import AppSettings, Observation, ParticipantSnapshot

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
MONEY_PRECISION = Decimal("0.0001")
PERCENT_PRECISION = Decimal("0.00001")
RATE_PRECISION = Decimal("0.000001")


def _decimal(value: float, precision: Decimal) -> Decimal:
    return Decimal(str(float(value))).quantize(precision, rounding=ROUND_HALF_UP)


def _diagnostics(
    *,
    row: int,
    seed: int,
    particle,
    bounds,
    projected_attribution,
    projection_repaired_rows: int,
    projection_max_adjustment_pp: float,
    residual_cost: Decimal,
    residual_subject: int,
    aggregate_cost_difference: Decimal,
    cost_monotonic_repair: Decimal,
    cost_monotonic_repair_subjects: int,
    total_cost_monotonic_repair: Decimal,
    filter_config: ParticleFilterConfig,
    adaptive: AdaptiveRangeOutput,
) -> dict:
    progress_lower = max(
        float(particle.total_percent_lower[row]),
        float(bounds.total_percent_lower[row]),
    )
    progress_upper = min(
        float(particle.total_percent_upper[row]),
        float(bounds.total_percent_upper[row]),
    )
    progress_interval_fallback = progress_lower > progress_upper
    if progress_interval_fallback:
        progress_lower = float(bounds.total_percent_lower[row])
        progress_upper = float(bounds.total_percent_upper[row])
    projected_total = float(projected_attribution[row].sum())
    progress_lower = max(
        float(bounds.total_percent_lower[row]),
        min(progress_lower, projected_total),
    )
    progress_upper = min(
        float(bounds.total_percent_upper[row]),
        max(progress_upper, projected_total),
    )

    return {
        "algorithm": ALGORITHM_VERSION,
        "seed": seed,
        "particles": filter_config.particles,
        "quantizer_probabilities": {
            name: round(float(particle.quantizer_probabilities[row, index]), 8)
            for index, name in enumerate(QUANTIZER_NAMES)
        },
        "speed_probabilities": {
            f"{int(tau)}h": round(
                float(particle.speed_probabilities[row, index]),
                8,
            )
            for index, tau in enumerate(filter_config.speed_taus_hours)
        },
        "ess_fraction": round(float(particle.ess_fraction[row]), 8),
        "resampled": bool(particle.resampled[row]),
        "raw_progress_probability_interval": [
            round(float(particle.total_percent_lower[row]), 5),
            round(float(particle.total_percent_upper[row]), 5),
        ],
        "progress_probability_interval": [
            round(progress_lower, 5),
            round(progress_upper, 5),
        ],
        "progress_interval_fallback": progress_interval_fallback,
        "progress_deterministic_bounds": [
            round(float(bounds.total_percent_lower[row]), 5),
            round(float(bounds.total_percent_upper[row]), 5),
        ],
        "deterministic_repairs": bounds.infeasible_repairs,
        "residual_cost_usd": float(residual_cost),
        "residual_attributed_percent": round(
            float(projected_attribution[row, residual_subject]),
            5,
        ),
        "residual_attributed_interval": [
            round(
                float(
                    particle.attributed_percent_lower[row, residual_subject]
                ),
                5,
            ),
            round(
                float(
                    particle.attributed_percent_upper[row, residual_subject]
                ),
                5,
            ),
        ],
        "aggregate_cost_difference_usd": float(
            aggregate_cost_difference
        ),
        "cost_monotonic_repair_usd": float(cost_monotonic_repair),
        "cost_monotonic_repair_subjects": cost_monotonic_repair_subjects,
        "total_cost_monotonic_repair_usd": float(
            total_cost_monotonic_repair
        ),
        "attribution_projection_applied": bool(
            np.max(
                np.abs(
                    projected_attribution[row]
                    - particle.attributed_percent_hat[row]
                )
            )
            > 1e-8
        ),
        "projection_repaired_rows": projection_repaired_rows,
        "projection_max_adjustment_pp": round(
            projection_max_adjustment_pp,
            5,
        ),
        "capacity_range_usd": [
            float(adaptive.capacity_min_usd[row]),
            float(adaptive.capacity_max_usd[row]),
        ],
        "capacity_range_stage": int(adaptive.stage[row]),
        "capacity_range_direction": (
            adaptive.direction if adaptive.stage[row] > 0 else None
        ),
        "capacity_range_promotions": [
            {
                "stage": index,
                "direction": promotion.direction,
                "model_row": promotion.row,
                "model_time_hours": round(promotion.time_hours, 8),
                "from_range_usd": [
                    promotion.from_min_usd,
                    promotion.from_max_usd,
                ],
                "to_range_usd": [
                    promotion.to_min_usd,
                    promotion.to_max_usd,
                ],
                "boundary_mass": round(promotion.boundary_mass, 8),
                "display_residual_pp": round(
                    promotion.display_residual_pp,
                    8,
                ),
            }
            for index, promotion in enumerate(adaptive.promotions, start=1)
            if promotion.row <= row
        ],
        "boundary_mass": {
            "lower": round(float(particle.lower_boundary_mass[row]), 8),
            "upper": round(float(particle.upper_boundary_mass[row]), 8),
        },
        "balance_interval_inflation": (
            filter_config.balance_interval_inflation
        ),
        "prior_capacity_usd": filter_config.initial_capacity_usd,
    }


def replay_dynamic_segment(
    *,
    account_id: int,
    segment: ReplaySegment,
    config: AppSettings,
    correction_prefix: FastCorrectionPrefix,
    prior_rate: Decimal | None = None,
) -> tuple[int, Decimal | None]:
    """一次运行完整区间，确保同一原始事实始终产生相同派生账本。"""

    replay_input = build_dynamic_replay_input(
        account_id=account_id,
        segment=segment,
        config=config,
        correction_prefix=correction_prefix,
    )
    seed = stable_segment_seed(account_id, segment)
    filter_config = ParticleFilterConfig(
        initial_capacity_usd=(
            float(prior_rate * Decimal("100"))
            if prior_rate is not None and prior_rate > ZERO
            else None
        )
    )
    adaptive = run_adaptive_range_filter(
        replay_input.model_input,
        seed=seed,
        config=filter_config,
    )
    particle = adaptive.particle
    bounds = adaptive.bounds
    (
        projected_attribution,
        projection_repaired_rows,
        projection_max_adjustment_pp,
    ) = project_attribution_to_bounds(
        particle.attributed_percent_hat,
        bounds,
    )
    projected_total = projected_attribution.sum(axis=1)
    projected_balance = (
        np.maximum(
            replay_input.model_input.rights_percent[None, :]
            - projected_attribution,
            0.0,
        )
        * particle.capacity_hat_usd[:, None]
        / 100.0
    )

    previous_observation: Observation | None = None
    previous_snapshots: dict[int, ParticipantSnapshot] = {}
    latest_rate: Decimal | None = None
    for observation_index, observation in enumerate(segment.observations):
        row = replay_input.observation_row_indices[observation_index]
        selected_total = replay_input.selected_totals[observation_index]
        interval_percent = max(
            ZERO,
            observation.upstream_used_percent - segment.percent_baseline,
        )
        delta_percent = (
            interval_percent - previous_observation.interval_used_percent
            if previous_observation is not None
            else None
        )
        delta_cost = (
            selected_total - previous_observation.selected_total_cost
            if previous_observation is not None
            else None
        )
        sample_rate = (
            selected_total / interval_percent
            if interval_percent > ZERO and selected_total > ZERO
            else None
        )
        capacity = _decimal(particle.capacity_hat_usd[row], MONEY_PRECISION)
        effective_rate = (capacity / Decimal("100")).quantize(
            RATE_PRECISION,
            rounding=ROUND_HALF_UP,
        )
        latest_rate = effective_rate

        observation.attribution_started_at = segment.started_at
        observation.selected_total_cost = selected_total
        observation.interval_used_percent = interval_percent
        observation.delta_percent = delta_percent
        observation.delta_cost = delta_cost
        observation.sample_usd_per_percent = (
            sample_rate.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)
            if sample_rate is not None
            else None
        )
        observation.effective_usd_per_percent = effective_rate
        observation.estimated_used_percent = _decimal(
            projected_total[row],
            PERCENT_PRECISION,
        )
        observation.capacity_lower_usd = _decimal(
            particle.capacity_lower_usd[row],
            MONEY_PRECISION,
        )
        observation.capacity_upper_usd = _decimal(
            particle.capacity_upper_usd[row],
            MONEY_PRECISION,
        )
        observation.valid_sample = sample_rate is not None
        observation.sample_note = (
            "累计成本与整数百分比仅作原始比值；时变归属采用自适应混合粒子滤波"
            if sample_rate is not None
            else "等待足够的成本与百分比观测"
        )
        observation.model_diagnostics = _diagnostics(
            row=row,
            seed=seed,
            particle=particle,
            bounds=bounds,
            projected_attribution=projected_attribution,
            projection_repaired_rows=projection_repaired_rows,
            projection_max_adjustment_pp=projection_max_adjustment_pp,
            residual_cost=replay_input.residual_costs[observation_index],
            residual_subject=len(replay_input.subject_user_ids) - 1,
            aggregate_cost_difference=(
                replay_input.aggregate_cost_differences[observation_index]
            ),
            cost_monotonic_repair=(
                replay_input.cost_monotonic_repairs[observation_index]
            ),
            cost_monotonic_repair_subjects=(
                replay_input.cost_monotonic_repair_subjects[observation_index]
            ),
            total_cost_monotonic_repair=(
                replay_input.total_cost_monotonic_repairs[observation_index]
            ),
            filter_config=filter_config,
            adaptive=adaptive,
        )
        raw_window = dict(observation.raw_window)
        raw_window.pop("conservative_percentile", None)
        raw_window.pop("rate_history_samples", None)
        raw_window.update(
            {
                "rate_method": ALGORITHM_VERSION,
                "rate_source": "particle_filter",
                "replay_segment_reason": segment.reason,
                "replay_decision": "included",
                "model_subject_count": len(replay_input.subject_user_ids),
            }
        )
        observation.raw_window = raw_window
        observation.save(
            update_fields=[
                "attribution_started_at",
                "selected_total_cost",
                "interval_used_percent",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "effective_usd_per_percent",
                "estimated_used_percent",
                "capacity_lower_usd",
                "capacity_upper_usd",
                "valid_sample",
                "sample_note",
                "model_diagnostics",
                "raw_window",
            ]
        )

        snapshots = list(observation.participant_snapshots.all())
        remaining_participant_ids = [
            item.participant_id
            for item in snapshots
            if (
                float(item.participant.share_percent)
                - projected_attribution[
                    row,
                    replay_input.participant_subject_indices[
                        item.participant_id
                    ],
                ]
            )
            > 1e-8
        ]
        sole_remaining_participant_id = (
            remaining_participant_ids[0]
            if len(snapshots) > 1 and len(remaining_participant_ids) == 1
            else None
        )
        for snapshot in snapshots:
            subject = replay_input.participant_subject_indices[snapshot.participant_id]
            old = previous_snapshots.get(snapshot.participant_id)
            selected_cost = _decimal(
                replay_input.model_input.costs_usd[row, subject],
                RATE_PRECISION,
            )
            charged = _decimal(
                projected_attribution[row, subject],
                PERCENT_PRECISION,
            )
            charged_lower_value = max(
                float(particle.attributed_percent_lower[row, subject]),
                float(bounds.attributed_percent_lower[row, subject]),
            )
            charged_upper_value = min(
                float(particle.attributed_percent_upper[row, subject]),
                float(bounds.attributed_percent_upper[row, subject]),
            )
            if charged_lower_value > charged_upper_value:
                charged_lower_value = float(
                    bounds.attributed_percent_lower[row, subject]
                )
                charged_upper_value = float(
                    bounds.attributed_percent_upper[row, subject]
                )
            charged_lower = _decimal(
                charged_lower_value,
                PERCENT_PRECISION,
            )
            charged_upper = _decimal(
                charged_upper_value,
                PERCENT_PRECISION,
            )
            remaining = max(ZERO, snapshot.participant.share_percent - charged)

            charged_lower = min(charged_lower, charged)
            charged_upper = max(charged_upper, charged)
            deterministic_min = _decimal(
                bounds.balance_lower_usd[row, subject],
                MONEY_PRECISION,
            )
            deterministic_max = _decimal(
                bounds.balance_upper_usd[row, subject],
                MONEY_PRECISION,
            )
            point_balance = _decimal(
                projected_balance[row, subject],
                MONEY_PRECISION,
            )
            point_balance = min(
                deterministic_max,
                max(deterministic_min, point_balance),
            )
            probability_min = max(
                deterministic_min,
                _decimal(
                    particle.balance_lower_usd[row, subject],
                    MONEY_PRECISION,
                ),
            )
            probability_max = min(
                deterministic_max,
                _decimal(
                    particle.balance_upper_usd[row, subject],
                    MONEY_PRECISION,
                ),
            )
            if probability_min > probability_max:
                probability_min, probability_max = (
                    deterministic_min,
                    deterministic_max,
                )

            probability_min = min(probability_min, point_balance)
            probability_max = max(probability_max, point_balance)
            recommendation_factor = (
                ONE
                if snapshot.participant_id == sole_remaining_participant_id
                else config.safety_factor
            )
            recommended = (point_balance * recommendation_factor).quantize(
                CENT,
                rounding=ROUND_HALF_UP,
            )
            recommended_min = (
                probability_min * recommendation_factor
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            recommended_max = (
                probability_max * recommendation_factor
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            current = snapshot.current_balance_usd
            difference = (
                (recommended - current).quantize(CENT, rounding=ROUND_HALF_UP)
                if current is not None
                else None
            )
            exhausted = bool(
                current is not None and current <= config.limit_warning_usd
            )
            needs_update = bool(
                difference is not None
                and (
                    abs(difference) >= config.recommendation_change_usd
                    or (exhausted and remaining > ZERO)
                )
            )
            overused = charged_lower > snapshot.participant.share_percent
            if overused:
                needs_update = False
            if overused:
                reason = (
                    "本上游周期已确认存在合同权益偏差，不再建议补充余额"
                )
            elif remaining <= ZERO:
                reason = "本上游周期的百分比权益已用尽"
            elif exhausted:
                reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
            elif snapshot.participant_id == sole_remaining_participant_id:
                reason = "其他参与者权益均已用尽，建议按完整剩余权益计算"
            elif needs_update:
                reason = "当前用户余额与最新测算建议差异较大"
            else:
                reason = "当前用户余额无需调整"
            recommendation_changed = bool(
                snapshot.recommended_balance_usd is not None
                and snapshot.recommended_balance_usd != recommended
            )

            snapshot.selected_cost = selected_cost
            snapshot.delta_cost = (
                selected_cost - old.selected_cost if old is not None else None
            )
            snapshot.charged_delta_percent = (
                charged - old.charged_cycle_percent if old is not None else charged
            )
            snapshot.charged_cycle_percent = charged
            snapshot.charged_percent_lower = charged_lower
            snapshot.charged_percent_upper = charged_upper
            snapshot.remaining_share_percent = remaining.quantize(
                PERCENT_PRECISION,
                rounding=ROUND_HALF_UP,
            )
            snapshot.recommended_balance_usd = recommended
            snapshot.recommended_balance_min_usd = recommended_min
            snapshot.recommended_balance_max_usd = recommended_max
            snapshot.deterministic_balance_min_usd = deterministic_min
            snapshot.deterministic_balance_max_usd = deterministic_max
            snapshot.balance_difference_usd = difference
            if snapshot.recommendation_applied and recommendation_changed:
                snapshot.recommendation_applied = False
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
                    "charged_percent_lower",
                    "charged_percent_upper",
                    "remaining_share_percent",
                    "recommended_balance_usd",
                    "recommended_balance_min_usd",
                    "recommended_balance_max_usd",
                    "deterministic_balance_min_usd",
                    "deterministic_balance_max_usd",
                    "balance_difference_usd",
                    "needs_manual_update",
                    "recommendation_applied",
                    "reason",
                ],
            )
        previous_observation = observation
        previous_snapshots = {item.participant_id: item for item in snapshots}

    return len(segment.observations), latest_rate
