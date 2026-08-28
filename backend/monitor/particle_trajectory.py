"""只读重放当前归属区间，生成粒子滤波可视化数据。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .accounting.adaptive_range import run_adaptive_range_filter
from .accounting.boundaries import participant_raw_costs
from .accounting.contracts import ALGORITHM_VERSION, ReplaySegment
from .accounting.model_inputs import (
    DynamicReplayInput,
    build_dynamic_replay_input,
    stable_segment_seed,
)
from .accounting.particle_filter import ParticleFilterConfig
from .fast_correction.prefix import FastCorrectionPrefix
from .models import AppSettings, MonitoredAccount, Observation
from .reporting import iso

ZERO = Decimal("0")
OBSERVED_BASELINE_REASONS = {"manual_override", "official_zero_observation"}
REASON_LABELS = {
    "official_window": "官方周期",
    "official_zero_observation": "官方 0% 起点",
    "manual_override": "管理员起点",
}


def _trajectory_periods(account_id: int) -> list[dict]:
    """同时返回推断周期边界和图表实际使用的首末观测时间。"""

    rows = (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("observed_at", "id")
        .values(
            "id",
            "observed_at",
            "attribution_started_at",
            "upstream_resets_at",
            "estimated_used_percent",
            "upstream_used_percent",
            "selected_total_cost",
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
                "estimated_used_percent": row["estimated_used_percent"],
                "displayed_used_percent": row["upstream_used_percent"],
                "used_usd": row["selected_total_cost"],
                "observation_count": 1,
            }
            continue
        period["last_observed_at"] = row["observed_at"]
        period["resets_at"] = row["upstream_resets_at"]
        period["estimated_used_percent"] = row["estimated_used_percent"]
        period["displayed_used_percent"] = row["upstream_used_percent"]
        period["used_usd"] = row["selected_total_cost"]
        period["observation_count"] += 1

    periods = sorted(
        grouped.values(),
        key=lambda period: (period["first_observed_at"], period["id"]),
    )
    if not periods:
        return periods
    current_period = max(
        periods,
        key=lambda period: (period["last_observed_at"], period["id"]),
    )
    for index, period in enumerate(periods):
        period["sequence"] = index + 1
        period["is_current"] = period["id"] == current_period["id"]
        period["ended_at"] = (
            periods[index + 1]["first_observed_at"]
            if index + 1 < len(periods)
            else period["resets_at"]
        )
    return periods


def cycle_usage_history(account_id: int) -> list[dict]:
    """Return each inferred cycle's persisted final usage summary."""

    return [
        {
            "sequence": period["sequence"],
            "started_at": iso(period["started_at"]),
            "ended_at": iso(period["ended_at"]),
            "used_percent": float(period["estimated_used_percent"]),
            "used_usd": float(period["used_usd"]),
            "is_current": period["is_current"],
        }
        for period in _trajectory_periods(account_id)
    ]


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
            first.upstream_used_percent if reason == "manual_override" else ZERO
        ),
    )


def _initial_capacity(segment: ReplaySegment) -> float | None:
    diagnostics = segment.observations[0].model_diagnostics
    value = diagnostics.get("prior_capacity_usd") if diagnostics else None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _cycle_usage_data(
    segment: ReplaySegment,
    replay_input: DynamicReplayInput,
    latest_point: dict,
    allowed_participant_ids: set[int] | None,
) -> dict:
    """Return selected-cycle costs from the same FAST-corrected replay input."""

    latest_row = replay_input.observation_row_indices[-1]
    participant_by_id = {}
    for observation in segment.observations:
        for snapshot in observation.participant_snapshots.all():
            participant_by_id[snapshot.participant_id] = snapshot.participant

    participants = []
    for participant_id, participant in sorted(
        participant_by_id.items(),
        key=lambda item: (not item[1].is_owner, item[0]),
    ):
        if (
            allowed_participant_ids is not None
            and participant_id not in allowed_participant_ids
        ):
            continue
        subject_index = replay_input.participant_subject_indices.get(
            participant_id
        )
        if subject_index is None:
            continue
        participants.append(
            {
                "participant_id": participant_id,
                "participant_name": participant.name,
                "is_owner": participant.is_owner,
                "used_usd": round(
                    float(
                        replay_input.model_input.costs_usd[
                            latest_row,
                            subject_index,
                        ]
                    ),
                    6,
                ),
            }
        )

    latest_observation = segment.observations[-1]
    return {
        "observed_at": iso(latest_observation.observed_at),
        "estimated_used_percent": latest_point["estimated_percent"],
        "displayed_used_percent": latest_point["displayed_percent"],
        "account_total_usd": round(
            float(replay_input.selected_totals[-1]),
            6,
        ),
        "participants": participants,
    }


def particle_trajectory_data(
    config: AppSettings,
    account: MonitoredAccount,
    period_id: int | None = None,
    *,
    allowed_participant_ids: set[int] | None = None,
) -> dict:
    """Read-only replay for one selected account and historical period."""

    periods = _trajectory_periods(account.external_account_id)
    if not periods:
        return {
            "available": False,
            "message": "该监控账号尚无可重放的观测记录",
        }
    selected_period = next(period for period in periods if period["is_current"])
    if period_id is not None:
        selected_period = next(
            (period for period in periods if period["id"] == period_id),
            None,
        )
        if selected_period is None:
            raise ValueError("所选历史周期不存在")

    segment = _segment_for_period(
        account.external_account_id,
        selected_period,
        config.cost_basis,
    )
    if segment is None:
        raise ValueError("所选历史周期没有可重放的观测记录")

    replay_input = build_dynamic_replay_input(
        account_id=account.external_account_id,
        segment=segment,
        config=config,
        correction_prefix=FastCorrectionPrefix(
            account.external_account_id,
            config.cost_basis,
        ),
    )
    seed = stable_segment_seed(account.external_account_id, segment)
    filter_config = ParticleFilterConfig(
        initial_capacity_usd=_initial_capacity(segment),
    )
    range_profile = account.resolved_capacity_profile
    adaptive = run_adaptive_range_filter(
        replay_input.model_input,
        seed=seed,
        config=filter_config,
        capacity_profile=range_profile,
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
                segment.started_at + timedelta(hours=promotion.time_hours)
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
        "account": {
            "id": account.id,
            "external_account_id": account.external_account_id,
            "name": account.name,
            "quota_profile": account.quota_profile,
            "detected_plan_type": account.detected_plan_type,
            "effective_quota_profile": account.effective_quota_profile,
            "capacity_min_usd_override": account.capacity_min_usd_override,
            "capacity_max_usd_override": account.capacity_max_usd_override,
            "capacity_min_usd": range_profile.capacity_min_usd,
            "capacity_max_usd": range_profile.capacity_max_usd,
        },
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
                "ended_at": iso(period["ended_at"]),
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
        "cycle_usage": _cycle_usage_data(
            segment,
            replay_input,
            latest,
            allowed_participant_ids,
        ),
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
