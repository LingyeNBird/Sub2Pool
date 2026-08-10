#!/usr/bin/env python3
"""Evaluate one-sided shadow filters and boundary-expansion triggers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.metrics import evaluate_output  # noqa: E402
from dynamic_limit.models import AlgorithmOutput, SimulationSpec  # noqa: E402
from dynamic_limit.observation import make_observations  # noqa: E402
from dynamic_limit.particle_filter import ParticleFilterConfig, particle_filter  # noqa: E402

BASE_MIN = 1400.0
BASE_MAX = 4000.0
UPPER_SHADOW_MAX = 6000.0
LOWER_SHADOW_MIN = 700.0
PARTICLES = 480
PARAMETERS = {
    "latent_stationary_sd": 0.60,
    "observation_soft_sigma_pp": 0.05,
    "timing_dirichlet_alpha": 0.8,
}
REGIMES = {
    "in_range": (1400.0, 4000.0),
    "high_mild": (1400.0, 4500.0),
    "high_severe": (1400.0, 5200.0),
    "low_mild": (1100.0, 4000.0),
    "low_severe": (800.0, 4000.0),
}
MASS_THRESHOLDS = (0.10, 0.20, 0.35, 0.50)
CONSECUTIVE_HITS = (1, 2, 3)
RESIDUAL_TOLERANCES = (-1.00, 0.00, 0.10, 0.25)


def stable_seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little")


def make_specs(cases_per_regime: int) -> list[tuple[str, str, SimulationSpec]]:
    speeds = (
        "slow",
        "medium",
        "fast",
        "extreme_late_reversal",
        "extreme_narrow_excursions",
        "extreme_monotone_full",
    )
    scenarios = (
        "uniform",
        "front_loaded",
        "back_loaded",
        "multi_burst",
        "one_steady_others_burst",
        "extreme_silent_then_burst",
        "extreme_first_day_whale",
        "extreme_sample_edge_bursts",
    )
    specs: list[tuple[str, str, SimulationSpec]] = []
    for regime, (capacity_min, capacity_max) in REGIMES.items():
        for index in range(cases_per_regime):
            split = "development" if index < cases_per_regime // 2 else "test"
            rng = np.random.default_rng(stable_seed(f"{regime}:{index}"))
            case_id = f"adaptive_bounds_{regime}_{index:03d}"
            specs.append(
                (
                    regime,
                    split,
                    SimulationSpec(
                        case_id=case_id,
                        seed=stable_seed(case_id),
                        speed=str(rng.choice(speeds)),
                        n_participants=int(rng.choice([2, 4, 6])),
                        rights_profile=str(
                            rng.choice(
                                ["balanced", "moderate_skew", "extreme_skew", "random"]
                            )
                        ),
                        scenario=str(rng.choice(scenarios)),
                        sample_hours=float(rng.choice([1.0, 3.0, 6.0])),
                        quantizer=str(rng.choice(["floor", "nearest", "ceil"])),
                        horizon_hours=168.0,
                        dt_hours=1.0 / 6.0,
                        target_progress_low=float(rng.choice([8.0, 45.0, 88.0])),
                        target_progress_high=float(rng.choice([25.0, 70.0, 99.0])),
                        capacity_min_usd=capacity_min,
                        capacity_max_usd=capacity_max,
                    ),
                )
            )
            spec = specs[-1][2]
            if spec.target_progress_high <= spec.target_progress_low:
                specs[-1] = (
                    regime,
                    split,
                    SimulationSpec(
                        **{
                            **asdict(spec),
                            "target_progress_low": 45.0,
                            "target_progress_high": 70.0,
                        }
                    ),
                )
    return specs


def config(capacity_min: float, capacity_max: float) -> ParticleFilterConfig:
    return ParticleFilterConfig(
        particles=PARTICLES,
        capacity_min_usd=capacity_min,
        capacity_max_usd=capacity_max,
        **PARAMETERS,
    )


def signed_residuals(output: AlgorithmOutput, displayed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    progress = output.q_hat.sum(axis=1)
    upper = np.maximum(progress - np.minimum(displayed + 1.0, 100.0), 0.0)
    lower = np.maximum(np.maximum(displayed - 1.0, 0.0) - progress, 0.0)
    return upper, lower


def first_consecutive(signal: np.ndarray, count: int) -> int | None:
    run = 0
    for index, active in enumerate(signal):
        run = run + 1 if active else 0
        if run >= count:
            return index
    return None


def adaptive_output(
    base: AlgorithmOutput,
    upper_shadow: AlgorithmOutput,
    lower_shadow: AlgorithmOutput,
    direction: str | None,
    promotion_index: int | None,
    obs,
) -> AlgorithmOutput:
    values = base.b_hat.copy()
    if promotion_index is not None and direction is not None:
        shadow = upper_shadow if direction == "upper" else lower_shadow
        values[promotion_index:] = shadow.b_hat[promotion_index:]
    return AlgorithmOutput(
        algorithm="adaptive_shadow",
        times=obs.times,
        b_hat=values,
        l_hat=obs.c_obs + values,
    )


def run_case(job: tuple[str, str, SimulationSpec]) -> tuple[list[dict], dict]:
    regime, split, spec = job
    truth = simulate_truth(spec)
    obs = make_observations(truth)
    seed = (spec.seed + 7919) % (2**32 - 1)

    started = perf_counter()
    base = particle_filter(obs, truth.rights, seed=seed, config=config(BASE_MIN, BASE_MAX))
    base_seconds = perf_counter() - started
    started = perf_counter()
    upper_shadow = particle_filter(
        obs,
        truth.rights,
        seed=seed,
        config=config(BASE_MIN, UPPER_SHADOW_MAX),
    )
    upper_seconds = perf_counter() - started
    started = perf_counter()
    lower_shadow = particle_filter(
        obs,
        truth.rights,
        seed=seed,
        config=config(LOWER_SHADOW_MIN, BASE_MAX),
    )
    lower_seconds = perf_counter() - started

    sampled_capacity = truth.v[obs.sample_idx]
    upper_needed = sampled_capacity > BASE_MAX + 50.0
    lower_needed = sampled_capacity < BASE_MIN - 50.0
    first_upper_needed = int(np.flatnonzero(upper_needed)[0]) if upper_needed.any() else None
    first_lower_needed = int(np.flatnonzero(lower_needed)[0]) if lower_needed.any() else None
    needed_direction = None
    first_needed = None
    if first_upper_needed is not None and (
        first_lower_needed is None or first_upper_needed <= first_lower_needed
    ):
        needed_direction, first_needed = "upper", first_upper_needed
    elif first_lower_needed is not None:
        needed_direction, first_needed = "lower", first_lower_needed

    upper_residual, lower_residual = signed_residuals(base, obs.z)
    upper_observable = upper_needed & (upper_residual > 0.10)
    lower_observable = lower_needed & (lower_residual > 0.10)
    first_upper_observable = (
        int(np.flatnonzero(upper_observable)[0])
        if upper_observable.any()
        else None
    )
    first_lower_observable = (
        int(np.flatnonzero(lower_observable)[0])
        if lower_observable.any()
        else None
    )
    observable_direction = None
    first_observable = None
    if first_upper_observable is not None and (
        first_lower_observable is None
        or first_upper_observable <= first_lower_observable
    ):
        observable_direction, first_observable = (
            "upper",
            first_upper_observable,
        )
    elif first_lower_observable is not None:
        observable_direction, first_observable = (
            "lower",
            first_lower_observable,
        )
    upper_mass = base.diagnostics["upper_boundary_mass"]
    lower_mass = base.diagnostics["lower_boundary_mass"]
    base_metric = evaluate_output(truth, obs, base)
    rows: list[dict] = []
    for mass_threshold in MASS_THRESHOLDS:
        for hits in CONSECUTIVE_HITS:
            for residual_tolerance in RESIDUAL_TOLERANCES:
                upper_signal = (upper_mass >= mass_threshold) & (
                    upper_residual > residual_tolerance
                ) & (upper_shadow.v_hat > BASE_MAX + 25.0)
                lower_signal = (lower_mass >= mass_threshold) & (
                    lower_residual > residual_tolerance
                ) & (lower_shadow.v_hat < BASE_MIN - 25.0)
                upper_promotion = first_consecutive(upper_signal, hits)
                lower_promotion = first_consecutive(lower_signal, hits)
                if upper_promotion is not None and (
                    lower_promotion is None or upper_promotion <= lower_promotion
                ):
                    direction, promotion = "upper", upper_promotion
                elif lower_promotion is not None:
                    direction, promotion = "lower", lower_promotion
                else:
                    direction, promotion = None, None

                output = adaptive_output(
                    base,
                    upper_shadow,
                    lower_shadow,
                    direction,
                    promotion,
                    obs,
                )
                metric = evaluate_output(truth, obs, output)
                correctly_detected = (
                    needed_direction is not None
                    and direction == needed_direction
                    and promotion is not None
                )
                observable_detected = (
                    observable_direction is not None
                    and direction == observable_direction
                    and promotion is not None
                )
                delay_hours = None
                if correctly_detected and first_needed is not None:
                    delay_hours = max(
                        0.0,
                        float(obs.times[promotion] - obs.times[first_needed]),
                    )
                observable_delay_hours = None
                if observable_detected and first_observable is not None:
                    observable_delay_hours = max(
                        0.0,
                        float(
                            obs.times[promotion]
                            - obs.times[first_observable]
                        ),
                    )
                observation_count = len(obs.times)
                estimated_compute_factor = (
                    1.0
                    if promotion is None
                    else 1.0 + (promotion + 1) / observation_count
                )
                rows.append(
                    {
                        "case_id": spec.case_id,
                        "regime": regime,
                        "split": split,
                        "mass_threshold": mass_threshold,
                        "consecutive_hits": hits,
                        "residual_tolerance_pp": residual_tolerance,
                        "needed_direction": needed_direction,
                        "promoted_direction": direction,
                        "promotion_index": promotion,
                        "correctly_detected": correctly_detected,
                        "observable_direction": observable_direction,
                        "observable_detected": observable_detected,
                        "false_expansion": needed_direction is None and direction is not None,
                        "wrong_direction": direction is not None
                        and needed_direction is not None
                        and direction != needed_direction,
                        "delay_hours": delay_hours,
                        "observable_delay_hours": observable_delay_hours,
                        "mae_usd": metric["mae_usd"],
                        "base_mae_usd": base_metric["mae_usd"],
                        "estimated_compute_factor": estimated_compute_factor,
                    }
                )
    timing = {
        "case_id": spec.case_id,
        "regime": regime,
        "split": split,
        "observations": len(obs.times),
        "base_seconds": base_seconds,
        "upper_shadow_seconds": upper_seconds,
        "lower_shadow_seconds": lower_seconds,
        "upper_needed": bool(upper_needed.any()),
        "lower_needed": bool(lower_needed.any()),
    }
    return rows, timing


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["mass_threshold", "consecutive_hits", "residual_tolerance_pp"]
    rows = []
    for values, group in frame.groupby(keys):
        needed = group[group["needed_direction"].notna()]
        in_range = group[group["needed_direction"].isna()]
        detected = needed[needed["correctly_detected"]]
        observable = group[group["observable_direction"].notna()]
        observable_detected = observable[observable["observable_detected"]]
        rows.append(
            {
                **dict(zip(keys, values)),
                "cases": len(group),
                "needed_cases": len(needed),
                "detection_rate": float(needed["correctly_detected"].mean())
                if len(needed)
                else 0.0,
                "observable_cases": len(observable),
                "observable_detection_rate": float(
                    observable["observable_detected"].mean()
                )
                if len(observable)
                else 0.0,
                "false_expansion_rate": float(in_range["false_expansion"].mean())
                if len(in_range)
                else 0.0,
                "wrong_direction_rate": float(needed["wrong_direction"].mean())
                if len(needed)
                else 0.0,
                "mean_delay_hours": float(detected["delay_hours"].mean())
                if len(detected)
                else None,
                "p95_delay_hours": float(detected["delay_hours"].quantile(0.95))
                if len(detected)
                else None,
                "mean_observable_delay_hours": float(
                    observable_detected["observable_delay_hours"].mean()
                )
                if len(observable_detected)
                else None,
                "p95_observable_delay_hours": float(
                    observable_detected[
                        "observable_delay_hours"
                    ].quantile(0.95)
                )
                if len(observable_detected)
                else None,
                "mean_mae_usd": float(group["mae_usd"].mean()),
                "mean_base_mae_usd": float(group["base_mae_usd"].mean()),
                "mean_compute_factor": float(group["estimated_compute_factor"].mean()),
                "promotion_rate": float(group["promoted_direction"].notna().mean()),
            }
        )
    return pd.DataFrame(rows)


def choose_candidate(summary: pd.DataFrame) -> pd.Series:
    eligible = summary[summary["false_expansion_rate"] <= 0.02]
    if eligible.empty:
        eligible = summary[
            summary["false_expansion_rate"]
            == summary["false_expansion_rate"].min()
        ]
    return eligible.sort_values(
        [
            "observable_detection_rate",
            "detection_rate",
            "wrong_direction_rate",
            "p95_observable_delay_hours",
            "mean_mae_usd",
        ],
        ascending=[False, False, True, True, True],
    ).iloc[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-per-regime", type=int, default=80)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()

    jobs = make_specs(args.cases_per_regime)
    all_rows: list[dict] = []
    timings: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, (rows, timing) in enumerate(pool.map(run_case, jobs, chunksize=1), 1):
            all_rows.extend(rows)
            timings.append(timing)
            if index % 50 == 0 or index == len(jobs):
                print(f"{index}/{len(jobs)}", flush=True)

    results = ROOT / "results"
    raw = pd.DataFrame(all_rows)
    timing_frame = pd.DataFrame(timings)
    raw.to_csv(results / "adaptive_boundary_raw.csv.gz", index=False, compression="gzip")
    timing_frame.to_csv(results / "adaptive_boundary_timing.csv", index=False)

    development = summarize(raw[raw["split"] == "development"])
    selected = choose_candidate(development)
    selector = (
        (raw["mass_threshold"] == selected["mass_threshold"])
        & (raw["consecutive_hits"] == selected["consecutive_hits"])
        & (raw["residual_tolerance_pp"] == selected["residual_tolerance_pp"])
    )
    frozen = raw[selector & (raw["split"] == "test")]
    test_summary = summarize(frozen).iloc[0].to_dict()
    regime_rows = []
    for regime, group in frozen.groupby("regime"):
        row = summarize(group).iloc[0].to_dict()
        row["regime"] = regime
        regime_rows.append(row)
    by_regime = pd.DataFrame(regime_rows)
    development.to_csv(results / "adaptive_boundary_development_summary.csv", index=False)
    by_regime.to_csv(results / "adaptive_boundary_test_by_regime.csv", index=False)

    payload = {
        "standard_range_usd": [BASE_MIN, BASE_MAX],
        "upper_shadow_range_usd": [BASE_MIN, UPPER_SHADOW_MAX],
        "lower_shadow_range_usd": [LOWER_SHADOW_MIN, BASE_MAX],
        "particles_per_filter": PARTICLES,
        "cases": len(jobs),
        "development_cases": len(jobs) // 2,
        "test_cases": len(jobs) // 2,
        "selected_condition": {
            "boundary_band_fraction": 0.05,
            "mass_threshold": float(selected["mass_threshold"]),
            "consecutive_hits": int(selected["consecutive_hits"]),
            "residual_tolerance_pp": float(selected["residual_tolerance_pp"]),
            "shadow_median_must_cross_boundary_usd": 25.0,
        },
        "development_selection_rule": (
            "Among candidates with <=2% false expansions, maximize detection "
            "after an observable out-of-range contradiction, then all-excursion "
            "detection; minimize wrong direction, observable p95 delay, and MAE."
        ),
        "frozen_test": test_summary,
        "timing": {
            "mean_base_ms": float(timing_frame["base_seconds"].mean() * 1000.0),
            "mean_upper_shadow_ms": float(
                timing_frame["upper_shadow_seconds"].mean() * 1000.0
            ),
            "mean_lower_shadow_ms": float(
                timing_frame["lower_shadow_seconds"].mean() * 1000.0
            ),
            "always_one_shadow_measured_factor": float(
                (
                    timing_frame["base_seconds"]
                    + 0.5
                    * (
                        timing_frame["upper_shadow_seconds"]
                        + timing_frame["lower_shadow_seconds"]
                    )
                ).sum()
                / timing_frame["base_seconds"].sum()
            ),
            "selected_on_demand_estimated_factor": float(
                test_summary["mean_compute_factor"]
            ),
        },
        "test_by_regime": by_regime.to_dict(orient="records"),
        "warning": (
            "Finite synthetic evidence; expansion should remain observable and replayable. "
            "The on-demand compute factor counts particle-update steps and excludes database I/O."
        ),
    }
    output = results / "adaptive_boundary_study.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
