"""只读重放当前归属区间，生成粒子滤波可视化数据。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .accounting.adaptive_range import run_adaptive_range_filter
from .accounting.boundaries import participant_raw_costs
from .accounting.contracts import ALGORITHM_VERSION, ReplaySegment
from .accounting.model_inputs import (
    build_dynamic_replay_input,
    stable_segment_seed,
)
from .accounting.particle_filter import ParticleFilterConfig
from .fast_correction.prefix import FastCorrectionPrefix
from .models import AppSettings, Observation
from .reporting import iso

ZERO = Decimal("0")
OBSERVED_BASELINE_REASONS = {"manual_override", "official_zero_observation"}
REASON_LABELS = {
    "official_window": "官方周期",
    "official_zero_observation": "官方 0% 起点",
    "manual_override": "管理员起点",
}


def _trajectory_periods(account_id: int) -> list[dict]:
    rows = (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("attribution_started_at", "observed_at", "id")
        .values(
            "id",
            "observed_at",
            "attribution_started_at",
            "upstream_resets_at",
        )
    )
    grouped: dict[datetime, dict] = {}
    for row in rows:
        started_at = row["attribution_started_at"]
        period = grouped.get(started_at)
        if period is None:
            grouped[started_at] = {
                "id": row["id"],
                "started_at": started_at,
                "first_observed_at": row["observed_at"],
                "last_observed_at": row["observed_at"],
                "resets_at": row["upstream_resets_at"],
                "observation_count": 1,
            }
            continue
        period["last_observed_at"] = row["observed_at"]
        period["resets_at"] = row["upstream_resets_at"]
        period["observation_count"] += 1

    periods = list(grouped.values())
    for index, period in enumerate(periods, start=1):
        period["sequence"] = index
        period["is_current"] = index == len(periods)
    return periods


def _segment_for_period(
    account_id: int,
    period: dict,
    cost_basis: str,
) -> ReplaySegment | None:
    observations = list(
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
            attribution_started_at=period["started_at"],
        )
        .prefetch_related("participant_snapshots__participant")
        .order_by("observed_at", "id")
    )
    if not observations:
        return None

    first = observations[0]
    reason = str(first.raw_window.get("replay_segment_reason") or "")
    if reason not in REASON_LABELS:
        if first.is_manual_start:
            reason = "manual_override"
        elif (
            first.observed_at == period["started_at"]
            and first.upstream_used_percent == ZERO
        ):
            reason = "official_zero_observation"
        else:
            reason = "official_window"

    observed_baseline = reason in OBSERVED_BASELINE_REASONS
    return ReplaySegment(
        observations=observations,
        started_at=period["started_at"],
        first_observed_at=first.observed_at,
        resets_at=period["resets_at"],
        reason=reason,
        total_baseline=first.normalized_cost(cost_basis) if observed_baseline else ZERO,
        participant_baselines=(
            participant_raw_costs(first) if observed_baseline else {}
        ),
        percent_baseline=(
            first.upstream_used_percent
            if reason == "manual_override"
            else ZERO
        ),
    )


def _initial_capacity(segment: ReplaySegment) -> float | None:
    diagnostics = segment.observations[0].model_diagnostics
    value = diagnostics.get("prior_capacity_usd") if diagnostics else None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def particle_trajectory_data(
    config: AppSettings,
    period_id: int | None = None,
) -> dict:
    """只读重放指定历史区间；默认选择当前区间。"""

    if not config.openai_account_id:
        return {
            "available": False,
            "message": "尚未配置 OpenAI 上游账号",
        }

    periods = _trajectory_periods(config.openai_account_id)
    if not periods:
        return {
            "available": False,
            "message": "尚无可重放的观测记录",
        }
    selected_period = periods[-1]
    if period_id is not None:
        selected_period = next(
            (period for period in periods if period["id"] == period_id),
            None,
        )
        if selected_period is None:
            raise ValueError("所选历史周期不存在")

    segment = _segment_for_period(
        config.openai_account_id,
        selected_period,
        config.cost_basis,
    )
    if segment is None:
        raise ValueError("所选历史周期没有可重放的观测记录")

    replay_input = build_dynamic_replay_input(
        account_id=config.openai_account_id,
        segment=segment,
        config=config,
        correction_prefix=FastCorrectionPrefix(
            config.openai_account_id,
            config.cost_basis,
        ),
    )
    seed = stable_segment_seed(config.openai_account_id, segment)
    filter_config = ParticleFilterConfig(
        initial_capacity_usd=_initial_capacity(segment),
    )
    adaptive = run_adaptive_range_filter(
        replay_input.model_input,
        seed=seed,
        config=filter_config,
    )
    particle = adaptive.particle

    points = []
    for observation_index, observation in enumerate(segment.observations):
        row = replay_input.observation_row_indices[observation_index]
        points.append(
            {
                "observation_id": observation.id,
                "observed_at": iso(observation.observed_at),
                "source": observation.source,
                "displayed_percent": float(observation.upstream_used_percent),
                "estimated_percent": round(
                    float(particle.total_percent_hat[row]),
                    5,
                ),
                "estimated_percent_lower": round(
                    float(particle.total_percent_lower[row]),
                    5,
                ),
                "estimated_percent_upper": round(
                    float(particle.total_percent_upper[row]),
                    5,
                ),
                "capacity_usd": round(float(particle.capacity_hat_usd[row]), 2),
                "capacity_lower_usd": round(
                    float(particle.capacity_lower_usd[row]),
                    2,
                ),
                "capacity_upper_usd": round(
                    float(particle.capacity_upper_usd[row]),
                    2,
                ),
                "range_min_usd": round(
                    float(adaptive.capacity_min_usd[row]),
                    2,
                ),
                "range_max_usd": round(
                    float(adaptive.capacity_max_usd[row]),
                    2,
                ),
                "range_stage": int(adaptive.stage[row]),
                "range_direction": (
                    adaptive.direction if adaptive.stage[row] > 0 else None
                ),
                "ess_fraction": round(float(particle.ess_fraction[row]), 6),
                "resampled": bool(particle.resampled[row]),
                "boundary_mass": {
                    "lower": round(
                        float(particle.lower_boundary_mass[row]),
                        6,
                    ),
                    "upper": round(
                        float(particle.upper_boundary_mass[row]),
                        6,
                    ),
                },
                "particles_usd": [
                    round(float(value), 2)
                    for value in particle.capacity_particle_samples_usd[row]
                ],
            }
        )

    promotions = [
        {
            "stage": index,
            "direction": promotion.direction,
            "occurred_at": iso(
                segment.started_at
                + timedelta(hours=promotion.time_hours)
            ),
            "from_range_usd": [
                promotion.from_min_usd,
                promotion.from_max_usd,
            ],
            "to_range_usd": [
                promotion.to_min_usd,
                promotion.to_max_usd,
            ],
            "boundary_mass": round(promotion.boundary_mass, 6),
            "display_residual_pp": round(
                promotion.display_residual_pp,
                6,
            ),
        }
        for index, promotion in enumerate(adaptive.promotions, start=1)
    ]
    latest = points[-1]
    return {
        "available": True,
        "message": "",
        "algorithm": ALGORITHM_VERSION,
        "seed": seed,
        "particle_count": filter_config.particles,
        "representative_particle_count": len(latest["particles_usd"]),
        "credible_mass_percent": filter_config.credible_mass * 100,
        "selected_period_id": selected_period["id"],
        "periods": [
            {
                "id": period["id"],
                "sequence": period["sequence"],
                "started_at": iso(period["started_at"]),
                "first_observed_at": iso(period["first_observed_at"]),
                "last_observed_at": iso(period["last_observed_at"]),
                "resets_at": iso(period["resets_at"]),
                "observation_count": period["observation_count"],
                "is_current": period["is_current"],
            }
            for period in periods
        ],
        "segment": {
            "started_at": iso(segment.started_at),
            "first_observed_at": iso(segment.first_observed_at),
            "resets_at": iso(segment.resets_at),
            "reason": segment.reason,
            "reason_label": REASON_LABELS.get(segment.reason, segment.reason),
            "observation_count": len(points),
        },
        "latest": {
            "observed_at": latest["observed_at"],
            "capacity_usd": latest["capacity_usd"],
            "capacity_lower_usd": latest["capacity_lower_usd"],
            "capacity_upper_usd": latest["capacity_upper_usd"],
            "range_min_usd": latest["range_min_usd"],
            "range_max_usd": latest["range_max_usd"],
            "range_stage": latest["range_stage"],
            "ess_fraction": latest["ess_fraction"],
        },
        "points": points,
        "promotions": promotions,
    }
