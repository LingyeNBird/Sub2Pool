#!/usr/bin/env python3
"""Run an out-of-model continuous extreme study for every V2-1 estimator.

The normal V2-1 benchmark answers which method is best on its declared process
family.  This supplement deliberately preserves the mathematical contract
(continuous V(t), 1400--2100 bounds, complete sampled cumulative costs, integer
progress) while making both the hidden path and participant consumption much
more hostile.  A development split selects observable fallback policies; the
frozen test, high-frequency, and adversarial-pool splits never tune them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dynamic_limit.algorithms import (  # noqa: E402
    adaptive_phase_extension,
    amount_proportion,
    multiphase_window,
    multiscale_window,
    single_window,
    static_allocation,
)
from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.metrics import evaluate_output  # noqa: E402
from dynamic_limit.models import AlgorithmOutput, SimulationSpec  # noqa: E402
from dynamic_limit.observation import make_observations  # noqa: E402
from dynamic_limit.particle_filter import (  # noqa: E402
    ParticleFilterConfig,
    calibrated_particle_interval,
    particle_filter,
)
from dynamic_limit.set_identification import recursive_outer_set  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def save_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)


def stable_seed(base: int, text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int((base + int.from_bytes(digest[:8], "little")) % (2**32 - 1))


def _spec_from_key(
    cfg: dict[str, Any],
    suite: str,
    key: str,
    speed: str,
    scenario: str,
    quantizer: str,
    case_index: int,
    sample_hours: float | None = None,
    n_participants: int | None = None,
    rights_profile: str | None = None,
    target_progress_band: list[float] | tuple[float, float] | None = None,
) -> SimulationSpec:
    seed = stable_seed(int(cfg["base_seed"]), f"{suite}:{key}")
    rng = np.random.default_rng(seed)
    participants = list(cfg["participants"])
    rights_profiles = list(cfg["rights_profiles"])
    samples = list(cfg["sample_hours"])
    bands = list(cfg["target_progress_bands"])
    n = int(n_participants if n_participants is not None else rng.choice(participants))
    rights = str(
        rights_profile if rights_profile is not None else rng.choice(rights_profiles)
    )
    sample = float(sample_hours if sample_hours is not None else rng.choice(samples))
    band = (
        target_progress_band
        if target_progress_band is not None
        else bands[int(rng.integers(0, len(bands)))]
    )
    return SimulationSpec(
        case_id=f"{suite}_{case_index:05d}_{speed}_{scenario}_{quantizer}",
        seed=seed,
        speed=speed,
        n_participants=n,
        rights_profile=rights,
        scenario=scenario,
        sample_hours=sample,
        quantizer=quantizer,
        horizon_hours=float(cfg["horizon_hours"]),
        dt_hours=float(cfg["dt_hours"]),
        target_progress_low=float(band[0]),
        target_progress_high=float(band[1]),
        capacity_min_usd=float(cfg.get("capacity_range", [1400.0, 2100.0])[0]),
        capacity_max_usd=float(cfg.get("capacity_range", [1400.0, 2100.0])[1]),
    )


def structured_specs(cfg: dict[str, Any], suite: str, replicates: int) -> list[SimulationSpec]:
    rows = [
        (replicate, str(speed), str(scenario), str(quantizer))
        for replicate in range(int(replicates))
        for speed in cfg["extreme_speeds"]
        for scenario in cfg["extreme_scenarios"]
        for quantizer in cfg["quantizers"]
    ]

    def balanced_assignment(values: list[Any], label: str) -> list[Any]:
        if not values:
            raise ValueError(f"{label} must contain at least one level")
        assigned = [values[index % len(values)] for index in range(len(rows))]
        rng = np.random.default_rng(
            stable_seed(int(cfg["base_seed"]), f"{suite}:design:{label}")
        )
        rng.shuffle(assigned)
        return assigned

    participant_assignment = balanced_assignment(list(cfg["participants"]), "participants")
    rights_assignment = balanced_assignment(list(cfg["rights_profiles"]), "rights")
    sample_assignment = balanced_assignment(list(cfg["sample_hours"]), "sample_hours")
    band_assignment = balanced_assignment(
        list(cfg["target_progress_bands"]), "target_progress_bands"
    )

    specs: list[SimulationSpec] = []
    for case_index, (replicate, speed, scenario, quantizer) in enumerate(rows):
        key = f"r{replicate}:{speed}:{scenario}:{quantizer}"
        specs.append(
            _spec_from_key(
                cfg,
                suite,
                key,
                speed,
                scenario,
                quantizer,
                case_index,
                float(sample_assignment[case_index]),
                int(participant_assignment[case_index]),
                str(rights_assignment[case_index]),
                band_assignment[case_index],
            )
        )
    return specs


def random_specs(
    cfg: dict[str, Any],
    suite: str,
    count: int,
    sample_hours: float | None = None,
    adversarial: bool = False,
) -> list[SimulationSpec]:
    rng = np.random.default_rng(stable_seed(int(cfg["base_seed"]), suite))
    speeds = list(cfg["extreme_speeds"])
    scenarios = list(cfg["extreme_scenarios"])
    quantizers = list(cfg["quantizers"])
    participants = list(cfg["participants"])
    rights_profiles = list(cfg["rights_profiles"])
    bands = list(cfg["target_progress_bands"])
    specs: list[SimulationSpec] = []
    for case_index in range(int(count)):
        speed = str(rng.choice(speeds))
        scenario = str(rng.choice(scenarios))
        quantizer = str(rng.choice(quantizers))
        n_participants = int(rng.choice(participants))
        rights_profile = str(rng.choice(rights_profiles))
        band = bands[int(rng.integers(0, len(bands)))]
        chosen_sample = sample_hours
        if adversarial and sample_hours is None:
            # Coarser samples are deliberately common in the finite search pool
            # because they expose stale-state failures.
            chosen_sample = float(rng.choice([1.0, 3.0, 6.0, 6.0]))
        key = f"{case_index}:{speed}:{scenario}:{quantizer}:{chosen_sample}"
        spec = _spec_from_key(
            cfg,
            suite,
            key,
            speed,
            scenario,
            quantizer,
            case_index,
            chosen_sample,
            n_participants,
            rights_profile,
            band,
        )
        specs.append(spec)
    return specs


def policy_name(policy: dict[str, Any]) -> str:
    mode = str(policy["mode"])
    if mode.endswith("_always"):
        return mode
    width = str(policy["width_threshold"]).replace(".", "p")
    disagreement = str(policy["disagreement_threshold"]).replace(".", "p")
    return f"fallback_{mode}_w{width}_d{disagreement}"


def candidate_policies(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = [
        {"mode": "median_always"},
        {"mode": "mean_pf_multiscale_always"},
    ]
    fallback = cfg["fallback"]
    for mode in fallback["modes"]:
        for width in fallback["interval_width_ratio_thresholds"]:
            for disagreement in fallback["disagreement_ratio_thresholds"]:
                policies.append(
                    {
                        "mode": str(mode),
                        "width_threshold": float(width),
                        "disagreement_threshold": float(disagreement),
                    }
                )
    for policy in policies:
        policy["name"] = policy_name(policy)
    return policies


def hybrid_output(
    policy: dict[str, Any],
    obs,
    rights: np.ndarray,
    pf: AlgorithmOutput,
    multiscale: AlgorithmOutput,
    amount: AlgorithmOutput,
    capacity_mid_usd: float,
) -> AlgorithmOutput:
    pf_values = pf.b_hat
    multi_values = multiscale.b_hat
    amount_values = amount.b_hat
    mode = str(policy["mode"])
    if mode == "median_always":
        values = np.median(np.stack([pf_values, multi_values, amount_values]), axis=0)
        risk_mask = np.ones_like(values, dtype=bool)
    elif mode == "mean_pf_multiscale_always":
        values = 0.5 * (pf_values + multi_values)
        risk_mask = np.ones_like(values, dtype=bool)
    else:
        if pf.b_lower is None or pf.b_upper is None:
            raise ValueError("Calibrated PF intervals are required by fallback policies")
        scale = np.maximum(capacity_mid_usd * rights[None, :], 1.0)
        width_ratio = (pf.b_upper - pf.b_lower) / scale
        disagreement_ratio = np.abs(pf_values - multi_values) / scale
        risk_mask = (width_ratio > float(policy["width_threshold"])) | (
            disagreement_ratio > float(policy["disagreement_threshold"])
        )
        if mode == "multiscale_on_risk":
            fallback_values = multi_values
        elif mode == "median_on_risk":
            fallback_values = np.median(
                np.stack([pf_values, multi_values, amount_values]), axis=0
            )
        elif mode == "conservative_on_risk":
            fallback_values = np.minimum(pf_values, multi_values)
        else:
            raise ValueError(f"Unknown fallback policy mode: {mode}")
        values = np.where(risk_mask, fallback_values, pf_values)
    values = np.maximum(values, 0.0)
    return AlgorithmOutput(
        algorithm=str(policy["name"]),
        times=obs.times,
        b_hat=values,
        l_hat=obs.c_obs + values,
        diagnostics={
            "mode": mode,
            "risk_activation_fraction": float(risk_mask.mean()),
        },
    )


def run_case(args: tuple[SimulationSpec, dict[str, Any], int, list[dict[str, Any]], str]):
    spec, selected, particle_count, policies, suite = args
    truth = simulate_truth(spec)
    if (
        not np.all(np.isfinite(truth.v))
        or truth.v.min() < spec.capacity_min_usd
        or truth.v.max() > spec.capacity_max_usd
    ):
        raise ValueError(f"Invalid V path in {spec.case_id}")
    if np.any(np.diff(truth.p) < -1e-10):
        raise ValueError(f"Non-monotone resource progress in {spec.case_id}")
    obs = make_observations(truth)
    set_output = recursive_outer_set(
        obs,
        truth.rights,
        capacity_min_usd=spec.capacity_min_usd,
        capacity_max_usd=spec.capacity_max_usd,
    )
    pf_cfg = ParticleFilterConfig(
        particles=int(particle_count),
        latent_stationary_sd=float(
            selected.get("pf_latent_stationary_sd", 0.78)
        ),
        observation_soft_sigma_pp=float(selected["pf_observation_soft_sigma_pp"]),
        timing_dirichlet_alpha=float(selected["pf_timing_dirichlet_alpha"]),
        capacity_min_usd=spec.capacity_min_usd,
        capacity_max_usd=spec.capacity_max_usd,
    )
    pf_raw = particle_filter(
        obs,
        truth.rights,
        seed=(spec.seed + 7919) % (2**32 - 1),
        config=pf_cfg,
    )
    pf = calibrated_particle_interval(
        pf_raw,
        obs,
        deterministic_set=set_output,
        inflation=float(selected["pf_interval_inflation"]),
    )
    legacy_pf = None
    if spec.capacity_min_usd != 1400.0 or spec.capacity_max_usd != 2100.0:
        legacy_cfg = ParticleFilterConfig(
            particles=int(particle_count),
            latent_stationary_sd=0.78,
            observation_soft_sigma_pp=0.15,
            timing_dirichlet_alpha=0.8,
            capacity_min_usd=1400.0,
            capacity_max_usd=2100.0,
        )
        legacy_pf = particle_filter(
            obs,
            truth.rights,
            seed=(spec.seed + 7919) % (2**32 - 1),
            config=legacy_cfg,
        )
        legacy_pf.algorithm = "particle_filter_legacy_bounds"
    pf.algorithm = "particle_filter_mixture"
    amount = amount_proportion(obs, truth.rights)
    multiscale = multiscale_window(
        obs,
        truth.rights,
        widths_pp=selected["multiscale_widths_pp"],
        shrink_pp=float(selected["window_shrink_pp"]),
    )
    outputs = [
        static_allocation(obs, truth.rights),
        amount,
        single_window(
            obs,
            truth.rights,
            width_pp=float(selected["single_width_pp"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        multiscale,
        multiphase_window(
            obs,
            truth.rights,
            width_pp=int(selected["multiphase_width_pp"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        adaptive_phase_extension(
            obs,
            truth.rights,
            widths_pp=selected["adaptive_widths_pp"],
            spread_threshold=float(selected["adaptive_spread_threshold"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        set_output,
        pf,
    ]
    if legacy_pf is not None:
        outputs.append(legacy_pf)
    capacity_mid_usd = 0.5 * (
        spec.capacity_min_usd + spec.capacity_max_usd
    )
    outputs.extend(
        hybrid_output(
            policy,
            obs,
            truth.rights,
            pf,
            multiscale,
            amount,
            capacity_mid_usd,
        )
        for policy in policies
    )

    scale = np.maximum(capacity_mid_usd * truth.rights[None, :], 1.0)
    interval_width_ratio = (pf.b_upper - pf.b_lower) / scale
    disagreement_ratio = np.abs(pf.b_hat - multiscale.b_hat) / scale
    informative = np.r_[
        False,
        (np.diff(obs.z) != 0)
        | (np.abs(np.diff(obs.c_obs, axis=0)).sum(axis=1) > 0.01),
    ]
    common = {
        "suite": suite,
        "pf_mean_interval_width_ratio": float(interval_width_ratio.mean()),
        "pf_max_interval_width_ratio": float(interval_width_ratio.max()),
        "pf_mean_disagreement_ratio": float(disagreement_ratio.mean()),
        "pf_max_disagreement_ratio": float(disagreement_ratio.max()),
        "informative_observation_fraction": float(informative.mean()),
        "v_min": float(truth.v.min()),
        "v_max": float(truth.v.max()),
        "v_max_step_10min": float(np.abs(np.diff(truth.v)).max()),
    }
    rows: list[dict[str, Any]] = []
    for output in outputs:
        row = evaluate_output(truth, obs, output)
        row.update(common)
        row["policy_activation_fraction"] = float(
            output.diagnostics.get("risk_activation_fraction", np.nan)
        )
        rows.append(row)
    metadata = {
        "case_id": spec.case_id,
        "suite": suite,
        "rights": truth.rights.tolist(),
        "target_progress": float(truth.p[-1]),
        "total_dollars": float(truth.c[-1].sum()),
        "num_observations": int(len(obs.times)),
        **truth.metadata,
    }
    return rows, metadata


def run_suite(
    specs: list[SimulationSpec],
    selected: dict[str, Any],
    particle_count: int,
    policies: list[dict[str, Any]],
    suite: str,
    workers: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    jobs = [(spec, selected, particle_count, policies, suite) for spec in specs]
    all_rows: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    completed = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for rows, case_metadata in pool.map(run_case, jobs, chunksize=1):
            all_rows.extend(rows)
            metadata.append(case_metadata)
            completed += 1
            if completed % 25 == 0 or completed == len(jobs):
                print(f"[{suite}] {completed}/{len(jobs)}", flush=True)
    return pd.DataFrame(all_rows), metadata


def algorithm_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    pf = frame[frame["algorithm"] == "particle_filter_mixture"].set_index("case_id")
    for algorithm, group in frame.groupby("algorithm", sort=False):
        paired = group.set_index("case_id").join(
            pf[["mae_usd"]].rename(columns={"mae_usd": "pf_mae"}), how="inner"
        )
        improvement = paired["pf_mae"] - paired["mae_usd"]
        rows.append(
            {
                "algorithm": algorithm,
                "cases": int(len(group)),
                "mean_mae_usd": float(group["mae_usd"].mean()),
                "median_case_mae_usd": float(group["mae_usd"].median()),
                "p95_case_mae_usd": float(group["mae_usd"].quantile(0.95)),
                "p99_case_mae_usd": float(group["mae_usd"].quantile(0.99)),
                "p95_case_max_abs_usd": float(group["max_abs_usd"].quantile(0.95)),
                "p95_case_max_over_usd": float(group["max_over_usd"].quantile(0.95)),
                "observed_max_over_usd": float(group["max_over_usd"].max()),
                "mean_bias_usd": float(group["bias_usd"].mean()),
                "mean_over_usd": float(group["mean_over_usd"].mean()),
                "mean_under_usd": float(group["mean_under_usd"].mean()),
                "mean_interval_coverage": float(group["interval_sample_coverage"].mean()),
                "mean_interval_width_usd": float(group["interval_mean_width_usd"].mean()),
                "mean_improvement_vs_pf_usd": float(improvement.mean()),
                "win_fraction_vs_pf": float((improvement > 1e-9).mean()),
                "mean_policy_activation_fraction": float(group["policy_activation_fraction"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["mean_mae_usd", "p95_case_max_over_usd"])


def stratified_summary(frame: pd.DataFrame, field: str) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for value, group in frame.groupby(field, dropna=False):
        summary = algorithm_summary(group)
        summary.insert(1, field, value)
        rows.append(summary)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def select_fallbacks(
    development: pd.DataFrame,
    policies: list[dict[str, Any]],
    max_mae_penalty: float,
) -> dict[str, Any]:
    summary = algorithm_summary(development)
    policy_map = {str(policy["name"]): policy for policy in policies}
    hybrid = summary[summary["algorithm"].isin(policy_map)].copy()
    best_mean_row = hybrid.sort_values(
        ["mean_mae_usd", "p95_case_max_over_usd"]
    ).iloc[0]
    pf_row = summary[summary["algorithm"] == "particle_filter_mixture"].iloc[0]
    risk_candidates = summary[
        summary["algorithm"].isin(["particle_filter_mixture", *policy_map.keys()])
        & (summary["mean_mae_usd"] <= float(pf_row["mean_mae_usd"]) + max_mae_penalty)
    ].copy()
    best_risk_row = risk_candidates.sort_values(
        ["p95_case_max_over_usd", "mean_mae_usd"]
    ).iloc[0]
    return {
        "best_mean_hybrid": {
            "algorithm": str(best_mean_row["algorithm"]),
            "development_mean_mae_usd": float(best_mean_row["mean_mae_usd"]),
            "policy": policy_map[str(best_mean_row["algorithm"])],
        },
        "best_overallocation_risk": {
            "algorithm": str(best_risk_row["algorithm"]),
            "development_mean_mae_usd": float(best_risk_row["mean_mae_usd"]),
            "development_p95_case_max_over_usd": float(
                best_risk_row["p95_case_max_over_usd"]
            ),
            "policy": policy_map.get(str(best_risk_row["algorithm"])),
        },
        "particle_filter_development": {
            "mean_mae_usd": float(pf_row["mean_mae_usd"]),
            "p95_case_max_over_usd": float(pf_row["p95_case_max_over_usd"]),
        },
        "selection_rule": (
            "Mean fallback minimizes development mean trajectory MAE among observable "
            "hybrids. Risk fallback minimizes development P95 case maximum over-allocation "
            f"subject to mean MAE no more than {max_mae_penalty:.2f} USD above PF."
        ),
    }


def selected_policy_list(selection: dict[str, Any]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    mean_policy = selection["best_mean_hybrid"]["policy"]
    selected[str(mean_policy["name"])] = mean_policy
    risk_policy = selection["best_overallocation_risk"].get("policy")
    if risk_policy is not None:
        selected[str(risk_policy["name"])] = risk_policy
    return list(selected.values())


def signal_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    pf = frame[frame["algorithm"] == "particle_filter_mixture"].copy()
    result: dict[str, Any] = {"cases": int(len(pf))}
    for signal in [
        "pf_mean_interval_width_ratio",
        "pf_max_interval_width_ratio",
        "pf_mean_disagreement_ratio",
        "pf_max_disagreement_ratio",
        "informative_observation_fraction",
    ]:
        for target in ["mae_usd", "max_over_usd", "max_abs_usd"]:
            correlation, pvalue = spearmanr(pf[signal], pf[target])
            result[f"{signal}__{target}"] = {
                "spearman": float(correlation),
                "pvalue": float(pvalue),
            }
    return result


def complementarity_analysis(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    pivot = frame.pivot(index="case_id", columns="algorithm", values="mae_usd")
    winner = pivot.idxmin(axis=1)
    counts = winner.value_counts().rename_axis("algorithm").reset_index(name="case_wins")
    counts["case_win_fraction"] = counts["case_wins"] / len(pivot)
    pf_values = pivot["particle_filter_mixture"]
    oracle = pivot.min(axis=1)
    potential = {
        "cases": int(len(pivot)),
        "particle_filter_mean_mae_usd": float(pf_values.mean()),
        "case_oracle_mean_mae_usd": float(oracle.mean()),
        "unrealizable_case_oracle_improvement_usd": float((pf_values - oracle).mean()),
        "warning": "The case oracle uses hidden truth and is not a deployable fallback.",
    }
    threshold = float(pf_values.quantile(0.90))
    hard_ids = pf_values[pf_values >= threshold].index
    hard = frame[frame["case_id"].isin(hard_ids)].copy()
    hard_summary = algorithm_summary(hard)
    hard_summary.insert(1, "pf_hard_case_threshold_usd", threshold)
    return counts, potential, hard_summary


def worst_cases(frame: pd.DataFrame, per_algorithm: int = 10) -> pd.DataFrame:
    columns = [
        "case_id",
        "algorithm",
        "speed",
        "scenario",
        "sample_hours",
        "n_participants",
        "rights_profile",
        "quantizer",
        "mae_usd",
        "p95_abs_usd",
        "max_abs_usd",
        "max_over_usd",
        "bias_usd",
        "pf_mean_interval_width_ratio",
        "pf_mean_disagreement_ratio",
        "informative_observation_fraction",
    ]
    rows = [
        group.nlargest(per_algorithm, "max_over_usd")[columns]
        for _, group in frame.groupby("algorithm")
    ]
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default=str(ROOT / "config" / "extreme_study.yaml")
    )
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a tiny non-reportable matrix to validate the full pipeline.",
    )
    args = parser.parse_args()
    cfg = load_yaml(Path(args.config))
    with (ROOT / cfg["selected_parameters_file"]).open("r", encoding="utf-8") as handle:
        selected = json.load(handle)
    particle_count = int(cfg["particle_count"])
    policies = candidate_policies(cfg)

    development_specs = structured_specs(
        cfg, "extreme_dev", int(cfg["development_replicates"])
    )
    test_specs = structured_specs(cfg, "extreme_test", int(cfg["test_replicates"]))
    high_frequency_specs = random_specs(
        cfg,
        "extreme_10min",
        int(cfg["high_frequency_cases"]),
        float(cfg["high_frequency_sample_hours"]),
    )
    adversarial_specs = random_specs(
        cfg,
        "extreme_search_pool",
        int(cfg["adversarial_pool_cases"]),
        adversarial=True,
    )
    if args.smoke:
        development_specs = development_specs[:8]
        test_specs = test_specs[:16]
        high_frequency_specs = high_frequency_specs[:4]
        adversarial_specs = adversarial_specs[:8]
        particle_count = min(64, particle_count)
        policies = policies[:5]

    results = ROOT / "results"
    development, development_metadata = run_suite(
        development_specs,
        selected,
        particle_count,
        policies,
        "development",
        args.workers,
    )
    selection = select_fallbacks(
        development,
        policies,
        float(cfg["fallback"]["max_mean_mae_penalty_usd"]),
    )
    selected_policies = selected_policy_list(selection)
    test, test_metadata = run_suite(
        test_specs,
        selected,
        particle_count,
        selected_policies,
        "test",
        args.workers,
    )
    high_frequency, high_frequency_metadata = run_suite(
        high_frequency_specs,
        selected,
        particle_count,
        selected_policies,
        "high_frequency",
        args.workers,
    )
    adversarial, adversarial_metadata = run_suite(
        adversarial_specs,
        selected,
        particle_count,
        selected_policies,
        "adversarial_pool",
        args.workers,
    )

    configured_prefix = str(cfg.get("results_prefix", "extreme"))
    prefix = f"{configured_prefix}_smoke" if args.smoke else configured_prefix
    development.to_csv(
        results / f"{prefix}_development_metrics.csv.gz", index=False, compression="gzip"
    )
    test.to_csv(results / f"{prefix}_test_metrics.csv.gz", index=False, compression="gzip")
    high_frequency.to_csv(
        results / f"{prefix}_high_frequency_metrics.csv.gz", index=False, compression="gzip"
    )
    adversarial.to_csv(
        results / f"{prefix}_adversarial_pool_metrics.csv.gz", index=False, compression="gzip"
    )

    development_summary = algorithm_summary(development)
    test_summary = algorithm_summary(test)
    high_frequency_summary = algorithm_summary(high_frequency)
    adversarial_summary = algorithm_summary(adversarial)
    development_summary.to_csv(results / f"{prefix}_development_summary.csv", index=False)
    test_summary.to_csv(results / f"{prefix}_test_summary.csv", index=False)
    high_frequency_summary.to_csv(
        results / f"{prefix}_high_frequency_summary.csv", index=False
    )
    adversarial_summary.to_csv(
        results / f"{prefix}_adversarial_pool_summary.csv", index=False
    )
    for field in ["speed", "scenario", "sample_hours", "n_participants", "rights_profile", "quantizer"]:
        stratified_summary(test, field).to_csv(
            results / f"{prefix}_test_by_{field}.csv", index=False
        )

    winner_counts, oracle_potential, hard_summary = complementarity_analysis(test)
    winner_counts.to_csv(results / f"{prefix}_case_winners.csv", index=False)
    hard_summary.to_csv(results / f"{prefix}_pf_hard_cases_summary.csv", index=False)
    worst_cases(adversarial).to_csv(
        results / f"{prefix}_adversarial_worst_cases.csv", index=False
    )
    save_json(results / f"{prefix}_selected_fallbacks.json", selection)
    save_json(results / f"{prefix}_pf_signal_analysis.json", signal_analysis(test))
    save_json(results / f"{prefix}_complementarity.json", oracle_potential)
    save_json(
        results / f"{prefix}_case_metadata.json",
        {
            "development": development_metadata,
            "test": test_metadata,
            "high_frequency": high_frequency_metadata,
            "adversarial_pool": adversarial_metadata,
        },
    )
    experiment_metadata = {
        "reportable": not args.smoke,
        "development_cases": len(development_specs),
        "test_cases": len(test_specs),
        "high_frequency_cases": len(high_frequency_specs),
        "adversarial_pool_cases": len(adversarial_specs),
        "base_algorithms": int(
            test[~test["algorithm"].str.startswith(("fallback_", "median_", "mean_"))][
                "algorithm"
            ].nunique()
        ),
        "development_fallback_candidates": len(policies),
        "selected_fallbacks": selected_policies,
        "particle_count": particle_count,
        "workers": int(args.workers),
        "config": cfg,
        "finite_sample_warning": (
            "Adversarial-pool maxima are observed finite-pool maxima, not mathematical bounds."
        ),
    }
    save_json(results / f"{prefix}_experiment_metadata.json", experiment_metadata)
    print(json.dumps(experiment_metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
