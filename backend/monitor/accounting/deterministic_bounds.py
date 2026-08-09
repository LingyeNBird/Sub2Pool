"""仅依赖金额精度、容量硬范围和整数显示的确定性外包络。"""

from __future__ import annotations

import numpy as np

from .dynamic_contracts import DeterministicBoundsOutput, DynamicModelInput
from .particle_filter import V_MAX, V_MIN

DOLLARS_PER_PERCENT_MIN = V_MIN / 100.0
DOLLARS_PER_PERCENT_MAX = V_MAX / 100.0


def _unknown_quantizer_cell(displayed: int) -> tuple[float, float]:
    """返回 floor、四舍五入、ceil 三种固定规则的并集外包络。"""

    return max(0.0, displayed - 1.0), min(100.0, displayed + 1.0)


def run_deterministic_bounds(
    model_input: DynamicModelInput,
) -> DeterministicBoundsOutput:
    """计算任何满足硬约束的真实路径都不能越过的因果边界。"""

    model_input.validate()
    observation_count, subject_count = model_input.costs_usd.shape

    attributed_lower = np.zeros((observation_count, subject_count))
    attributed_upper = np.zeros_like(attributed_lower)
    total_lower = np.zeros(observation_count)
    total_upper = np.zeros(observation_count)

    absolute_lower = np.zeros(observation_count)
    absolute_upper = np.zeros(observation_count)
    if model_input.baseline_exact_zero:
        absolute_lower[0] = absolute_upper[0] = 0.0
    else:
        absolute_lower[0], absolute_upper[0] = _unknown_quantizer_cell(
            int(round(model_input.baseline_display_percent))
        )
    baseline_absolute_lower = absolute_lower[0]
    baseline_absolute_upper = absolute_upper[0]

    infeasible_repairs = 0
    for index in range(1, observation_count):
        cost_delta = np.maximum(
            model_input.costs_usd[index]
            - model_input.costs_usd[index - 1],
            0.0,
        )
        cost_lower = np.maximum(cost_delta - 0.01, 0.0)
        cost_upper = cost_delta + 0.01
        progress_lower = cost_lower / DOLLARS_PER_PERCENT_MAX
        progress_upper = cost_upper / DOLLARS_PER_PERCENT_MIN
        increment_lower = float(progress_lower.sum())
        increment_upper = float(progress_upper.sum())

        cell_lower, cell_upper = _unknown_quantizer_cell(
            int(round(model_input.displayed_percent[index]))
        )
        next_absolute_lower = max(
            cell_lower,
            absolute_lower[index - 1] + increment_lower,
        )
        next_absolute_upper = min(
            cell_upper,
            absolute_upper[index - 1] + increment_upper,
        )
        if next_absolute_lower > next_absolute_upper + 1e-10:
            # 输入精度与三种候选显示规则偶尔会在边界处不相容。
            # 向外修复，保证安全边界不会因为数值冲突而错误收窄。
            infeasible_repairs += 1
            next_absolute_lower = min(
                cell_lower,
                absolute_lower[index - 1] + increment_lower,
            )
            next_absolute_upper = max(
                cell_upper,
                absolute_upper[index - 1] + increment_upper,
            )
        absolute_lower[index] = next_absolute_lower
        absolute_upper[index] = next_absolute_upper

        feasible_increment_lower = max(
            increment_lower,
            next_absolute_lower - absolute_upper[index - 1],
            0.0,
        )
        feasible_increment_upper = min(
            increment_upper,
            next_absolute_upper - absolute_lower[index - 1],
        )
        if feasible_increment_lower > feasible_increment_upper:
            feasible_increment_lower = increment_lower
            feasible_increment_upper = increment_upper

        for subject in range(subject_count):
            other_upper = increment_upper - progress_upper[subject]
            other_lower = increment_lower - progress_lower[subject]
            subject_lower = max(
                progress_lower[subject],
                feasible_increment_lower - other_upper,
                0.0,
            )
            subject_upper = min(
                progress_upper[subject],
                feasible_increment_upper - other_lower,
            )
            if subject_lower > subject_upper:
                subject_lower = progress_lower[subject]
                subject_upper = progress_upper[subject]
            attributed_lower[index, subject] = (
                attributed_lower[index - 1, subject] + subject_lower
            )
            attributed_upper[index, subject] = (
                attributed_upper[index - 1, subject] + subject_upper
            )

        total_lower[index] = max(
            0.0,
            absolute_lower[index] - baseline_absolute_upper,
        )
        total_upper[index] = min(
            100.0 - baseline_absolute_lower,
            max(0.0, absolute_upper[index] - baseline_absolute_lower),
        )

    remaining_lower = np.maximum(
        model_input.rights_percent[None, :] - attributed_upper,
        0.0,
    )
    remaining_upper = np.maximum(
        model_input.rights_percent[None, :] - attributed_lower,
        0.0,
    )
    balance_lower = DOLLARS_PER_PERCENT_MIN * remaining_lower
    balance_upper = DOLLARS_PER_PERCENT_MAX * remaining_upper

    return DeterministicBoundsOutput(
        total_percent_lower=total_lower,
        total_percent_upper=total_upper,
        attributed_percent_lower=attributed_lower,
        attributed_percent_upper=attributed_upper,
        balance_lower_usd=balance_lower,
        balance_upper_usd=balance_upper,
        infeasible_repairs=infeasible_repairs,
    )
