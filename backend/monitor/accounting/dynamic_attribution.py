"""运行时变模型并把完整区间结果写回可重放账本。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .contracts import ReplaySegment
from .deterministic_bounds import run_deterministic_bounds
from .model_inputs import (
    ALGORITHM_VERSION,
    build_dynamic_replay_input,
    stable_segment_seed,
)
from .particle_filter import QUANTIZER_NAMES, ParticleFilterConfig, run_particle_filter
from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import AppSettings, Observation, ParticipantSnapshot

ZERO = Decimal("0")
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
    residual_cost: Decimal,
    aggregate_cost_difference: Decimal,
    filter_config: ParticleFilterConfig,
) -> dict:
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
        "progress_probability_interval": [
            round(float(particle.total_percent_lower[row]), 5),
            round(float(particle.total_percent_upper[row]), 5),
        ],
        "progress_deterministic_bounds": [
            round(float(bounds.total_percent_lower[row]), 5),
            round(float(bounds.total_percent_upper[row]), 5),
        ],
        "deterministic_repairs": bounds.infeasible_repairs,
        "residual_cost_usd": float(residual_cost),
        "aggregate_cost_difference_usd": float(
            aggregate_cost_difference
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
    particle = run_particle_filter(
        replay_input.model_input,
        seed=seed,
        config=filter_config,
    )
    bounds = run_deterministic_bounds(replay_input.model_input)

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
            particle.total_percent_hat[row],
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
            "累计成本与整数百分比仅作原始比值；时变归属采用混合粒子滤波"
            if sample_rate is not None
            else "等待足够的成本与百分比观测"
        )
        observation.model_diagnostics = _diagnostics(
            row=row,
            seed=seed,
            particle=particle,
            bounds=bounds,
            residual_cost=replay_input.residual_costs[observation_index],
            aggregate_cost_difference=(
                replay_input.aggregate_cost_differences[observation_index]
            ),
            filter_config=filter_config,
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
        for snapshot in snapshots:
            subject = replay_input.participant_subject_indices[snapshot.participant_id]
            old = previous_snapshots.get(snapshot.participant_id)
            selected_cost = _decimal(
                replay_input.model_input.costs_usd[row, subject],
                RATE_PRECISION,
            )
            charged = _decimal(
                particle.attributed_percent_hat[row, subject],
                PERCENT_PRECISION,
            )
            charged_lower = _decimal(
                particle.attributed_percent_lower[row, subject],
                PERCENT_PRECISION,
            )
            charged_upper = _decimal(
                particle.attributed_percent_upper[row, subject],
                PERCENT_PRECISION,
            )
            remaining = max(ZERO, snapshot.participant.share_percent - charged)

            deterministic_min = _decimal(
                bounds.balance_lower_usd[row, subject],
                MONEY_PRECISION,
            )
            deterministic_max = _decimal(
                bounds.balance_upper_usd[row, subject],
                MONEY_PRECISION,
            )
            point_balance = _decimal(
                particle.balance_hat_usd[row, subject],
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

            recommended = (point_balance * config.safety_factor).quantize(
                CENT,
                rounding=ROUND_HALF_UP,
            )
            recommended_min = (
                probability_min * config.safety_factor
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            recommended_max = (
                probability_max * config.safety_factor
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
            if remaining <= ZERO:
                reason = "本上游周期的百分比权益已用尽"
            elif exhausted:
                reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
            elif needs_update:
                reason = "当前用户余额与最新测算建议差异较大"
            else:
                reason = "当前用户余额无需调整"

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
                    "reason",
                ],
            )
        previous_observation = observation
        previous_snapshots = {item.participant_id: item for item in snapshots}

    return len(segment.observations), latest_rate
