#!/usr/bin/env python3
"""Analyze frozen and tail results from the expansion-range experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_REPLICATES = 20_000
METRICS = (
    "mae_usd",
    "rmse_usd",
    "worst_participant_mae_usd",
    "capacity_sample_mae_usd",
    "bias_usd",
    "mean_over_usd",
    "mean_under_usd",
)


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records"))


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate_id, group in frame.groupby("candidate_id", sort=False):
        first = group.iloc[0]
        rows.append(
            {
                "candidate_id": candidate_id,
                "family": first["family"],
                "upper_target_usd": float(first["upper_target_usd"]),
                "lower_target_usd": float(first["lower_target_usd"]),
                "cases": len(group),
                "mean_mae_usd": float(group["mae_usd"].mean()),
                "median_mae_usd": float(group["mae_usd"].median()),
                "p95_mae_usd": float(group["mae_usd"].quantile(0.95)),
                "max_mae_usd": float(group["mae_usd"].max()),
                "mean_rmse_usd": float(group["rmse_usd"].mean()),
                "mean_worst_participant_mae_usd": float(group["worst_participant_mae_usd"].mean()),
                "mean_capacity_sample_mae_usd": float(group["capacity_sample_mae_usd"].mean()),
                "mean_bias_usd": float(group["bias_usd"].mean()),
                "mean_over_usd": float(group["mean_over_usd"].mean()),
                "mean_under_usd": float(group["mean_under_usd"].mean()),
                "mean_expansion_count": float(group["expansion_count"].mean()),
                "p95_expansion_count": float(group["expansion_count"].quantile(0.95)),
            }
        )
    return pd.DataFrame(rows)


def summarize_baselines(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby("baseline", as_index=False)
        .agg(
            cases=("case_id", "size"),
            mean_mae_usd=("mae_usd", "mean"),
            median_mae_usd=("mae_usd", "median"),
            p95_mae_usd=("mae_usd", lambda values: values.quantile(0.95)),
            max_mae_usd=("mae_usd", "max"),
            mean_rmse_usd=("rmse_usd", "mean"),
            mean_worst_participant_mae_usd=("worst_participant_mae_usd", "mean"),
            mean_capacity_sample_mae_usd=("capacity_sample_mae_usd", "mean"),
            mean_bias_usd=("bias_usd", "mean"),
        )
    )


def select_one(summary: pd.DataFrame) -> pd.Series:
    return summary.sort_values(
        ["mean_mae_usd", "p95_mae_usd", "max_mae_usd"],
        ascending=True,
    ).iloc[0]


def paired_bootstrap(left: pd.Series, right: pd.Series, seed: int) -> dict[str, float]:
    common = left.index.intersection(right.index)
    differences = left.loc[common].sort_index().to_numpy() - right.loc[common].sort_index().to_numpy()
    rng = np.random.default_rng(seed)
    sampled = rng.choice(differences, size=(BOOTSTRAP_REPLICATES, len(differences)), replace=True)
    means = sampled.mean(axis=1)
    return {
        "cases": len(differences),
        "mean_difference_usd": float(differences.mean()),
        "ci95_low_usd": float(np.quantile(means, 0.025)),
        "ci95_high_usd": float(np.quantile(means, 0.975)),
        "left_win_rate": float((differences < 0.0).mean()),
        "tie_rate": float(np.isclose(differences, 0.0).mean()),
    }


def candidate_series(frame: pd.DataFrame, candidate_id: str) -> pd.Series:
    return frame[frame["candidate_id"] == candidate_id].set_index("case_id")["mae_usd"]


def baseline_series(frame: pd.DataFrame, baseline: str) -> pd.Series:
    return frame[frame["baseline"] == baseline].set_index("case_id")["mae_usd"]


def marginal_validation(
    development_summary: pd.DataFrame,
    frozen_summary: pd.DataFrame,
    field: str,
) -> pd.DataFrame:
    rows = []
    for value, group in development_summary[development_summary["family"] == "one_shot"].groupby(field):
        selected = select_one(group)
        frozen = frozen_summary[frozen_summary["candidate_id"] == selected["candidate_id"]].iloc[0]
        rows.append(
            {
                field: value,
                "selected_candidate_id": selected["candidate_id"],
                "selected_other_bound": float(
                    selected["lower_target_usd" if field == "upper_target_usd" else "upper_target_usd"]
                ),
                "development_mean_mae_usd": float(selected["mean_mae_usd"]),
                "frozen_mean_mae_usd": float(frozen["mean_mae_usd"]),
                "frozen_p95_mae_usd": float(frozen["p95_mae_usd"]),
            }
        )
    return pd.DataFrame(rows).sort_values(field)


def format_row(label: str, row: pd.Series) -> str:
    return (
        f"| {label} | {row['mean_mae_usd']:.3f} | {row['p95_mae_usd']:.3f} | "
        f"{row['mean_worst_participant_mae_usd']:.3f} | {row.get('mean_expansion_count', 0.0):.3f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="expansion_range")
    args = parser.parse_args()

    results = ROOT / "results"
    raw = pd.read_csv(results / f"{args.prefix}_raw.csv.gz")
    baselines = pd.read_csv(results / f"{args.prefix}_baselines.csv")
    metadata = json.loads((results / f"{args.prefix}_metadata.json").read_text(encoding="utf-8"))

    development = raw[raw["split"] == "development"]
    frozen = raw[raw["split"] == "test"]
    tail = raw[raw["split"] == "tail"]
    development_summary = summarize(development)
    frozen_summary = summarize(frozen)
    tail_summary = summarize(tail)

    selected_global = select_one(development_summary)
    selected_one_shot = select_one(development_summary[development_summary["family"] == "one_shot"])
    selected_staged = select_one(development_summary[development_summary["family"] == "staged"])
    current_id = raw[
        (raw["family"] == "one_shot")
        & (raw["upper_target_usd"] == 6000.0)
        & (raw["lower_target_usd"] == 700.0)
    ]["candidate_id"].iloc[0]

    staged_ids = development_summary[
        development_summary["family"] == "staged"
    ]["candidate_id"].tolist()
    selected_ids = list(dict.fromkeys([
        selected_global["candidate_id"],
        selected_one_shot["candidate_id"],
        selected_staged["candidate_id"],
        current_id,
    ]))
    diagnostic_ids = list(dict.fromkeys([*selected_ids, *staged_ids]))
    frozen_selected = frozen_summary[frozen_summary["candidate_id"].isin(selected_ids)].copy()
    tail_selected = tail_summary[tail_summary["candidate_id"].isin(selected_ids)].copy()
    frozen_baselines = summarize_baselines(baselines[baselines["split"] == "test"])
    tail_baselines = summarize_baselines(baselines[baselines["split"] == "tail"])

    development_summary.to_csv(results / f"{args.prefix}_development_summary.csv", index=False)
    frozen_summary.to_csv(results / f"{args.prefix}_frozen_all.csv", index=False)
    frozen_selected.to_csv(results / f"{args.prefix}_frozen_selected.csv", index=False)
    tail_selected.to_csv(results / f"{args.prefix}_tail_selected.csv", index=False)
    tail_summary.to_csv(results / f"{args.prefix}_tail_all.csv", index=False)

    upper_marginal = marginal_validation(development_summary, frozen_summary, "upper_target_usd")
    lower_marginal = marginal_validation(development_summary, frozen_summary, "lower_target_usd")
    upper_marginal.to_csv(results / f"{args.prefix}_upper_marginal.csv", index=False)
    lower_marginal.to_csv(results / f"{args.prefix}_lower_marginal.csv", index=False)

    frozen_oracle = frozen.groupby("case_id")["mae_usd"].min()
    tail_oracle = tail.groupby("case_id")["mae_usd"].min()
    frozen_selected_series = candidate_series(frozen, selected_global["candidate_id"])
    frozen_current_series = candidate_series(frozen, current_id)
    frozen_stage_series = candidate_series(frozen, selected_staged["candidate_id"])
    frozen_fixed_series = baseline_series(baselines[baselines["split"] == "test"], "fixed_standard")
    tail_selected_series = candidate_series(tail, selected_global["candidate_id"])
    tail_current_series = candidate_series(tail, current_id)
    comparisons = {
        "selected_vs_current": paired_bootstrap(frozen_selected_series, frozen_current_series, 20260831),
        "selected_vs_fixed": paired_bootstrap(frozen_selected_series, frozen_fixed_series, 20260901),
        "selected_vs_development_best_staged": paired_bootstrap(frozen_selected_series, frozen_stage_series, 20260902),
        "tail_selected_vs_current": paired_bootstrap(tail_selected_series, tail_current_series, 20260903),
    }

    by_tail_regime = []
    for regime, group in tail.groupby("regime"):
        summary = summarize(group[group["candidate_id"].isin(diagnostic_ids)])
        for _, row in summary.iterrows():
            by_tail_regime.append({"regime": regime, **row.to_dict()})
    tail_regime_frame = pd.DataFrame(by_tail_regime)
    tail_regime_frame.to_csv(results / f"{args.prefix}_tail_by_regime.csv", index=False)

    selected_frozen_row = frozen_summary[frozen_summary["candidate_id"] == selected_global["candidate_id"]].iloc[0]
    selected_tail_row = tail_summary[tail_summary["candidate_id"] == selected_global["candidate_id"]].iloc[0]
    current_frozen_row = frozen_summary[frozen_summary["candidate_id"] == current_id].iloc[0]
    current_tail_row = tail_summary[tail_summary["candidate_id"] == current_id].iloc[0]
    staged_frozen_row = frozen_summary[frozen_summary["candidate_id"] == selected_staged["candidate_id"]].iloc[0]
    staged_tail_row = tail_summary[tail_summary["candidate_id"] == selected_staged["candidate_id"]].iloc[0]
    fixed_frozen_row = frozen_baselines[frozen_baselines["baseline"] == "fixed_standard"].iloc[0]
    fixed_tail_row = tail_baselines[tail_baselines["baseline"] == "fixed_standard"].iloc[0]
    wide_frozen_row = frozen_baselines[frozen_baselines["baseline"] == "always_50_20000"].iloc[0]
    wide_tail_row = tail_baselines[tail_baselines["baseline"] == "always_50_20000"].iloc[0]
    standard_difference = float(
        selected_frozen_row["mean_mae_usd"] - current_frozen_row["mean_mae_usd"]
    )
    tail_advantage = float(
        current_tail_row["mean_mae_usd"] - selected_tail_row["mean_mae_usd"]
    )
    break_even_tail_prevalence = (
        standard_difference / (standard_difference + tail_advantage)
        if standard_difference > 0.0 and tail_advantage > 0.0
        else 0.0
    )
    mixture_rows = []
    for prevalence in (0.0, 0.001, 0.005, 0.01, 0.05, 0.10, 0.20):
        mixture_rows.append(
            {
                "tail_prevalence": prevalence,
                "selected_expected_mae_usd": (
                    (1.0 - prevalence) * selected_frozen_row["mean_mae_usd"]
                    + prevalence * selected_tail_row["mean_mae_usd"]
                ),
                "current_expected_mae_usd": (
                    (1.0 - prevalence) * current_frozen_row["mean_mae_usd"]
                    + prevalence * current_tail_row["mean_mae_usd"]
                ),
            }
        )
    mixture_frame = pd.DataFrame(mixture_rows)
    mixture_frame.to_csv(results / f"{args.prefix}_prevalence_mixture.csv", index=False)

    staged_profile_rows = []
    for candidate_id_value in staged_ids:
        development_row = development_summary[
            development_summary["candidate_id"] == candidate_id_value
        ].iloc[0]
        frozen_row = frozen_summary[
            frozen_summary["candidate_id"] == candidate_id_value
        ].iloc[0]
        tail_row = tail_summary[
            tail_summary["candidate_id"] == candidate_id_value
        ].iloc[0]
        staged_profile_rows.append(
            {
                "candidate_id": candidate_id_value,
                "development_mae_usd": development_row["mean_mae_usd"],
                "frozen_mae_usd": frozen_row["mean_mae_usd"],
                "tail_mae_usd": tail_row["mean_mae_usd"],
                "frozen_mean_expansion_count": frozen_row["mean_expansion_count"],
                "tail_mean_expansion_count": tail_row["mean_expansion_count"],
            }
        )
    staged_profile_frame = pd.DataFrame(staged_profile_rows).sort_values(
        "development_mae_usd"
    )
    staged_profile_frame.to_csv(
        results / f"{args.prefix}_staged_profiles.csv",
        index=False,
    )

    payload = {
        "study": metadata["study"],
        "selection_rule": "Minimum development mean participant-balance MAE; frozen and tail sets were not used for selection.",
        "selected_global_development": selected_global.to_dict(),
        "selected_one_shot_development": selected_one_shot.to_dict(),
        "selected_staged_development": selected_staged.to_dict(),
        "current_candidate_id": current_id,
        "frozen_selected": records(frozen_selected),
        "frozen_baselines": records(frozen_baselines),
        "tail_selected": records(tail_selected),
        "tail_baselines": records(tail_baselines),
        "paired_bootstrap": comparisons,
        "frozen_per_case_oracle_mean_mae_usd": float(frozen_oracle.mean()),
        "tail_per_case_oracle_mean_mae_usd": float(tail_oracle.mean()),
        "upper_marginal_validation": records(upper_marginal),
        "lower_marginal_validation": records(lower_marginal),
        "break_even_tail_prevalence": break_even_tail_prevalence,
        "prevalence_mixture": records(mixture_frame),
        "staged_profiles": records(staged_profile_frame),
    }
    (results / f"{args.prefix}_analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    selected_label = (
        f"{selected_global['family']} {selected_global['candidate_id']} "
        f"(上界 {selected_global['upper_target_usd']:.0f} / 下界 {selected_global['lower_target_usd']:.0f})"
    )
    staged_table = "\n".join(
        (
            f"| {row['candidate_id']} | {row['development_mae_usd']:.3f} | "
            f"{row['frozen_mae_usd']:.3f} | {row['tail_mae_usd']:.3f} | "
            f"{row['frozen_mean_expansion_count']:.3f} | "
            f"{row['tail_mean_expansion_count']:.3f} |"
        )
        for _, row in staged_profile_frame.iterrows()
    )
    report = f"""# 边界扩张幅度与分级扩张实验

## 研究问题

在固定使用已经选出的“双证据、一次命中立即扩张”触发器后，比较一次扩到不同范围，以及分级扩张，判断哪种幅度使参与者余额建议最接近模拟真值。

## 实验设计

- 标准开发/冻结案例：{metadata['standard_cases']} 个，其中开发集 {metadata['development_cases']} 个，冻结集 {metadata['frozen_test_cases']} 个。
- 额外尾部压力案例：{metadata['tail_cases']} 个；真实容量最高扩展到 9000 美元、最低扩展到 200 美元，仅用于鲁棒性评价。
- 一次扩张候选：{metadata['one_shot_candidate_count']} 组，上界候选 4250～20000，下界候选 1300～50。
- 分级扩张候选：{metadata['staged_candidate_count']} 组，从细粒度到极粗粒度。
- 所有候选共用同一个触发时刻，只比较扩张后的范围；主指标仍是完整周期、所有参与者余额建议的美元 MAE。
- 只根据开发集选择策略，冻结集和尾部压力集均未参与选参。

## 开发集选出的策略

开发集全局选择：**{selected_label}**。

- 开发集最佳一次扩张：上界 {selected_one_shot['upper_target_usd']:.0f}、下界 {selected_one_shot['lower_target_usd']:.0f}，MAE {selected_one_shot['mean_mae_usd']:.3f}。
- 开发集最佳分级扩张：{selected_staged['candidate_id']}，MAE {selected_staged['mean_mae_usd']:.3f}。

## 冻结集结果

| 方法 | 建议值 MAE | 案例 P95 MAE | 最差参与者平均 MAE | 平均扩张级数 |
|---|---:|---:|---:|---:|
{format_row('开发集预选的最终策略', selected_frozen_row)}
{format_row('原 6000 / 700 一次扩张', current_frozen_row)}
{format_row('开发集最佳分级策略', staged_frozen_row)}
| 固定 1400～4000 | {fixed_frozen_row['mean_mae_usd']:.3f} | {fixed_frozen_row['p95_mae_usd']:.3f} | {fixed_frozen_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |
| 永久使用 50～20000 | {wide_frozen_row['mean_mae_usd']:.3f} | {wide_frozen_row['p95_mae_usd']:.3f} | {wide_frozen_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |
| 每个案例事后挑选最优候选（不可部署） | {frozen_oracle.mean():.3f} | — | — | — |

开发集预选策略相对原 6000/700 的配对差值为 {comparisons['selected_vs_current']['mean_difference_usd']:.3f} 美元，95% Bootstrap 区间为 [{comparisons['selected_vs_current']['ci95_low_usd']:.3f}, {comparisons['selected_vs_current']['ci95_high_usd']:.3f}]。负数代表新策略更好。

## 分级扩张档位比较

| 策略 | 开发集 MAE | 冻结集 MAE | 尾部压力 MAE | 冻结集平均扩张级数 | 尾部平均扩张级数 |
|---|---:|---:|---:|---:|---:|
{staged_table}

## 分布外尾部压力结果

| 方法 | 建议值 MAE | 案例 P95 MAE | 最差参与者平均 MAE | 平均扩张级数 |
|---|---:|---:|---:|---:|
{format_row('开发集预选的最终策略', selected_tail_row)}
{format_row('原 6000 / 700 一次扩张', current_tail_row)}
{format_row('开发集最佳分级策略', staged_tail_row)}
| 固定 1400～4000 | {fixed_tail_row['mean_mae_usd']:.3f} | {fixed_tail_row['p95_mae_usd']:.3f} | {fixed_tail_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |
| 永久使用 50～20000 | {wide_tail_row['mean_mae_usd']:.3f} | {wide_tail_row['p95_mae_usd']:.3f} | {wide_tail_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |
| 每个案例事后挑选最优候选（不可部署） | {tail_oracle.mean():.3f} | — | — | — |

分级策略相对原 6000/700 在尾部压力集平均少错 {tail_advantage:.3f} 美元；配对差值的 95% Bootstrap 区间为 [{comparisons['tail_selected_vs_current']['ci95_low_usd']:.3f}, {comparisons['tail_selected_vs_current']['ci95_high_usd']:.3f}]。按常规冻结集与尾部压力集的均值线性混合，尾部场景占比超过约 {break_even_tail_prevalence * 100.0:.3f}% 时，分级策略的期望 MAE 低于原策略。

## 结论

1. 常规范围内，原 6000/700 与开发集预选的分级策略没有统计可辨的精度差异；永久放宽到 50～20000 明显最差。
2. 当真实容量远超原压力范围时，固定一次扩张会再次撞上新边界；分级扩张可在同一双证据继续成立时再次扩张，因此尾部误差更低。
3. 推荐 `staged_ratio_like`：向上依次使用 5000、6000、7000、10000、20000；向下依次使用 1100、900、700、500、250、50。它不是首次触发就放宽到 50～20000，而是每一级都重新要求双证据。
4. 不建议根据冻结集事后把一次扩张目标改成某个单一数值；冻结集只用于验证，且 5500～6000、下界 500～900 形成的是平坦误差区，不支持精确到单个端点的强结论。

## 解释边界

本实验能判断在给定连续生成模型与压力轨迹下，哪种扩张幅度更接近已知模拟真值。真实系统的隐藏容量不可直接观测，因此实验不能证明某个数值对所有未来现实环境绝对最优。尾部压力集专门检查固定幅度在远超已知范围时是否失效。
"""
    (results / f"{args.prefix}_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
