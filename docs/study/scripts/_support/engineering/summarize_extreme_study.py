#!/usr/bin/env python3
"""Derive statistical comparisons and design diagnostics for the extreme study.

The simulation runner saves case-level metrics.  This script keeps inferential
post-processing separate so the raw experiment never depends on report prose.
All comparisons are paired by case and use the frozen test split only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "results"
PF = "particle_filter_mixture"
BOOTSTRAP_REPLICATES = 20_000


def stable_seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little")


def add_progress_band(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    progress = enriched["final_total_progress_pp"]
    enriched["progress_band"] = np.select(
        [progress <= 25.001, progress <= 70.001],
        ["low_8_25", "medium_45_70"],
        default="high_88_99",
    )
    return enriched

def paired_bootstrap_ci(values: np.ndarray, label: str) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(stable_seed(f"extreme-bootstrap:{label}"))
    means = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
    chunk_size = 500
    for start in range(0, BOOTSTRAP_REPLICATES, chunk_size):
        stop = min(start + chunk_size, BOOTSTRAP_REPLICATES)
        indices = rng.integers(0, len(values), size=(stop - start, len(values)))
        means[start:stop] = values[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return float(lower), float(upper)


def holm_adjust(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues.items(), key=lambda item: item[1])
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, pvalue) in enumerate(ordered):
        candidate = min(1.0, (total - rank) * pvalue)
        running = max(running, candidate)
        adjusted[name] = running
    return adjusted


def statistical_comparisons(frame: pd.DataFrame) -> pd.DataFrame:
    pivot = frame.pivot(index="case_id", columns="algorithm", values="mae_usd")
    pf = pivot[PF].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    raw_pvalues: dict[str, float] = {}
    for algorithm in pivot.columns:
        if algorithm == PF:
            continue
        difference = pivot[algorithm].to_numpy(dtype=float) - pf
        lower, upper = paired_bootstrap_ci(difference, algorithm)
        try:
            pvalue = float(wilcoxon(difference, zero_method="pratt").pvalue)
        except ValueError:
            pvalue = 1.0
        raw_pvalues[str(algorithm)] = pvalue
        rows.append(
            {
                "algorithm": str(algorithm),
                "cases": int(len(difference)),
                "mean_mae_difference_competitor_minus_pf_usd": float(difference.mean()),
                "paired_bootstrap_95ci_lower_usd": lower,
                "paired_bootstrap_95ci_upper_usd": upper,
                "pf_lower_mae_fraction": float((difference > 0).mean()),
                "equal_mae_fraction": float((difference == 0).mean()),
                "wilcoxon_p_raw": pvalue,
            }
        )
    adjusted = holm_adjust(raw_pvalues)
    for row in rows:
        row["wilcoxon_p_holm"] = adjusted[str(row["algorithm"])]
    return pd.DataFrame(rows).sort_values(
        "mean_mae_difference_competitor_minus_pf_usd", ascending=False
    )


def factor_findings(frame: pd.DataFrame) -> pd.DataFrame:
    factors = [
        "speed",
        "scenario",
        "sample_hours",
        "n_participants",
        "rights_profile",
        "quantizer",
        "progress_band",
    ]
    rows: list[dict[str, Any]] = []
    for factor in factors:
        grouped = (
            frame.groupby([factor, "algorithm"], as_index=False)["mae_usd"]
            .mean()
            .rename(columns={"mae_usd": "mean_mae_usd"})
        )
        for level, level_frame in grouped.groupby(factor):
            ordered = level_frame.sort_values("mean_mae_usd")
            best = ordered.iloc[0]
            pf_mae = float(
                level_frame.loc[level_frame["algorithm"] == PF, "mean_mae_usd"].iloc[0]
            )
            rows.append(
                {
                    "factor": factor,
                    "level": level,
                    "best_algorithm": str(best["algorithm"]),
                    "best_mean_mae_usd": float(best["mean_mae_usd"]),
                    "particle_filter_mean_mae_usd": pf_mae,
                    "particle_filter_gap_to_best_usd": pf_mae
                    - float(best["mean_mae_usd"]),
                    "particle_filter_rank": int(
                        ordered.reset_index(drop=True)
                        .index[ordered["algorithm"].to_numpy() == PF][0]
                        + 1
                    ),
                }
            )
    return pd.DataFrame(rows)


def design_balance(frame: pd.DataFrame) -> dict[str, Any]:
    factors = [
        "speed",
        "scenario",
        "sample_hours",
        "n_participants",
        "rights_profile",
        "quantizer",
        "progress_band",
    ]
    cases = frame[frame["algorithm"] == PF].copy()
    marginals = {
        factor: {
            str(level): int(count)
            for level, count in cases[factor].value_counts().sort_index().items()
        }
        for factor in factors
    }
    pairwise: dict[str, Any] = {}
    for left_index, left in enumerate(factors):
        for right in factors[left_index + 1 :]:
            table = pd.crosstab(cases[left], cases[right])
            pairwise[f"{left}__{right}"] = {
                "observed_cells": int((table > 0).sum().sum()),
                "possible_cells": int(table.shape[0] * table.shape[1]),
                "minimum_cell_count": int(table.to_numpy().min()),
                "maximum_cell_count": int(table.to_numpy().max()),
            }
    return {
        "cases": int(len(cases)),
        "design": (
            "Full speed × scenario × quantizer grid with independently shuffled, "
            "globally balanced assignments for participant count, rights profile, "
            "sample interval, and target-progress band."
        ),
        "marginal_counts": marginals,
        "pairwise_cross_tables": pairwise,
        "all_pairwise_combinations_observed": bool(
            all(
                entry["observed_cells"] == entry["possible_cells"]
                for entry in pairwise.values()
            )
        ),
    }


def main() -> None:
    frame = add_progress_band(pd.read_csv(RESULTS / "extreme_test_metrics.csv.gz"))
    comparisons = statistical_comparisons(frame)
    factors = factor_findings(frame)
    balance = design_balance(frame)

    comparisons.to_csv(RESULTS / "extreme_statistical_comparisons.csv", index=False)
    factors.to_csv(RESULTS / "extreme_factor_findings.csv", index=False)
    (RESULTS / "extreme_design_balance.json").write_text(
        json.dumps(balance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": int(frame["case_id"].nunique()),
                "comparisons": int(len(comparisons)),
                "factor_levels": int(len(factors)),
                "all_pairwise_combinations_observed": balance[
                    "all_pairwise_combinations_observed"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
