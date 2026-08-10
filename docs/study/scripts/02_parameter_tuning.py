#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dynamic_limit.algorithms import (  # noqa: E402
    adaptive_phase_extension,
    multiphase_window,
    multiscale_window,
    single_window,
)
from dynamic_limit.experiment import default_selected_parameters, load_yaml, make_tuning_specs  # noqa: E402
from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.metrics import evaluate_output  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from dynamic_limit.particle_filter import (  # noqa: E402
    ParticleFilterConfig,
    calibrated_particle_interval,
    guarded_particle_filter,
    particle_filter,
)
from dynamic_limit.set_identification import recursive_outer_set  # noqa: E402


def summarize(df: pd.DataFrame, group: str) -> pd.DataFrame:
    return (
        df.groupby(group, as_index=False)
        .agg(
            cases=("case_id", "nunique"),
            mean_mae_usd=("mae_usd", "mean"),
            median_mae_usd=("mae_usd", "median"),
            p95_case_mae_usd=("mae_usd", lambda s: s.quantile(0.95)),
            mean_p95_abs_usd=("p95_abs_usd", "mean"),
            mean_max_abs_usd=("max_abs_usd", "mean"),
        )
        .sort_values(["mean_mae_usd", "p95_case_mae_usd"])
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config" / "tuning.yaml"))
    args = parser.parse_args()
    cfg = load_yaml(args.config)
    specs = make_tuning_specs(cfg)
    results_dir = ROOT / "results"
    prefix = str(cfg.get("results_prefix", ""))
    results_dir.mkdir(exist_ok=True)

    cache = []
    for spec in specs:
        truth = simulate_truth(spec)
        obs = make_observations(truth)
        cache.append((spec, truth, obs))

    raw_rows: list[dict] = []

    # Window-family tuning.
    for width in cfg["window_grid"]["single_width_pp"]:
        label = f"single_w{width}"
        for _, truth, obs in cache:
            out = single_window(obs, truth.rights, width_pp=float(width))
            row = evaluate_output(truth, obs, out)
            row["variant"] = label
            row["family"] = "single"
            raw_rows.append(row)

    for widths in cfg["window_grid"]["multiscale_sets"]:
        label = "multiscale_" + "-".join(map(str, widths))
        for _, truth, obs in cache:
            out = multiscale_window(obs, truth.rights, widths_pp=widths)
            row = evaluate_output(truth, obs, out)
            row["variant"] = label
            row["family"] = "multiscale"
            raw_rows.append(row)

    for width in cfg["window_grid"]["multiphase_width_pp"]:
        label = f"multiphase_w{width}"
        for _, truth, obs in cache:
            out = multiphase_window(obs, truth.rights, width_pp=int(width))
            row = evaluate_output(truth, obs, out)
            row["variant"] = label
            row["family"] = "multiphase"
            raw_rows.append(row)

    for threshold in cfg["window_grid"]["adaptive_spread_threshold"]:
        label = f"adaptive_t{threshold}"
        for _, truth, obs in cache:
            out = adaptive_phase_extension(obs, truth.rights, spread_threshold=float(threshold))
            row = evaluate_output(truth, obs, out)
            row["variant"] = label
            row["family"] = "adaptive"
            raw_rows.append(row)

    raw = pd.DataFrame(raw_rows)
    raw.to_csv(results_dir / f"{prefix}tuning_window_raw.csv.gz", index=False, compression="gzip")
    window_summary = summarize(raw, "variant")
    window_summary.to_csv(results_dir / f"{prefix}tuning_window_summary.csv", index=False)

    selected = default_selected_parameters()
    family_best: dict[str, str] = {}
    for family in ("single", "multiscale", "multiphase", "adaptive"):
        subset = raw[raw["family"] == family]
        ranking = summarize(subset, "variant")
        best = str(ranking.iloc[0]["variant"])
        family_best[family] = best
    selected["single_width_pp"] = int(family_best["single"].split("w")[-1])
    selected["multiscale_widths_pp"] = [int(x) for x in family_best["multiscale"].split("_")[-1].split("-")]
    selected["multiphase_width_pp"] = int(family_best["multiphase"].split("w")[-1])
    selected["adaptive_spread_threshold"] = float(family_best["adaptive"].split("t")[-1])

    # Particle-model tuning on a disjoint fixed subset of tuning cases.
    pf_cases = cache[: int(cfg["particle_grid"]["cases"])]
    pf_rows: list[dict] = []
    pf_outputs: dict[tuple[float, float, float], list] = {}
    for latent_sd, sigma, alpha in itertools.product(
        cfg["particle_grid"].get("latent_stationary_sd", [0.78]),
        cfg["particle_grid"]["observation_soft_sigma_pp"],
        cfg["particle_grid"]["timing_dirichlet_alpha"],
    ):
        key = (float(latent_sd), float(sigma), float(alpha))
        pf_outputs[key] = []
        for spec, truth, obs in pf_cases:
            pf_cfg = ParticleFilterConfig(
                particles=int(cfg["particle_grid"]["particles"]),
                latent_stationary_sd=float(latent_sd),
                observation_soft_sigma_pp=float(sigma),
                timing_dirichlet_alpha=float(alpha),
                capacity_min_usd=spec.capacity_min_usd,
                capacity_max_usd=spec.capacity_max_usd,
            )
            out = particle_filter(obs, truth.rights, seed=(spec.seed + 7919) % (2**32 - 1), config=pf_cfg)
            pf_outputs[key].append((truth, obs, out))
            row = evaluate_output(truth, obs, out)
            row["variant"] = f"pf_sd{latent_sd}_sigma{sigma}_alpha{alpha}"
            row["family"] = "particle"
            row["latent_sd"] = latent_sd
            row["sigma"] = sigma
            row["timing_alpha"] = alpha
            pf_rows.append(row)
    pf_raw = pd.DataFrame(pf_rows)
    pf_raw.to_csv(results_dir / f"{prefix}tuning_particle_raw.csv.gz", index=False, compression="gzip")
    pf_summary = summarize(pf_raw, "variant")
    pf_summary.to_csv(results_dir / f"{prefix}tuning_particle_summary.csv", index=False)
    best_pf_label = str(pf_summary.iloc[0]["variant"])
    best_row = pf_raw[pf_raw["variant"] == best_pf_label].iloc[0]
    best_key = (
        float(best_row["latent_sd"]),
        float(best_row["sigma"]),
        float(best_row["timing_alpha"]),
    )
    selected["pf_latent_stationary_sd"] = best_key[0]
    selected["pf_observation_soft_sigma_pp"] = best_key[1]
    selected["pf_timing_dirichlet_alpha"] = best_key[2]

    # Calibrate interval inflation: smallest factor reaching >=90% pooled sample
    # coverage on tuning cases. Width is recorded rather than hidden in a score.
    interval_rows = []
    best_pf_outputs = pf_outputs[best_key]
    for factor in cfg["interval_inflation_grid"]:
        cover_num = 0
        cover_den = 0
        widths = []
        for truth, obs, pf in best_pf_outputs:
            set_out = recursive_outer_set(
                obs,
                truth.rights,
                capacity_min_usd=truth.spec.capacity_min_usd,
                capacity_max_usd=truth.spec.capacity_max_usd,
            )
            cal = calibrated_particle_interval(pf, obs, set_out, inflation=float(factor))
            b_true, _ = true_limits(truth)
            sample_true = b_true[obs.sample_idx]
            covered = (sample_true >= cal.b_lower) & (sample_true <= cal.b_upper)
            cover_num += int(covered.sum())
            cover_den += int(covered.size)
            widths.append(float((cal.b_upper - cal.b_lower).mean()))
        interval_rows.append(
            {
                "inflation": float(factor),
                "sample_coverage": cover_num / cover_den,
                "mean_width_usd": float(np.mean(widths)),
            }
        )
    interval_df = pd.DataFrame(interval_rows)
    interval_df.to_csv(results_dir / f"{prefix}tuning_interval_calibration.csv", index=False)
    eligible = interval_df[interval_df["sample_coverage"] >= 0.90]
    if len(eligible):
        selected["pf_interval_inflation"] = float(eligible.iloc[0]["inflation"])
    else:
        selected["pf_interval_inflation"] = float(interval_df.iloc[-1]["inflation"])

    # Guarded point-estimate inertia tuning on the same PF subset.
    inertia_rows = []
    for inertia in cfg["guarded_inertia_grid"]:
        maes = []
        p95s = []
        for truth, obs, pf in best_pf_outputs:
            set_out = recursive_outer_set(
                obs,
                truth.rights,
                capacity_min_usd=truth.spec.capacity_min_usd,
                capacity_max_usd=truth.spec.capacity_max_usd,
            )
            cal = calibrated_particle_interval(
                pf, obs, set_out, inflation=float(selected["pf_interval_inflation"])
            )
            guard = guarded_particle_filter(cal, set_out, obs, inertia=float(inertia))
            met = evaluate_output(truth, obs, guard)
            maes.append(met["mae_usd"])
            p95s.append(met["p95_abs_usd"])
        inertia_rows.append(
            {
                "inertia": float(inertia),
                "mean_mae_usd": float(np.mean(maes)),
                "p95_case_mae_usd": float(np.quantile(maes, 0.95)),
                "mean_p95_abs_usd": float(np.mean(p95s)),
            }
        )
    inertia_df = pd.DataFrame(inertia_rows).sort_values(["mean_mae_usd", "p95_case_mae_usd"])
    inertia_df.to_csv(results_dir / f"{prefix}tuning_guarded_inertia.csv", index=False)
    selected["pf_guarded_inertia"] = float(inertia_df.iloc[0]["inertia"])

    selected["tuning_case_count"] = len(specs)
    selected["capacity_min_usd"] = float(cfg.get("capacity_range", [1400.0, 2100.0])[0])
    selected["capacity_max_usd"] = float(cfg.get("capacity_range", [1400.0, 2100.0])[1])
    selected["particle_count"] = int(cfg["particle_grid"]["particles"])
    selected["particle_tuning_case_count"] = len(pf_cases)
    selected["selection_rule"] = (
        "Each point-estimator family uses the parameter with the lowest tuning-set mean trajectory MAE; "
        "case-level p95 is retained as a separate diagnostic. Interval inflation is the smallest grid value "
        "with at least 90% pooled sample-time coverage."
    )
    selected_file = str(cfg.get("selected_parameters_file", f"{prefix}selected_parameters.json"))
    selection_file = str(cfg.get("selection_file", f"{prefix}tuning_selection.json"))
    with open(results_dir / selected_file, "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)
    with open(results_dir / selection_file, "w", encoding="utf-8") as f:
        json.dump({"family_best": family_best, "best_particle": best_pf_label}, f, ensure_ascii=False, indent=2)
    print(json.dumps(selected, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
