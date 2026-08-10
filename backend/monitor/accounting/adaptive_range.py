"""用双证据触发器对粒子滤波容量范围执行单侧分级扩张。"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Literal

import numpy as np

from .deterministic_bounds import run_deterministic_bounds
from .dynamic_contracts import (
    DeterministicBoundsOutput,
    DynamicModelInput,
    ParticleFilterOutput,
)
from .particle_filter import (
    V_MAX,
    V_MIN,
    ParticleFilterConfig,
    run_particle_filter,
)

ExpansionDirection = Literal["upper", "lower"]

BOUNDARY_MASS_THRESHOLD = 0.10
DISPLAY_RESIDUAL_THRESHOLD_PP = 0.05
UPPER_STAGES_USD = (6000.0, 10000.0, 20000.0)
LOWER_STAGES_USD = (700.0, 250.0, 50.0)


@dataclass(frozen=True)
class RangePromotion:
    """一次由正式观测证据触发的范围升级。"""

    direction: ExpansionDirection
    row: int
    time_hours: float
    from_min_usd: float
    from_max_usd: float
    to_min_usd: float
    to_max_usd: float
    boundary_mass: float
    display_residual_pp: float


@dataclass(frozen=True)
class AdaptiveRangeOutput:
    particle: ParticleFilterOutput
    bounds: DeterministicBoundsOutput
    capacity_min_usd: np.ndarray
    capacity_max_usd: np.ndarray
    stage: np.ndarray
    direction: ExpansionDirection | None
    promotions: tuple[RangePromotion, ...]
    filter_config: ParticleFilterConfig


def _display_residuals(
    particle: ParticleFilterOutput,
    displayed_percent: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    # 与研究实现一致：各主体后验中位数之和用于检查显示单元矛盾。
    progress = particle.attributed_percent_hat.sum(axis=1)
    upper = np.maximum(
        progress - np.minimum(displayed_percent + 1.0, 100.0),
        0.0,
    )
    lower = np.maximum(
        np.maximum(displayed_percent - 1.0, 0.0) - progress,
        0.0,
    )
    return upper, lower


def _direction_evidence(
    particle: ParticleFilterOutput,
    displayed_percent: np.ndarray,
    direction: ExpansionDirection,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    upper_residual, lower_residual = _display_residuals(
        particle,
        displayed_percent,
    )
    if direction == "upper":
        mass = particle.upper_boundary_mass
        residual = upper_residual
    else:
        mass = particle.lower_boundary_mass
        residual = lower_residual
    score = np.minimum(
        mass - BOUNDARY_MASS_THRESHOLD,
        residual - DISPLAY_RESIDUAL_THRESHOLD_PP,
    )
    signal = (mass >= BOUNDARY_MASS_THRESHOLD) & (
        residual > DISPLAY_RESIDUAL_THRESHOLD_PP
    )
    return signal, score, mass, residual


def _first_signal(signal: np.ndarray, start: int = 0) -> int | None:
    rows = np.flatnonzero(signal & (np.arange(len(signal)) >= start))
    return int(rows[0]) if len(rows) else None


def _initial_promotion(
    particle: ParticleFilterOutput,
    displayed_percent: np.ndarray,
) -> tuple[ExpansionDirection | None, int | None]:
    upper_signal, upper_score, _, _ = _direction_evidence(
        particle,
        displayed_percent,
        "upper",
    )
    lower_signal, lower_score, _, _ = _direction_evidence(
        particle,
        displayed_percent,
        "lower",
    )
    upper_row = _first_signal(upper_signal)
    lower_row = _first_signal(lower_signal)
    if upper_row is None and lower_row is None:
        return None, None
    if lower_row is None or (
        upper_row is not None and upper_row < lower_row
    ):
        return "upper", upper_row
    if upper_row is None or lower_row < upper_row:
        return "lower", lower_row
    if upper_score[upper_row] >= lower_score[lower_row]:
        return "upper", upper_row
    return "lower", lower_row


def _copy_particle(output: ParticleFilterOutput) -> ParticleFilterOutput:
    return ParticleFilterOutput(
        **{
            field.name: np.array(getattr(output, field.name), copy=True)
            for field in fields(ParticleFilterOutput)
        }
    )


def _overwrite_particle(
    target: ParticleFilterOutput,
    source: ParticleFilterOutput,
    start: int,
) -> None:
    for field in fields(ParticleFilterOutput):
        target_value = getattr(target, field.name)
        source_value = getattr(source, field.name)
        target_value[start:] = source_value[start:]


def _copy_bounds(output: DeterministicBoundsOutput) -> DeterministicBoundsOutput:
    return DeterministicBoundsOutput(
        total_percent_lower=output.total_percent_lower.copy(),
        total_percent_upper=output.total_percent_upper.copy(),
        attributed_percent_lower=output.attributed_percent_lower.copy(),
        attributed_percent_upper=output.attributed_percent_upper.copy(),
        balance_lower_usd=output.balance_lower_usd.copy(),
        balance_upper_usd=output.balance_upper_usd.copy(),
        infeasible_repairs=output.infeasible_repairs,
    )


def _overwrite_bounds(
    target: DeterministicBoundsOutput,
    source: DeterministicBoundsOutput,
    start: int,
) -> DeterministicBoundsOutput:
    target.total_percent_lower[start:] = source.total_percent_lower[start:]
    target.total_percent_upper[start:] = source.total_percent_upper[start:]
    target.attributed_percent_lower[start:] = source.attributed_percent_lower[
        start:
    ]
    target.attributed_percent_upper[start:] = source.attributed_percent_upper[
        start:
    ]
    target.balance_lower_usd[start:] = source.balance_lower_usd[start:]
    target.balance_upper_usd[start:] = source.balance_upper_usd[start:]
    return replace(
        target,
        infeasible_repairs=max(
            target.infeasible_repairs,
            source.infeasible_repairs,
        ),
    )


def run_adaptive_range_filter(
    model_input: DynamicModelInput,
    *,
    seed: int,
    config: ParticleFilterConfig | None = None,
) -> AdaptiveRangeOutput:
    """从标准范围开始，按同方向持续证据逐级重放并扩张。"""

    base_config = replace(
        config or ParticleFilterConfig(),
        capacity_min_usd=V_MIN,
        capacity_max_usd=V_MAX,
    )
    base_particle = run_particle_filter(
        model_input,
        seed=seed,
        config=base_config,
    )
    base_bounds = run_deterministic_bounds(
        model_input,
        capacity_min_usd=V_MIN,
        capacity_max_usd=V_MAX,
    )
    particle = _copy_particle(base_particle)
    bounds = _copy_bounds(base_bounds)
    row_count = len(model_input.times_hours)
    active_min = np.full(row_count, V_MIN, dtype=float)
    active_max = np.full(row_count, V_MAX, dtype=float)
    active_stage = np.zeros(row_count, dtype=int)

    direction, promotion_row = _initial_promotion(
        base_particle,
        model_input.displayed_percent,
    )
    if direction is None or promotion_row is None:
        return AdaptiveRangeOutput(
            particle=particle,
            bounds=bounds,
            capacity_min_usd=active_min,
            capacity_max_usd=active_max,
            stage=active_stage,
            direction=None,
            promotions=(),
            filter_config=base_config,
        )

    targets = UPPER_STAGES_USD if direction == "upper" else LOWER_STAGES_USD
    current_particle = base_particle
    current_min = V_MIN
    current_max = V_MAX
    promotions: list[RangePromotion] = []

    for stage_number, target in enumerate(targets, start=1):
        signal, _, mass, residual = _direction_evidence(
            current_particle,
            model_input.displayed_percent,
            direction,
        )
        if not signal[promotion_row]:
            break

        next_min = current_min if direction == "upper" else target
        next_max = target if direction == "upper" else current_max
        next_config = replace(
            base_config,
            capacity_min_usd=next_min,
            capacity_max_usd=next_max,
        )
        expanded_particle = run_particle_filter(
            model_input,
            seed=seed,
            config=next_config,
        )
        expanded_bounds = run_deterministic_bounds(
            model_input,
            capacity_min_usd=next_min,
            capacity_max_usd=next_max,
        )
        _overwrite_particle(particle, expanded_particle, promotion_row)
        bounds = _overwrite_bounds(bounds, expanded_bounds, promotion_row)
        active_min[promotion_row:] = next_min
        active_max[promotion_row:] = next_max
        active_stage[promotion_row:] = stage_number
        promotions.append(
            RangePromotion(
                direction=direction,
                row=promotion_row,
                time_hours=float(model_input.times_hours[promotion_row]),
                from_min_usd=current_min,
                from_max_usd=current_max,
                to_min_usd=next_min,
                to_max_usd=next_max,
                boundary_mass=float(mass[promotion_row]),
                display_residual_pp=float(residual[promotion_row]),
            )
        )

        current_particle = expanded_particle
        current_min = next_min
        current_max = next_max
        next_signal, _, _, _ = _direction_evidence(
            current_particle,
            model_input.displayed_percent,
            direction,
        )
        next_promotion = _first_signal(next_signal, promotion_row)
        if next_promotion is None:
            break
        promotion_row = next_promotion

    return AdaptiveRangeOutput(
        particle=particle,
        bounds=bounds,
        capacity_min_usd=active_min,
        capacity_max_usd=active_max,
        stage=active_stage,
        direction=direction,
        promotions=tuple(promotions),
        filter_config=base_config,
    )
