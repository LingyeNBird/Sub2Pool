#!/usr/bin/env python3
"""Verify expansion-range artifacts and the final decision invariants."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "results"


def verify_run(prefix: str, expected_cases: int, expected_candidates: int) -> None:
    raw = pd.read_csv(RESULTS / f"{prefix}_raw.csv.gz")
    baselines = pd.read_csv(RESULTS / f"{prefix}_baselines.csv")
    metadata = json.loads(
        (RESULTS / f"{prefix}_metadata.json").read_text(encoding="utf-8")
    )
    assert raw["case_id"].nunique() == expected_cases
    assert raw["candidate_id"].nunique() == expected_candidates
    assert len(raw) == expected_cases * expected_candidates
    assert len(baselines) == expected_cases * 2
    assert raw[
        [
            "mae_usd",
            "rmse_usd",
            "worst_participant_mae_usd",
            "capacity_sample_mae_usd",
        ]
    ].notna().all().all()
    assert metadata["one_shot_candidate_count"] + metadata["staged_candidate_count"] == expected_candidates


def main() -> None:
    verify_run("expansion_range", expected_cases=1120, expected_candidates=115)
    verify_run(
        "expansion_range_confirmation",
        expected_cases=1120,
        expected_candidates=115,
    )
    confirmation = json.loads(
        (RESULTS / "expansion_range_confirmation_analysis.json").read_text(
            encoding="utf-8"
        )
    )
    standard = {
        row["candidate_id"]: row for row in confirmation["standard_finalists"]
    }
    tail = {row["candidate_id"]: row for row in confirmation["tail_finalists"]}
    current_id = next(
        candidate_id
        for candidate_id, row in standard.items()
        if row["family"] == "one_shot"
    )
    assert abs(
        standard["staged_very_coarse"]["mean_mae_usd"]
        - standard[current_id]["mean_mae_usd"]
    ) < 0.1
    assert (
        tail["staged_very_coarse"]["mean_mae_usd"]
        < tail[current_id]["mean_mae_usd"]
    )
    comparisons = confirmation["paired_bootstrap"]
    assert comparisons["very_coarse_vs_current_tail"]["ci95_high_usd"] < 0.0
    assert comparisons["very_coarse_vs_ratio_tail"]["ci95_high_usd"] < 0.0
    assert (RESULTS / "expansion_range_final_report.md").is_file()
    print("expansion-range verification passed: 2 x 1120 cases, 115 candidates")


if __name__ == "__main__":
    main()
