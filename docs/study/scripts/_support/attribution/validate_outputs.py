"""Validate the completed V2 research artifact tree.

This script checks file readability, frozen sample sizes, algorithm multiplicities,
solver success where required, exact-set truth coverage, tie-coupling properties,
and the presence of paper/figure outputs. It writes a machine-readable report and
exits non-zero on any failed invariant.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "results" / "raw"
SUMMARY = ROOT / "results" / "summary"


def read_parts(folder: str) -> pd.DataFrame:
    files = sorted((RAW / folder).glob("*.csv.gz"))
    if not files:
        raise AssertionError(f"No compressed CSV parts found in {folder}")
    frames = [pd.read_csv(path) for path in files]
    return pd.concat(frames, ignore_index=True)


def check_dataset(folder: str, expected_cycles: int, expected_algorithms: int) -> dict[str, Any]:
    data = read_parts(folder)
    cycles = int(data[["scenario", "seed"]].drop_duplicates().shape[0])
    algorithms = int(data["algorithm"].nunique())
    rows = int(len(data))
    expected_rows = expected_cycles * expected_algorithms
    success_rate = float(data["success"].mean())
    assert cycles == expected_cycles, (folder, cycles, expected_cycles)
    assert algorithms == expected_algorithms, (folder, algorithms, expected_algorithms)
    assert rows == expected_rows, (folder, rows, expected_rows)
    assert success_rate == 1.0, (folder, success_rate)
    return {
        "cycles": cycles,
        "algorithms": algorithms,
        "rows": rows,
        "success_rate": success_rate,
    }


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "attribution_study.yaml").read_text(encoding="utf-8"))
    report: dict[str, Any] = {"status": "PASS", "checks": {}}

    report["checks"]["main_fast"] = check_dataset("main_fast", 100_000, 8)
    report["checks"]["main_lp"] = check_dataset("main_lp", 10_000, 8)
    report["checks"]["ood_fast"] = check_dataset("ood_fast", 10_000, 8)
    report["checks"]["ood_lp"] = check_dataset("ood_lp", 2_000, 8)

    center = pd.read_csv(RAW / "audits" / "center_nonuniqueness.csv.gz")
    assert len(center) == 480
    assert center["seed"].nunique() == 120
    report["checks"]["center_audit"] = {
        "rows": int(len(center)),
        "cycles": int(center["seed"].nunique()),
        "nonunique_cycle_fraction": float(
            (center[center.algorithm == "set_midpoint_lex"].face_diameter_linf > 1e-5).mean()
        ),
    }

    ties = pd.read_csv(RAW / "audits" / "tie_coupling.csv.gz")
    assert len(ties) == int(cfg["sample_sizes"]["tie_cycles"])
    assert float(ties["exact_coverage"].mean()) == 1.0
    assert float(ties["legacy_coverage"].mean()) == 1.0
    assert bool((ties["legacy_width_mean"] + 1e-10 >= ties["exact_width_mean"]).all())
    report["checks"]["timestamp_ties"] = {
        "cycles": int(len(ties)),
        "exact_coverage": float(ties["exact_coverage"].mean()),
        "legacy_coverage": float(ties["legacy_coverage"].mean()),
        "legacy_is_outer_envelope_all": True,
    }

    delay = pd.read_csv(RAW / "delay" / "online_delay_metrics.csv.gz")
    assert len(delay) == 81_000
    report["checks"]["strict_delay"] = {
        "rows": int(len(delay)),
        "full_cycles": int(delay["seed"].nunique()),
        "target_fractions": sorted(map(float, delay["target_fraction"].unique())),
        "requested_lags_minutes": sorted(map(int, delay["requested_lag_minutes"].unique())),
    }

    quant = pd.read_csv(RAW / "quantizer" / "quantizer_metrics.csv.gz")
    assert len(quant) == 15_000
    report["checks"]["quantizer_stress"] = {
        "rows": int(len(quant)),
        "cycles": int(quant[["generator_quantizer", "seed"]].drop_duplicates().shape[0]),
        "generator_quantizers": sorted(quant["generator_quantizer"].unique().tolist()),
    }

    history = pd.read_csv(RAW / "history" / "history_metrics.csv.gz")
    reconciliation = pd.read_csv(RAW / "reconciliation" / "reconciliation_metrics.csv.gz")
    boundary = pd.read_csv(RAW / "boundary" / "boundary_metrics.csv.gz")
    assert len(history) == 7_200
    assert len(reconciliation) == 3_600
    assert len(boundary) == 1_000
    report["checks"]["history_rows"] = int(len(history))
    report["checks"]["reconciliation_rows"] = int(len(reconciliation))
    report["checks"]["boundary_rows"] = int(len(boundary))

    interaction_files = sorted((RAW / "phase_interaction").glob("*.csv.gz"))
    interaction_rows = sum(len(pd.read_csv(path)) for path in interaction_files)
    assert len(interaction_files) == 10
    assert interaction_rows == 108_900
    report["checks"]["phase_interaction"] = {
        "scenario_files": int(len(interaction_files)),
        "rows": int(interaction_rows),
        "audit_cycles": 30,
        "configurations_per_cycle": 3_630,
    }

    adversarial_best = pd.read_csv(RAW / "adversarial" / "adversarial_search_best.csv")
    search_runs = adversarial_best[["search_target", "restart"]].drop_duplicates()
    assert len(adversarial_best) == 96
    assert len(search_runs) == 16
    report["checks"]["active_search"] = {
        "algorithm_rows": int(len(adversarial_best)),
        "independent_search_runs": int(len(search_runs)),
        "targets": sorted(adversarial_best["search_target"].unique().tolist()),
        "max_found_over_attribution_pp": float(adversarial_best["max_over"].max()),
    }

    key = json.loads((SUMMARY / "final_key_values.json").read_text(encoding="utf-8"))
    assert key["main_fast_cycles"] == 100_000
    assert key["main_lp_cycles"] == 10_000
    assert key["ood_fast_cycles"] == 10_000
    assert key["ood_lp_cycles"] == 2_000
    assert key["set_coverage"] == 1.0
    report["checks"]["final_summary"] = {
        "set_coverage": float(key["set_coverage"]),
        "phase_vs_single_window_mae_improvement_pct": float(
            key["phase_vs_window_mae_improvement_pct"]
        ),
    }

    required_figures = [
        "main_fast_mae", "main_lp_mae", "ood_fast_mae", "ood_lp_mae",
        "center_face_hist", "timestamp_tie_scatter", "delay_mae",
        "quantizer_feasibility", "history_prior", "adversarial_found",
    ]
    for stem in required_figures:
        for ext in ("pdf", "png"):
            path = ROOT / "results" / "figures" / f"{stem}.{ext}"
            assert path.exists() and path.stat().st_size > 0, path
    report["checks"]["required_figures"] = len(required_figures) * 2

    paper = ROOT / "paper" / "main.pdf"
    assert paper.exists() and paper.stat().st_size > 0
    report["checks"]["paper_pdf_bytes"] = int(paper.stat().st_size)

    SUMMARY.mkdir(parents=True, exist_ok=True)
    (SUMMARY / "validation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = ["V2 FINAL VALIDATION: PASS"]
    for name, value in report["checks"].items():
        lines.append(f"- {name}: {value}")
    (ROOT / "notes" / "validation_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
