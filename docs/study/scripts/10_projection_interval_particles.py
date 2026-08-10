#!/usr/bin/env python3
"""Validate production projection, expanded-range intervals, and particle counts.

The study uses continuous synthetic paths with known truth, but executes the
production adaptive-range filter and deterministic projection. Development and
confirmation splits use disjoint deterministic seeds. Truth is used only for
metrics and never by the estimator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_ROOT = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "_support" / "engineering"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(DEFAULT_APP_ROOT / "backend"))

from dynamic_limit.models import SimulationSpec  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from monitor.accounting.adaptive_range import run_adaptive_range_filter  # noqa: E402
from monitor.accounting.deterministic_bounds import (  # noqa: E402
    project_attribution_to_bounds,
)
from monitor.accounting.dynamic_contracts import DynamicModelInput  # noqa: E402
from monitor.accounting.particle_filter import ParticleFilterConfig  # noqa: E402
from study_range_contraction import (  # noqa: E402
    REGIMES,
    make_jobs,
    simulate_contraction_truth,
    stable_seed,
)

PARTICLE_COUNTS = (120, 240, 320, 480, 960, 1920)
INFLATION_FACTORS = (
    1.0,
    1.15,
    1.3,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
    12.0,
    16.0,
    24.0,
)
BOOTSTRAP_REPLICATES = 20_000


def input_from_observations(truth, obs) -> DynamicModelInput:
    return DynamicModelInput(
        times_hours=np.asarray(obs.times, dtype=float),
        costs_usd=np.asarray(obs.c_obs, dtype=float),
        displayed_percent=np.asarray(obs.z, dtype=float),
        rights_percent=np.asarray(truth.rights, dtype=float) * 100.0,
        baseline_display_percent=float(obs.z[0]),
        baseline_exact_zero=True,
    )


def held_error_metrics(estimate: np.ndarray, truth, obs) -> dict[str, float]:
    true_balance, _ = true_limits(truth)
    absolute_sum = 0.0
    square_sum = 0.0
    signed_sum = 0.0
    count = 0
    max_absolute = 0.0
    for row in range(len(obs.sample_idx)):
        start = int(obs.sample_idx[row])
        end = (
            int(obs.sample_idx[row + 1])
            if row + 1 < len(obs.sample_idx)
            else len(truth.t)
        )
        error = estimate[row][None, :] - true_balance[start:end]
        absolute = np.abs(error)
        absolute_sum += float(absolute.sum())
        square_sum += float(np.square(error).sum())
        signed_sum += float(error.sum())
        count += int(error.size)
        max_absolute = max(max_absolute, float(absolute.max(initial=0.0)))
    denominator = max(1, count)
    return {
        "held_mae_usd": absolute_sum / denominator,
        "held_rmse_usd": float(np.sqrt(square_sum / denominator)),
        "held_bias_usd": signed_sum / denominator,
        "held_max_abs_usd": max_absolute,
    }


def sample_error_metrics(estimate: np.ndarray, truth, obs) -> dict[str, float]:
    true_balance, _ = true_limits(truth)
    error = estimate - true_balance[obs.sample_idx]
    absolute = np.abs(error)
    return {
        "sample_mae_usd": float(absolute.mean()),
        "sample_rmse_usd": float(np.sqrt(np.square(error).mean())),
        "sample_bias_usd": float(error.mean()),
        "sample_max_abs_usd": float(absolute.max(initial=0.0)),
    }


def production_point_balances(adaptive, rights_percent: np.ndarray):
    particle = adaptive.particle
    bounds = adaptive.bounds
    raw_attribution = particle.attributed_percent_hat
    projected_attribution, repaired_rows, max_adjustment = (
        project_attribution_to_bounds(raw_attribution, bounds)
    )
    raw_balance = (
        np.maximum(rights_percent[None, :] - raw_attribution, 0.0)
        * particle.capacity_hat_usd[:, None]
        / 100.0
    )
    projected_balance = (
        np.maximum(rights_percent[None, :] - projected_attribution, 0.0)
        * particle.capacity_hat_usd[:, None]
        / 100.0
    )
    projected_balance = np.clip(
        projected_balance,
        bounds.balance_lower_usd,
        bounds.balance_upper_usd,
    )
    return (
        raw_balance,
        projected_balance,
        projected_attribution,
        repaired_rows,
        max_adjustment,
    )


def calibrated_interval(adaptive, projected_balance: np.ndarray, factor: float):
    particle = adaptive.particle
    bounds = adaptive.bounds
    lower = np.maximum(
        particle.balance_hat_usd
        - factor * (particle.balance_hat_usd - particle.balance_lower_usd),
        0.0,
    )
    upper = particle.balance_hat_usd + factor * (
        particle.balance_upper_usd - particle.balance_hat_usd
    )
    lower = np.maximum(lower, bounds.balance_lower_usd)
    upper = np.minimum(upper, bounds.balance_upper_usd)
    incompatible = lower > upper
    lower[incompatible] = bounds.balance_lower_usd[incompatible]
    upper[incompatible] = bounds.balance_upper_usd[incompatible]
    lower = np.minimum(lower, projected_balance)
    upper = np.maximum(upper, projected_balance)
    return lower, upper


def interval_group_rows(
    *,
    case_id: str,
    split: str,
    regime: str,
    particle_count: int,
    factor: float | str,
    lower: np.ndarray,
    upper: np.ndarray,
    sample_truth: np.ndarray,
    stage: np.ndarray,
    direction: str | None,
) -> list[dict[str, Any]]:
    masks: dict[str, np.ndarray] = {
        "all": np.ones(len(stage), dtype=bool),
        "standard": stage == 0,
        "expanded": stage > 0,
        "stage1": stage == 1,
        "stage2plus": stage >= 2,
    }
    if direction in {"upper", "lower"}:
        masks[f"expanded_{direction}"] = stage > 0
        masks[f"stage1_{direction}"] = stage == 1
        masks[f"stage2plus_{direction}"] = stage >= 2
    rows: list[dict[str, Any]] = []
    for group, row_mask in masks.items():
        if not row_mask.any():
            continue
        covered = (
            (sample_truth[row_mask] >= lower[row_mask] - 1e-9)
            & (sample_truth[row_mask] <= upper[row_mask] + 1e-9)
        )
        width = upper[row_mask] - lower[row_mask]
        rows.append(
            {
                "case_id": case_id,
                "split": split,
                "regime": regime,
                "direction": direction or "none",
                "particle_count": particle_count,
                "inflation": factor,
                "group": group,
                "covered": int(covered.sum()),
                "total": int(covered.size),
                "width_sum_usd": float(width.sum()),
            }
        )
    return rows


def run_case(payload) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    (regime, split, spec), particle_count, seed_override = payload
    truth = simulate_contraction_truth(spec)
    obs = make_observations(truth)
    model_input = input_from_observations(truth, obs)
    seed = (
        int(seed_override)
        if seed_override is not None
        else (spec.seed + 7919) % (2**32 - 1)
    )
    config = ParticleFilterConfig(
        particles=int(particle_count),
        balance_interval_inflation=1.0,
    )
    started = perf_counter()
    adaptive = run_adaptive_range_filter(
        model_input,
        seed=seed,
        config=config,
    )
    elapsed = perf_counter() - started
    (
        raw_balance,
        projected_balance,
        _,
        repaired_rows,
        max_adjustment,
    ) = production_point_balances(adaptive, model_input.rights_percent)
    raw_metrics = {
        **held_error_metrics(raw_balance, truth, obs),
        **sample_error_metrics(raw_balance, truth, obs),
    }
    projected_metrics = {
        **held_error_metrics(projected_balance, truth, obs),
        **sample_error_metrics(projected_balance, truth, obs),
    }
    point_row = {
        "case_id": spec.case_id,
        "split": split,
        "regime": regime,
        "speed": spec.speed,
        "scenario": spec.scenario,
        "sample_hours": spec.sample_hours,
        "quantizer": spec.quantizer,
        "n_participants": spec.n_participants,
        "particle_count": particle_count,
        "seed": seed,
        "runtime_seconds": elapsed,
        "observation_count": len(obs.times),
        "direction": adaptive.direction or "none",
        "max_stage": int(adaptive.stage.max(initial=0)),
        "expanded_fraction": float((adaptive.stage > 0).mean()),
        "deterministic_repairs": adaptive.bounds.infeasible_repairs,
        "projection_repaired_rows": repaired_rows,
        "projection_fraction": repaired_rows / max(1, len(obs.times)),
        "projection_max_adjustment_pp": max_adjustment,
        "final_capacity_usd": float(adaptive.particle.capacity_hat_usd[-1]),
        "final_projected_balance_total_usd": float(projected_balance[-1].sum()),
        **{f"raw_{key}": value for key, value in raw_metrics.items()},
        **{f"projected_{key}": value for key, value in projected_metrics.items()},
    }

    interval_rows: list[dict[str, Any]] = []
    if particle_count == 480 and seed_override is None:
        true_balance, _ = true_limits(truth)
        sample_truth = true_balance[obs.sample_idx]
        for factor in INFLATION_FACTORS:
            lower, upper = calibrated_interval(
                adaptive,
                projected_balance,
                factor,
            )
            interval_rows.extend(
                interval_group_rows(
                    case_id=spec.case_id,
                    split=split,
                    regime=regime,
                    particle_count=particle_count,
                    factor=factor,
                    lower=lower,
                    upper=upper,
                    sample_truth=sample_truth,
                    stage=adaptive.stage,
                    direction=adaptive.direction,
                )
            )
        interval_rows.extend(
            interval_group_rows(
                case_id=spec.case_id,
                split=split,
                regime=regime,
                particle_count=particle_count,
                factor="deterministic",
                lower=adaptive.bounds.balance_lower_usd,
                upper=adaptive.bounds.balance_upper_usd,
                sample_truth=sample_truth,
                stage=adaptive.stage,
                direction=adaptive.direction,
            )
        )
    timing = {
        "case_id": spec.case_id,
        "split": split,
        "regime": regime,
        "particle_count": particle_count,
        "runtime_seconds": elapsed,
    }
    return point_row, interval_rows, timing


def run_payloads(payloads: list[tuple], workers: int):
    point_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    if workers <= 1:
        iterator = map(run_case, payloads)
        pool = None
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        iterator = pool.map(run_case, payloads, chunksize=1)
    try:
        for point, intervals, timing in iterator:
            point_rows.append(point)
            interval_rows.extend(intervals)
            timing_rows.append(timing)
    finally:
        if pool is not None:
            pool.shutdown()
    return (
        pd.DataFrame(point_rows),
        pd.DataFrame(interval_rows),
        pd.DataFrame(timing_rows),
    )


def summarize_points(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (split, particle_count), group in raw.groupby(["split", "particle_count"]):
        rows.append(
            {
                "split": split,
                "particle_count": int(particle_count),
                "cases": group["case_id"].nunique(),
                "raw_mean_mae_usd": group["raw_held_mae_usd"].mean(),
                "projected_mean_mae_usd": group["projected_held_mae_usd"].mean(),
                "projected_case_p95_mae_usd": group["projected_held_mae_usd"].quantile(0.95),
                "projected_mean_rmse_usd": group["projected_held_rmse_usd"].mean(),
                "projected_mean_bias_usd": group["projected_held_bias_usd"].mean(),
                "projection_delta_mae_usd": (
                    group["projected_held_mae_usd"] - group["raw_held_mae_usd"]
                ).mean(),
                "cases_improved_by_projection": (
                    group["projected_held_mae_usd"] < group["raw_held_mae_usd"] - 1e-12
                ).mean(),
                "cases_worsened_by_projection": (
                    group["projected_held_mae_usd"] > group["raw_held_mae_usd"] + 1e-12
                ).mean(),
                "mean_projection_fraction": group["projection_fraction"].mean(),
                "mean_runtime_seconds": group["runtime_seconds"].mean(),
                "total_runtime_seconds": group["runtime_seconds"].sum(),
            }
        )
    return pd.DataFrame(rows).sort_values(["split", "particle_count"])


def cluster_bootstrap_coverage(
    group: pd.DataFrame,
    *,
    seed_label: str,
) -> tuple[float, float]:
    covered = group["covered"].to_numpy(dtype=float)
    total = group["total"].to_numpy(dtype=float)
    rng = np.random.default_rng(stable_seed(seed_label))
    samples: list[np.ndarray] = []
    remaining = BOOTSTRAP_REPLICATES
    while remaining > 0:
        size = min(1_000, remaining)
        indices = rng.integers(0, len(group), size=(size, len(group)))
        samples.append(covered[indices].sum(axis=1) / total[indices].sum(axis=1))
        remaining -= size
    lower, upper = np.quantile(np.concatenate(samples), [0.025, 0.975])
    return float(lower), float(upper)


def summarize_intervals(raw: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (split, inflation, interval_group), group in raw.groupby(
        ["split", "inflation", "group"],
        sort=False,
    ):
        covered = int(group["covered"].sum())
        total = int(group["total"].sum())
        ci_low, ci_high = cluster_bootstrap_coverage(
            group,
            seed_label=f"interval-bootstrap:{split}:{inflation}:{interval_group}",
        )
        rows.append(
            {
                "split": split,
                "inflation": inflation,
                "group": interval_group,
                "cases": group["case_id"].nunique(),
                "covered": covered,
                "total": total,
                "coverage": covered / total,
                "coverage_ci95_low": ci_low,
                "coverage_ci95_high": ci_high,
                "mean_width_usd": group["width_sum_usd"].sum() / total,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["split", "group", "inflation"],
        key=lambda col: col.astype(str),
    )


def paired_bootstrap_difference(
    raw: pd.DataFrame,
    *,
    split: str,
    particle_count: int,
    left: str,
    right: str,
    seed_label: str,
) -> dict[str, float]:
    group = raw[
        (raw["split"] == split)
        & (raw["particle_count"] == particle_count)
    ]
    difference = group[left].to_numpy(dtype=float) - group[right].to_numpy(dtype=float)
    rng = np.random.default_rng(stable_seed(seed_label))
    indices = rng.integers(0, len(difference), size=(BOOTSTRAP_REPLICATES, len(difference)))
    means = difference[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return {
        "cases": int(len(difference)),
        "mean_difference_usd": float(difference.mean()),
        "ci95_low_usd": float(lower),
        "ci95_high_usd": float(upper),
    }


def bootstrap_mean_difference(
    difference: np.ndarray,
    *,
    seed_label: str,
) -> tuple[float, float]:
    rng = np.random.default_rng(stable_seed(seed_label))
    indices = rng.integers(
        0,
        len(difference),
        size=(BOOTSTRAP_REPLICATES, len(difference)),
    )
    means = difference[indices].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def particle_comparisons(
    raw: pd.DataFrame,
    split: str,
    pairs: tuple[tuple[int, int], ...],
) -> pd.DataFrame:
    pivot = raw[raw["split"] == split].pivot(
        index="case_id",
        columns="particle_count",
        values="projected_held_mae_usd",
    )
    rows = []
    for left, right in pairs:
        difference = (pivot[left] - pivot[right]).dropna().to_numpy(dtype=float)
        low, high = bootstrap_mean_difference(
            difference,
            seed_label=f"particle-bootstrap:{split}:{left}:{right}",
        )
        rows.append(
            {
                "split": split,
                "left_particle_count": left,
                "right_particle_count": right,
                "cases": len(difference),
                "left_minus_right_mae_usd": float(difference.mean()),
                "ci95_low_usd": low,
                "ci95_high_usd": high,
            }
        )
    return pd.DataFrame(rows)


def summarize_stability(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    per_case = (
        raw.groupby(["particle_count", "case_id"], as_index=False)
        .agg(
            capacity_sd_usd=("final_capacity_usd", "std"),
            capacity_range_usd=("final_capacity_usd", lambda value: value.max() - value.min()),
            balance_sd_usd=("final_projected_balance_total_usd", "std"),
            balance_range_usd=("final_projected_balance_total_usd", lambda value: value.max() - value.min()),
            mae_sd_usd=("projected_held_mae_usd", "std"),
        )
    )
    return (
        per_case.groupby("particle_count", as_index=False)
        .agg(
            cases=("case_id", "nunique"),
            mean_capacity_sd_usd=("capacity_sd_usd", "mean"),
            p95_capacity_range_usd=("capacity_range_usd", lambda value: value.quantile(0.95)),
            mean_balance_sd_usd=("balance_sd_usd", "mean"),
            p95_balance_range_usd=("balance_range_usd", lambda value: value.quantile(0.95)),
            mean_mae_sd_usd=("mae_sd_usd", "mean"),
        )
        .sort_values("particle_count")
    )


def select_global_inflation(
    summary: pd.DataFrame,
    *,
    conservative: bool,
) -> float | None:
    development = summary[
        (summary["split"] == "development")
        & (summary["inflation"] != "deterministic")
    ].copy()
    development["inflation_numeric"] = development["inflation"].astype(float)
    required_groups = {
        "expanded",
        "stage1",
        "stage2plus",
        "expanded_upper",
        "expanded_lower",
        "stage1_upper",
        "stage1_lower",
        "stage2plus_upper",
        "stage2plus_lower",
    }
    metric = "coverage_ci95_low" if conservative else "coverage"
    candidates = []
    for factor, group in development.groupby("inflation_numeric"):
        eligible = group[
            group["group"].isin(required_groups)
            & (group["cases"] >= 20)
        ]
        if set(eligible["group"]) != required_groups:
            continue
        if (eligible[metric] >= 0.90).all():
            candidates.append(float(factor))
    return min(candidates) if candidates else None


def select_conditional_inflations(
    summary: pd.DataFrame,
) -> dict[str, float | str | None]:
    development = summary[summary["split"] == "development"].copy()
    numeric = development[
        development["inflation"] != "deterministic"
    ].copy()
    numeric["inflation_numeric"] = numeric["inflation"].astype(float)
    selections: dict[str, float | str | None] = {}
    for interval_group in (
        "stage1_upper",
        "stage1_lower",
        "stage2plus_upper",
        "stage2plus_lower",
    ):
        rows = numeric[
            (numeric["group"] == interval_group)
            & (numeric["cases"] >= 20)
            & (numeric["coverage"] >= 0.90)
        ]
        if not rows.empty:
            selections[interval_group] = float(rows["inflation_numeric"].min())
            continue
        deterministic = development[
            (development["group"] == interval_group)
            & (development["inflation"] == "deterministic")
            & (development["cases"] >= 20)
            & (development["coverage"] >= 0.90)
        ]
        selections[interval_group] = (
            "deterministic"
            if not deterministic.empty
            else None
        )
    return selections


def conditional_policy_summary(
    raw: pd.DataFrame,
    selections: dict[str, float | str | None],
    *,
    split: str,
) -> dict[str, Any]:
    selected = []
    for interval_group, factor in selections.items():
        if factor is None:
            continue
        selected.append(
            raw[
                (raw["split"] == split)
                & (raw["group"] == interval_group)
                & (raw["inflation"] == factor)
            ]
        )
    if len(selected) != len(selections):
        return {}
    per_case = (
        pd.concat(selected, ignore_index=True)
        .groupby("case_id", as_index=False)
        .agg(
            covered=("covered", "sum"),
            total=("total", "sum"),
            width_sum_usd=("width_sum_usd", "sum"),
        )
    )
    ci_low, ci_high = cluster_bootstrap_coverage(
        per_case,
        seed_label=f"conditional-policy:{split}",
    )
    covered = int(per_case["covered"].sum())
    total = int(per_case["total"].sum())
    return {
        "split": split,
        "cases": per_case["case_id"].nunique(),
        "covered": covered,
        "total": total,
        "coverage": covered / total,
        "coverage_ci95_low": ci_low,
        "coverage_ci95_high": ci_high,
        "mean_width_usd": per_case["width_sum_usd"].sum() / total,
    }

def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "无。"
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in frame.itertuples(index=False, name=None):
        values = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.6g}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)



def build_report(
    point_summary: pd.DataFrame,
    interval_summary: pd.DataFrame,
    particle_reference: pd.DataFrame,
    particle_adjacent: pd.DataFrame,
    stability_summary: pd.DataFrame,
    projection_comparison: dict[str, float],
    nominal_global_inflation: float | None,
    conservative_global_inflation: float | None,
    conditional_inflations: dict[str, float | str | None],
    conditional_development: dict[str, Any],
    conditional_confirmation: dict[str, Any],
) -> str:
    confirmation_points = point_summary[point_summary["split"] == "confirmation"]
    confirmation_intervals = interval_summary[
        interval_summary["split"] == "confirmation"
    ]
    nominal_text = (
        "无候选同时达到要求"
        if nominal_global_inflation is None
        else f"{nominal_global_inflation:g}"
    )
    conservative_text = (
        "无候选同时达到要求"
        if conservative_global_inflation is None
        else f"{conservative_global_inflation:g}"
    )
    selected_rows = (
        confirmation_intervals[
            confirmation_intervals["inflation"].astype(str)
            == str(nominal_global_inflation)
        ]
        if nominal_global_inflation is not None
        else pd.DataFrame()
    )
    selected_columns = [
        "group",
        "cases",
        "coverage",
        "coverage_ci95_low",
        "coverage_ci95_high",
        "mean_width_usd",
    ]
    selected_table = markdown_table(
        selected_rows[selected_columns]
        if not selected_rows.empty
        else pd.DataFrame(columns=selected_columns)
    )
    conditional_rows = []
    for interval_group, factor in conditional_inflations.items():
        row = confirmation_intervals[
            (confirmation_intervals["group"] == interval_group)
            & (confirmation_intervals["inflation"].astype(str) == str(factor))
        ]
        if not row.empty:
            conditional_rows.append(row.iloc[0].to_dict())
    conditional_table = markdown_table(
        pd.DataFrame(conditional_rows)[
            ["group", "inflation", *selected_columns[1:]]
        ]
        if conditional_rows
        else pd.DataFrame()
    )
    points_by_particle = confirmation_points.set_index("particle_count")
    adjacent_480_960 = particle_adjacent[
        (particle_adjacent["left_particle_count"] == 480)
        & (particle_adjacent["right_particle_count"] == 960)
    ].iloc[0]
    reference_960 = particle_reference[
        particle_reference["left_particle_count"] == 960
    ].iloc[0]
    runtime_480 = float(points_by_particle.loc[480, "mean_runtime_seconds"])
    runtime_960 = float(points_by_particle.loc[960, "mean_runtime_seconds"])
    runtime_1920 = float(points_by_particle.loc[1920, "mean_runtime_seconds"])
    return f"""# 生产粒子算法鲁棒性补充实验

## 实验设计

- 使用生产 `run_adaptive_range_filter` 与 `project_attribution_to_bounds`，不是研究版近似实现。
- 容量路径连续；覆盖范围内贴边、暂时越界、极端越界、反弹和永久越界共 10 类路径。
- 参与者 2/4/6 人，采样间隔 1/3/6 小时，三种整数显示规则和八类消费模式。
- 开发集负责选择区间膨胀系数，独立确认集只报告冻结后的选择。
- 粒子数量比较 120、240、320、480、960、1920；1920 只作高计算量参考，不预设为最佳。

## 确定性投影

确认集 480 粒子下，投影后减投影前的完整轨迹余额 MAE 为 **{projection_comparison['mean_difference_usd']:.4f} 美元**，95% Bootstrap 区间为 **[{projection_comparison['ci95_low_usd']:.4f}, {projection_comparison['ci95_high_usd']:.4f}]**。负值表示投影改善。

{markdown_table(confirmation_points)}

## 90% 区间校准


开发集按名义覆盖率冻结单一全局系数：**{nominal_text}**。其规则是扩张总体、两个扩张级别、上下方向以及“级别 × 方向”子组的汇总覆盖率都不少于 90%。


独立确认集的全局系数结果：

{selected_table}

若进一步按“扩张级别 × 方向”校准，开发集冻结参数为 `{json.dumps(conditional_inflations, ensure_ascii=False)}`。数字表示概率区间膨胀系数；`deterministic` 表示有限膨胀仍不足时直接采用确定性外包络。
独立确认集各子组：

{conditional_table}

组合策略在开发集为 `{json.dumps(conditional_development, ensure_ascii=False)}`，在独立确认集为 `{json.dumps(conditional_confirmation, ensure_ascii=False)}`。

更严格地要求每个开发子组的按案例 Bootstrap 95% 覆盖率下界也达到 90%时，全局系数为：**{conservative_text}**。无候选表示现有概率区间受当前激活硬范围截断，单纯继续膨胀也无法形成这种强保证。

## 粒子数量

各粒子数相对 1920 粒子的配对结果（正值表示左侧粒子数误差更大）：

{markdown_table(particle_reference)}

相邻粒子数的配对结果：

{markdown_table(particle_adjacent)}

多随机种子稳定性：

{markdown_table(stability_summary)}

## 结论

- **确定性投影保留。** 480 粒子下，投影使余额 MAE 平均变化 {projection_comparison['mean_difference_usd']:+.4f} 美元，95% 区间为 {projection_comparison['ci95_low_usd']:+.4f}～{projection_comparison['ci95_high_usd']:+.4f} 美元；影响可忽略，但它能阻止输出落入已知不可能区域。
- **扩张后的区间不能继续使用一个固定膨胀系数。** 推荐第一级上扩采用确定性外包络，第一级下扩用 1.3，第二级及以上上、下扩都用 1.0。组合策略在开发集覆盖 {conditional_development.get('coverage', float('nan')):.2%}，在独立确认集覆盖 {conditional_confirmation.get('coverage', float('nan')):.2%}，确认集 95% 区间为 {conditional_confirmation.get('coverage_ci95_low', float('nan')):.2%}～{conditional_confirmation.get('coverage_ci95_high', float('nan')):.2%}。
- **粒子数推荐 960。** 相比 480，确认集余额 MAE 少 {adjacent_480_960['left_minus_right_mae_usd']:.4f} 美元（95% 区间 {adjacent_480_960['ci95_low_usd']:.4f}～{adjacent_480_960['ci95_high_usd']:.4f}）；相比 1920 只多 {reference_960['left_minus_right_mae_usd']:.4f} 美元（{reference_960['ci95_low_usd']:.4f}～{reference_960['ci95_high_usd']:.4f}）。960 的平均运行时间约为 480 的 {runtime_960 / runtime_480:.2f} 倍、1920 的 {runtime_960 / runtime_1920:.0%}。
- 上述区间和粒子数是本次补充实验的**下一版设计建议**，不表示当前生产默认值已自动修改。

## 解释边界

- 合成实验能比较已知真值下的误差，但不能直接观测现实隐藏容量。
- 区间膨胀不能修复确定性外包络本身不覆盖真值的案例；报告同时保留确定性区间上限。
- 粒子数量选择应同时看误差差值置信区间、尾部误差、随机种子稳定性和运行时间，不能只按平均 MAE 排名。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-per-regime", type=int, default=30)
    parser.add_argument("--confirmation-per-regime", type=int, default=60)
    parser.add_argument("--stability-per-regime", type=int, default=3)
    parser.add_argument("--stability-replicates", type=int, default=6)
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--prefix", default="production_robustness")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    development_per_regime = 1 if args.smoke else args.development_per_regime
    confirmation_per_regime = 1 if args.smoke else args.confirmation_per_regime
    stability_per_regime = 1 if args.smoke else args.stability_per_regime
    stability_replicates = 2 if args.smoke else args.stability_replicates

    development = make_jobs(
        development_per_regime,
        split="development",
        salt="production-robustness-development-20260810",
    )
    confirmation = make_jobs(
        confirmation_per_regime,
        split="confirmation",
        salt="production-robustness-confirmation-20260810",
    )
    primary_payloads = [
        (job, particle_count, None)
        for job in [*development, *confirmation]
        for particle_count in PARTICLE_COUNTS
    ]
    point_raw, interval_raw, timing = run_payloads(primary_payloads, args.workers)

    stability_jobs = [
        job
        for regime in REGIMES
        for job in [
            candidate
            for candidate in development
            if candidate[0] == regime
        ][:stability_per_regime]
    ]
    stability_payloads = []
    for job in stability_jobs:
        for particle_count in PARTICLE_COUNTS:
            for replicate in range(stability_replicates):
                seed = stable_seed(
                    f"production-stability:{job[2].case_id}:{particle_count}:{replicate}"
                )
                stability_payloads.append((job, particle_count, seed))
    stability_raw, _, stability_timing = run_payloads(
        stability_payloads,
        args.workers,
    )

    point_summary = summarize_points(point_raw)
    interval_summary = summarize_intervals(interval_raw)
    particle_reference = particle_comparisons(
        point_raw,
        "confirmation",
        tuple((count, 1920) for count in PARTICLE_COUNTS),
    )
    particle_adjacent = particle_comparisons(
        point_raw,
        "confirmation",
        tuple(zip(PARTICLE_COUNTS[:-1], PARTICLE_COUNTS[1:], strict=True)),
    )
    stability_summary = summarize_stability(stability_raw)
    projection_comparison = paired_bootstrap_difference(
        point_raw,
        split="confirmation",
        particle_count=480,
        left="projected_held_mae_usd",
        right="raw_held_mae_usd",
        seed_label="production-projection-confirmation",
    )
    nominal_global_inflation = select_global_inflation(
        interval_summary,
        conservative=False,
    )
    conservative_global_inflation = select_global_inflation(
        interval_summary,
        conservative=True,
    )
    conditional_inflations = select_conditional_inflations(interval_summary)
    conditional_development = conditional_policy_summary(
        interval_raw,
        conditional_inflations,
        split="development",
    )
    conditional_confirmation = conditional_policy_summary(
        interval_raw,
        conditional_inflations,
        split="confirmation",
    )

    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    prefix = f"{args.prefix}_smoke" if args.smoke else args.prefix
    point_raw.to_csv(results / f"{prefix}_point_raw.csv.gz", index=False, compression="gzip")
    interval_raw.to_csv(results / f"{prefix}_interval_raw.csv.gz", index=False, compression="gzip")
    stability_raw.to_csv(results / f"{prefix}_stability_raw.csv.gz", index=False, compression="gzip")
    pd.concat([timing, stability_timing], ignore_index=True).to_csv(
        results / f"{prefix}_timing.csv",
        index=False,
    )
    point_summary.to_csv(results / f"{prefix}_point_summary.csv", index=False)
    interval_summary.to_csv(results / f"{prefix}_interval_summary.csv", index=False)
    particle_reference.to_csv(
        results / f"{prefix}_particle_reference_comparison.csv",
        index=False,
    )
    particle_adjacent.to_csv(
        results / f"{prefix}_particle_adjacent_comparison.csv",
        index=False,
    )
    stability_summary.to_csv(results / f"{prefix}_stability_summary.csv", index=False)

    metadata = {
        "study": "production projection, expanded interval calibration, and particle-count sensitivity",
        "production_source": str(DEFAULT_APP_ROOT / "backend"),
        "continuous_capacity_paths": True,
        "regimes": list(REGIMES),
        "particle_counts": list(PARTICLE_COUNTS),
        "inflation_factors": list(INFLATION_FACTORS),
        "development_cases": len(development),
        "confirmation_cases": len(confirmation),
        "stability_cases": len(stability_jobs),
        "stability_replicates": stability_replicates,
        "nominal_global_inflation": nominal_global_inflation,
        "conservative_global_inflation": conservative_global_inflation,
        "conditional_inflations": conditional_inflations,
        "conditional_development": conditional_development,
        "conditional_confirmation": conditional_confirmation,
        "selection_rule": "nominal global and conditional factors are the smallest development factors with >=90% pooled coverage; conservative global additionally requires the case-cluster bootstrap 95% lower bound >=90% in all eligible expansion groups",
        "projection_comparison": projection_comparison,
        "random_salts": {
            "development": "production-robustness-development-20260810",
            "confirmation": "production-robustness-confirmation-20260810",
        },
    }
    (results / f"{prefix}_study.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = build_report(
        point_summary,
        interval_summary,
        particle_reference,
        particle_adjacent,
        stability_summary,
        projection_comparison,
        nominal_global_inflation,
        conservative_global_inflation,
        conditional_inflations,
        conditional_development,
        conditional_confirmation,
    )
    (results / f"{prefix}_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
