#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dynamic_limit.experiment import (  # noqa: E402
    load_yaml,
    make_final_specs,
    run_standard_case,
)
from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from dynamic_limit.set_identification import exact_lp_identification, recursive_outer_set  # noqa: E402


def _worker(args):
    spec, selected, particle_count = args
    metrics, phase, _, metadata = run_standard_case(
        spec, selected, particle_count=particle_count, save_trajectory=False
    )
    return metrics, phase, metadata


def run_suite(specs, selected, particle_count, workers, label):
    all_metrics = []
    all_phase = []
    metadata = []
    tasks = [(s, selected, particle_count) for s in specs]
    if workers <= 1:
        for idx, task in enumerate(tasks, 1):
            m, p, meta = _worker(task)
            all_metrics.extend(m)
            if not p.empty:
                all_phase.append(p)
            metadata.append(meta)
            if idx % 50 == 0 or idx == len(tasks):
                print(f"[{label}] {idx}/{len(tasks)}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_worker, task): task[0].case_id for task in tasks}
            for idx, future in enumerate(as_completed(futures), 1):
                m, p, meta = future.result()
                all_metrics.extend(m)
                if not p.empty:
                    all_phase.append(p)
                metadata.append(meta)
                if idx % 50 == 0 or idx == len(tasks):
                    print(f"[{label}] {idx}/{len(tasks)}", flush=True)
    return pd.DataFrame(all_metrics), (pd.concat(all_phase, ignore_index=True) if all_phase else pd.DataFrame()), metadata


def bootstrap_ci(values: np.ndarray, rng: np.random.Generator, reps: int = 1200) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return np.nan, np.nan
    idx = rng.integers(0, len(values), size=(reps, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def aggregate_metrics(df: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(20260808)
    rows = []
    for alg, g in df.groupby("algorithm"):
        lo, hi = bootstrap_ci(g["mae_usd"].to_numpy(), rng)
        rows.append(
            {
                "algorithm": alg,
                "cases": g["case_id"].nunique(),
                "mean_mae_usd": g["mae_usd"].mean(),
                "mean_mae_bootstrap_ci_low": lo,
                "mean_mae_bootstrap_ci_high": hi,
                "median_case_mae_usd": g["mae_usd"].median(),
                "p95_case_mae_usd": g["mae_usd"].quantile(0.95),
                "mean_rmse_usd": g["rmse_usd"].mean(),
                "mean_p95_abs_usd": g["p95_abs_usd"].mean(),
                "worst_observed_max_abs_usd": g["max_abs_usd"].max(),
                "mean_bias_usd": g["bias_usd"].mean(),
                "mean_over_usd": g["mean_over_usd"].mean(),
                "mean_under_usd": g["mean_under_usd"].mean(),
                "mean_worst_participant_mae_usd": g["worst_participant_mae_usd"].mean(),
                "mean_hold_mae_increment_usd": g["hold_mae_increment_usd"].mean(),
                "mean_adjustment_total_variation_usd": g["adjustment_total_variation_usd"].mean(),
                "mean_adjustment_count_gt_1usd": g["adjustment_count_gt_1usd"].mean(),
                "mean_interval_sample_coverage": g["interval_sample_coverage"].mean(),
                "mean_interval_width_usd": g["interval_mean_width_usd"].mean(),
                "mean_q_sample_mae_pp": g["q_sample_mae_pp"].mean(),
                "mean_v_sample_mae_usd": g["v_sample_mae_usd"].mean(),
            }
        )
    return pd.DataFrame(rows).sort_values(["mean_mae_usd", "p95_case_mae_usd"])


def stratified(df: pd.DataFrame, field: str) -> pd.DataFrame:
    return (
        df.groupby(["algorithm", field], as_index=False)
        .agg(
            cases=("case_id", "nunique"),
            mean_mae_usd=("mae_usd", "mean"),
            p95_case_mae_usd=("mae_usd", lambda s: s.quantile(0.95)),
            mean_p95_abs_usd=("p95_abs_usd", "mean"),
            mean_max_abs_usd=("max_abs_usd", "mean"),
            mean_bias_usd=("bias_usd", "mean"),
            interval_coverage=("interval_sample_coverage", "mean"),
            interval_width_usd=("interval_mean_width_usd", "mean"),
        )
        .sort_values([field, "mean_mae_usd"])
    )


def pairwise_table(df: pd.DataFrame) -> pd.DataFrame:
    pivot = df.pivot(index="case_id", columns="algorithm", values="mae_usd")
    rows = []
    algs = list(pivot.columns)
    for a in algs:
        for b in algs:
            if a == b:
                continue
            valid = pivot[[a, b]].dropna()
            diff = valid[a] - valid[b]
            rows.append(
                {
                    "algorithm_a": a,
                    "algorithm_b": b,
                    "cases": len(valid),
                    "mean_mae_difference_a_minus_b": diff.mean(),
                    "median_difference": diff.median(),
                    "a_win_fraction": float((diff < 0).mean()),
                    "tie_fraction": float((np.abs(diff) < 1e-12).mean()),
                }
            )
    return pd.DataFrame(rows)


def auc_binary(labels: np.ndarray, scores: np.ndarray) -> float:
    labels = np.asarray(labels, dtype=bool)
    scores = np.asarray(scores, dtype=float)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return np.nan
    ranks = rankdata(scores)
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def phase_analysis(phase: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    clean = phase[np.isfinite(phase["phase_spread"]) & (phase["phase_count"] >= 2)].copy()
    stats = []
    bins = []
    for alg, g in clean.groupby("algorithm"):
        rho, p = spearmanr(g["phase_spread"], g["mean_abs_limit_error_usd"])
        threshold = g["mean_abs_limit_error_usd"].quantile(0.75)
        auc = auc_binary(g["mean_abs_limit_error_usd"].to_numpy() >= threshold, g["phase_spread"].to_numpy())
        stats.append(
            {
                "algorithm": alg,
                "rows": len(g),
                "spearman_rho": rho,
                "spearman_p_value": p,
                "top_quartile_error_auc": auc,
                "error_top_quartile_threshold_usd": threshold,
                "zero_or_near_zero_spread_fraction": float((g["phase_spread"] < 1e-6).mean()),
            }
        )
        try:
            g["spread_decile"] = pd.qcut(g["phase_spread"], 10, labels=False, duplicates="drop")
        except ValueError:
            g["spread_decile"] = 0
        b = (
            g.groupby("spread_decile", as_index=False)
            .agg(
                rows=("case_id", "size"),
                mean_spread=("phase_spread", "mean"),
                mean_error_usd=("mean_abs_limit_error_usd", "mean"),
                p90_error_usd=("mean_abs_limit_error_usd", lambda s: s.quantile(0.90)),
            )
        )
        b["algorithm"] = alg
        bins.append(b)
    width_dist = (
        clean[clean["algorithm"] == "adaptive_phase_extension"]
        .groupby("chosen_width_pp", as_index=False)
        .agg(rows=("case_id", "size"), mean_error_usd=("mean_abs_limit_error_usd", "mean"))
    )
    if len(width_dist):
        width_dist["fraction"] = width_dist["rows"] / width_dist["rows"].sum()
    return pd.DataFrame(stats), (pd.concat(bins, ignore_index=True) if bins else pd.DataFrame()), width_dist


def lp_validation(specs, max_cases=12) -> pd.DataFrame:
    rows = []
    selected = []
    seen = set()
    for spec in specs:
        key = (spec.n_participants, spec.sample_hours, spec.quantizer)
        if key not in seen:
            selected.append(spec)
            seen.add(key)
        if len(selected) >= max_cases:
            break
    for spec in selected:
        truth = simulate_truth(spec)
        obs = make_observations(truth)
        positions = sorted(set([len(obs.times) // 3, 2 * len(obs.times) // 3, len(obs.times) - 1]))
        exact = exact_lp_identification(obs, truth.rights, positions)
        outer = recursive_outer_set(obs, truth.rights)
        b_true, _ = true_limits(truth)
        for j, k in enumerate(positions):
            for i in range(spec.n_participants):
                rows.append(
                    {
                        "case_id": spec.case_id,
                        "sample_position": k,
                        "time_hours": obs.times[k],
                        "participant": i,
                        "q_true": truth.q[obs.sample_idx[k], i],
                        "q_lp_lower": exact["q_lower"][j, i],
                        "q_lp_upper": exact["q_upper"][j, i],
                        "q_lp_covers": bool(
                            exact["q_lower"][j, i] - 1e-8 <= truth.q[obs.sample_idx[k], i] <= exact["q_upper"][j, i] + 1e-8
                        ),
                        "b_true": b_true[obs.sample_idx[k], i],
                        "b_lp_lower": exact["b_lower"][j, i],
                        "b_lp_upper": exact["b_upper"][j, i],
                        "b_lp_covers": bool(
                            exact["b_lower"][j, i] - 1e-8 <= b_true[obs.sample_idx[k], i] <= exact["b_upper"][j, i] + 1e-8
                        ),
                        "b_outer_lower": outer.b_lower[k, i],
                        "b_outer_upper": outer.b_upper[k, i],
                        "outer_to_lp_width_ratio": (outer.b_upper[k, i] - outer.b_lower[k, i])
                        / max(exact["b_upper"][j, i] - exact["b_lower"][j, i], 1e-12),
                        "feasible_quantizer_branches": exact["feasible_quantizer_branches"][j],
                    }
                )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "final_test.yaml"))
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument(
        "--skip-trajectories",
        action="store_true",
        help="Skip the slower representative-trajectory reruns; run finalize_results.py afterward.",
    )
    args = parser.parse_args()
    cfg = load_yaml(args.config)
    selected_path = ROOT / cfg["selected_parameters_file"]
    with open(selected_path, "r", encoding="utf-8") as f:
        selected = json.load(f)
    specs, stress_specs = make_final_specs(cfg)
    particle_count = int(cfg["particle_count"])
    results = ROOT / "results"

    final_df, phase_df, metadata = run_suite(specs, selected, particle_count, args.workers, "final")
    stress_df, stress_phase_df, stress_metadata = run_suite(
        stress_specs, selected, particle_count, args.workers, "stress"
    )
    final_df.to_csv(results / "final_raw_metrics.csv.gz", index=False, compression="gzip")
    stress_df.to_csv(results / "stress_raw_metrics.csv.gz", index=False, compression="gzip")
    all_phase = pd.concat([phase_df.assign(suite="final"), stress_phase_df.assign(suite="stress")], ignore_index=True)
    all_phase.to_csv(results / "phase_diagnostics.csv.gz", index=False, compression="gzip")
    with open(results / "case_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    with open(results / "stress_case_metadata.json", "w", encoding="utf-8") as f:
        json.dump(stress_metadata, f, ensure_ascii=False, indent=2)

    aggregate_metrics(final_df).to_csv(results / "final_aggregate.csv", index=False)
    aggregate_metrics(stress_df).to_csv(results / "stress_aggregate.csv", index=False)
    for field in ["speed", "sample_hours", "n_participants", "scenario", "quantizer", "rights_profile"]:
        stratified(final_df, field).to_csv(results / f"final_by_{field}.csv", index=False)
    pairwise_table(final_df).to_csv(results / "pairwise_mae_comparisons.csv", index=False)

    phase_stats, phase_bins, width_dist = phase_analysis(all_phase)
    phase_stats.to_csv(results / "phase_disagreement_statistics.csv", index=False)
    phase_bins.to_csv(results / "phase_disagreement_deciles.csv", index=False)
    width_dist.to_csv(results / "adaptive_width_distribution.csv", index=False)

    lp_df = lp_validation(specs, max_cases=12)
    lp_df.to_csv(results / "exact_lp_validation.csv", index=False)

    # Representative trajectories require additional particle-filter reruns.
    # They may be deferred to finalize_results.py so the expensive matrix run
    # and trajectory rendering can be resumed independently.
    selected_ids: set[str] = set()
    if not args.skip_trajectories:
        representative = [
            s.case_id
            for s in specs
            if s.speed == "medium" and s.n_participants == 4 and s.scenario == "staggered" and s.sample_hours == 3.0 and s.quantizer == "nearest"
        ][:1]
        selected_ids.update(representative)
        for alg in ["particle_filter_mixture", "multiphase_9pp", "adaptive_phase_extension", "static_1750"]:
            selected_ids.update(final_df[final_df["algorithm"] == alg].nlargest(2, "mae_usd")["case_id"].tolist())
        selected_ids.update(stress_df[stress_df["algorithm"] == "particle_filter_mixture"].nlargest(2, "mae_usd")["case_id"].tolist())
        spec_map = {s.case_id: s for s in specs + stress_specs}
        trajectory_frames = []
        for case_id in sorted(selected_ids):
            _, _, trajectory, _ = run_standard_case(
                spec_map[case_id], selected, particle_count=particle_count, save_trajectory=True
            )
            trajectory_frames.append(trajectory)
        trajectories = pd.concat(trajectory_frames, ignore_index=True)
        trajectories.to_csv(results / "selected_trajectories.csv.gz", index=False, compression="gzip")
        with open(results / "selected_trajectory_cases.json", "w", encoding="utf-8") as f:
            json.dump(sorted(selected_ids), f, ensure_ascii=False, indent=2)

    experiment_meta = {
        "final_case_count": len(specs),
        "stress_case_count": len(stress_specs),
        "algorithms_per_case": int(final_df["algorithm"].nunique()),
        "final_metric_rows": len(final_df),
        "stress_metric_rows": len(stress_df),
        "particle_count": particle_count,
        "selected_parameters": selected,
        "workers": args.workers,
        "trajectories_deferred": bool(args.skip_trajectories),
        "selected_trajectory_case_count": len(selected_ids),
        "finite_sample_warning": "Observed maxima are finite-sample maxima, not mathematical worst-case bounds.",
    }
    with open(results / "experiment_metadata.json", "w", encoding="utf-8") as f:
        json.dump(experiment_meta, f, ensure_ascii=False, indent=2)
    print(json.dumps(experiment_meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
