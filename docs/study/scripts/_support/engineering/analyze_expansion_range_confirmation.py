#!/usr/bin/env python3
"""Confirm the finalist expansion policies on an untouched replication."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "_support" / "engineering"))

from analyze_expansion_ranges import (  # noqa: E402
    baseline_series,
    candidate_series,
    paired_bootstrap,
    records,
    summarize,
    summarize_baselines,
)

FINALISTS = (
    "staged_ratio_like",
    "staged_coarse",
    "staged_very_coarse",
)


def table_row(label: str, row: pd.Series) -> str:
    return (
        f"| {label} | {row['mean_mae_usd']:.3f} | {row['p95_mae_usd']:.3f} | "
        f"{row['mean_worst_participant_mae_usd']:.3f} | "
        f"{row.get('mean_expansion_count', 0.0):.3f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="expansion_range_confirmation")
    args = parser.parse_args()

    results = ROOT / "results"
    raw = pd.read_csv(results / f"{args.prefix}_raw.csv.gz")
    baselines = pd.read_csv(results / f"{args.prefix}_baselines.csv")
    metadata = json.loads(
        (results / f"{args.prefix}_metadata.json").read_text(encoding="utf-8")
    )
    standard = raw[raw["split"] != "tail"]
    tail = raw[raw["split"] == "tail"]
    standard_baselines_raw = baselines[baselines["split"] != "tail"]
    tail_baselines_raw = baselines[baselines["split"] == "tail"]

    current_id = raw[
        (raw["family"] == "one_shot")
        & (raw["upper_target_usd"] == 6000.0)
        & (raw["lower_target_usd"] == 700.0)
    ]["candidate_id"].iloc[0]
    finalist_ids = (current_id, *FINALISTS)
    standard_summary = summarize(
        standard[standard["candidate_id"].isin(finalist_ids)]
    )
    tail_summary = summarize(tail[tail["candidate_id"].isin(finalist_ids)])
    standard_baselines = summarize_baselines(standard_baselines_raw)
    tail_baselines = summarize_baselines(tail_baselines_raw)

    current_standard = candidate_series(standard, current_id)
    current_tail = candidate_series(tail, current_id)
    very_coarse_standard = candidate_series(standard, "staged_very_coarse")
    very_coarse_tail = candidate_series(tail, "staged_very_coarse")
    comparisons = {
        "very_coarse_vs_current_standard": paired_bootstrap(
            very_coarse_standard,
            current_standard,
            20260911,
        ),
        "very_coarse_vs_current_tail": paired_bootstrap(
            very_coarse_tail,
            current_tail,
            20260912,
        ),
        "very_coarse_vs_ratio_standard": paired_bootstrap(
            very_coarse_standard,
            candidate_series(standard, "staged_ratio_like"),
            20260913,
        ),
        "very_coarse_vs_ratio_tail": paired_bootstrap(
            very_coarse_tail,
            candidate_series(tail, "staged_ratio_like"),
            20260914,
        ),
    }

    by_tail_regime = []
    for regime, group in tail.groupby("regime"):
        summary = summarize(group[group["candidate_id"].isin(finalist_ids)])
        for _, row in summary.iterrows():
            by_tail_regime.append({"regime": regime, **row.to_dict()})
    by_tail_regime_frame = pd.DataFrame(by_tail_regime)

    standard_summary.to_csv(
        results / f"{args.prefix}_standard_finalists.csv",
        index=False,
    )
    tail_summary.to_csv(
        results / f"{args.prefix}_tail_finalists.csv",
        index=False,
    )
    by_tail_regime_frame.to_csv(
        results / f"{args.prefix}_tail_by_regime.csv",
        index=False,
    )

    payload = {
        "study": "untouched replication for expansion-range finalists",
        "replication_salt": metadata["replication_salt"],
        "standard_cases": int(standard["case_id"].nunique()),
        "tail_cases": int(tail["case_id"].nunique()),
        "standard_finalists": records(standard_summary),
        "tail_finalists": records(tail_summary),
        "standard_baselines": records(standard_baselines),
        "tail_baselines": records(tail_baselines),
        "paired_bootstrap": comparisons,
    }
    (results / f"{args.prefix}_analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    def candidate_row(summary: pd.DataFrame, candidate_id: str) -> pd.Series:
        return summary[summary["candidate_id"] == candidate_id].iloc[0]

    current_standard_row = candidate_row(standard_summary, current_id)
    current_tail_row = candidate_row(tail_summary, current_id)
    ratio_standard_row = candidate_row(standard_summary, "staged_ratio_like")
    ratio_tail_row = candidate_row(tail_summary, "staged_ratio_like")
    coarse_standard_row = candidate_row(standard_summary, "staged_coarse")
    coarse_tail_row = candidate_row(tail_summary, "staged_coarse")
    very_coarse_standard_row = candidate_row(standard_summary, "staged_very_coarse")
    very_coarse_tail_row = candidate_row(tail_summary, "staged_very_coarse")
    fixed_standard_row = standard_baselines[
        standard_baselines["baseline"] == "fixed_standard"
    ].iloc[0]
    fixed_tail_row = tail_baselines[
        tail_baselines["baseline"] == "fixed_standard"
    ].iloc[0]
    standard_difference = (
        very_coarse_standard_row["mean_mae_usd"]
        - current_standard_row["mean_mae_usd"]
    )
    tail_advantage = (
        current_tail_row["mean_mae_usd"] - very_coarse_tail_row["mean_mae_usd"]
    )

    report = f"""# 扩张幅度最终候选独立复现实验

## 目的

第一次幅度实验完成后，`staged_very_coarse` 在冻结集保持原 6000/700 的常规表现，同时在尾部压力集继续扩张。由于这一观察发生在冻结集分析之后，本复现实验使用全新的随机种子，避免把原冻结集再次当作选参集。

- 独立常规案例：{standard['case_id'].nunique()} 个；
- 独立尾部压力案例：{tail['case_id'].nunique()} 个；
- 所有轨迹、消费模式和观测噪声均由复现盐 `{metadata['replication_salt']}` 重新生成。

## 常规案例

| 策略 | 建议值 MAE | 案例 P95 MAE | 最差参与者平均 MAE | 平均扩张级数 |
|---|---:|---:|---:|---:|
{table_row('原 6000/700 一次扩张', current_standard_row)}
{table_row('staged_ratio_like', ratio_standard_row)}
{table_row('staged_coarse', coarse_standard_row)}
{table_row('staged_very_coarse', very_coarse_standard_row)}
| 固定 1400～4000 | {fixed_standard_row['mean_mae_usd']:.3f} | {fixed_standard_row['p95_mae_usd']:.3f} | {fixed_standard_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |

`staged_very_coarse` 相对原策略的 MAE 差值为 {standard_difference:.3f} 美元；95% Bootstrap 区间为 [{comparisons['very_coarse_vs_current_standard']['ci95_low_usd']:.3f}, {comparisons['very_coarse_vs_current_standard']['ci95_high_usd']:.3f}]。

## 尾部压力案例

| 策略 | 建议值 MAE | 案例 P95 MAE | 最差参与者平均 MAE | 平均扩张级数 |
|---|---:|---:|---:|---:|
{table_row('原 6000/700 一次扩张', current_tail_row)}
{table_row('staged_ratio_like', ratio_tail_row)}
{table_row('staged_coarse', coarse_tail_row)}
{table_row('staged_very_coarse', very_coarse_tail_row)}
| 固定 1400～4000 | {fixed_tail_row['mean_mae_usd']:.3f} | {fixed_tail_row['p95_mae_usd']:.3f} | {fixed_tail_row['mean_worst_participant_mae_usd']:.3f} | 0.000 |

`staged_very_coarse` 相对原策略平均少错 {tail_advantage:.3f} 美元；95% Bootstrap 区间为 [{comparisons['very_coarse_vs_current_tail']['ci95_low_usd']:.3f}, {comparisons['very_coarse_vs_current_tail']['ci95_high_usd']:.3f}]。

## 决策

如果独立复现仍显示常规案例无可辨损失、尾部案例稳定改善，则采用 `staged_very_coarse`：第一次仍扩到上界 6000 或下界 700；同方向双证据继续成立时，再扩到上界 10000/20000，或下界 250/50。它不会在第一次触发时直接使用 50～20000。
"""
    (results / f"{args.prefix}_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
