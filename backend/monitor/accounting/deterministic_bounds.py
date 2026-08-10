"""仅依赖金额精度、容量硬范围和整数显示的确定性外包络。"""

from __future__ import annotations

import numpy as np

from .dynamic_contracts import DeterministicBoundsOutput, DynamicModelInput
from .particle_filter import V_MAX, V_MIN


def _unknown_quantizer_cell(displayed: int) -> tuple[float, float]:
    """返回 floor、四舍五入、ceil 三种固定规则的并集外包络。"""

    return max(0.0, displayed - 1.0), min(100.0, displayed + 1.0)


def run_deterministic_bounds(
    model_input: DynamicModelInput,
    *,
    capacity_min_usd: float = V_MIN,
    capacity_max_usd: float = V_MAX,
) -> DeterministicBoundsOutput:
    """计算任何满足硬约束的真实路径都不能越过的因果边界。"""

    model_input.validate()
    if capacity_min_usd <= 0:
        raise ValueError("容量下界必须大于 0")
    if capacity_min_usd >= capacity_max_usd:
        raise ValueError("容量下界必须小于容量上界")
    dollars_per_percent_min = capacity_min_usd / 100.0
    dollars_per_percent_max = capacity_max_usd / 100.0
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
        progress_lower = cost_lower / dollars_per_percent_max
        progress_upper = cost_upper / dollars_per_percent_min
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
    balance_lower = dollars_per_percent_min * remaining_lower
    balance_upper = dollars_per_percent_max * remaining_upper

    return DeterministicBoundsOutput(
        total_percent_lower=total_lower,
        total_percent_upper=total_upper,
        attributed_percent_lower=attributed_lower,
        attributed_percent_upper=attributed_upper,
        balance_lower_usd=balance_lower,
        balance_upper_usd=balance_upper,
        infeasible_repairs=infeasible_repairs,
    )

def project_attribution_to_bounds(
    point_estimate: np.ndarray,
    bounds: DeterministicBoundsOutput,
) -> tuple[np.ndarray, int, float]:
    """把概率点估计投影到逐主体与总进度的确定性可行外包络。"""

    if point_estimate.shape != bounds.attributed_percent_lower.shape:
        raise ValueError("归属点估计与确定性边界形状不一致")
    projected = np.empty_like(point_estimate, dtype=float)
    repaired_rows = 0
    max_adjustment = 0.0
    for row in range(len(point_estimate)):
        lower = bounds.attributed_percent_lower[row]
        if row:
            # 归属是周期累计量；逐行投影也必须保留时间单调性。
            lower = np.maximum(lower, projected[row - 1])
        upper = bounds.attributed_percent_upper[row]
        estimate = point_estimate[row]
        feasible_total_lower = max(
            float(bounds.total_percent_lower[row]),
            float(lower.sum()),
        )
        feasible_total_upper = min(
            float(bounds.total_percent_upper[row]),
            float(upper.sum()),
        )
        if feasible_total_lower > feasible_total_upper:
            feasible_total_lower = float(lower.sum())
            feasible_total_upper = float(upper.sum())
        target_total = float(
            np.clip(
                estimate.sum(),
                feasible_total_lower,
                feasible_total_upper,
            )
        )

        # 欧氏投影到 box 与固定总和的交集：
        # x_i = clip(estimate_i + λ, lower_i, upper_i)。
        lambda_lower = float(np.min(lower - estimate)) - 1.0
        lambda_upper = float(np.max(upper - estimate)) + 1.0
        for _ in range(80):
            midpoint = 0.5 * (lambda_lower + lambda_upper)
            candidate = np.clip(estimate + midpoint, lower, upper)
            if float(candidate.sum()) < target_total:
                lambda_lower = midpoint
            else:
                lambda_upper = midpoint
        candidate = np.clip(
            estimate + 0.5 * (lambda_lower + lambda_upper),
            lower,
            upper,
        )
        projected[row] = candidate
        row_adjustment = float(np.max(np.abs(candidate - estimate)))
        if row_adjustment > 1e-8:
            repaired_rows += 1
            max_adjustment = max(max_adjustment, row_adjustment)
    return projected, repaired_rows, max_adjustment
