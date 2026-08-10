#!/usr/bin/env python3
"""Compare one-shot and staged boundary-expansion magnitudes.

The trigger is fixed to the previously selected direct dual-evidence rule. This
study isolates what range should replace the standard 1400--4000 range after
that trigger fires.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "_support" / "engineering"))
sys.path.insert(0, str(ROOT / "scripts"))

from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.models import SimulationSpec  # noqa: E402
from dynamic_limit.observation import make_observations  # noqa: E402
from dynamic_limit.particle_filter import particle_filter  # noqa: E402
from compare_expansion_strategies import (  # noqa: E402
    aggregate_error,
    choose_direction,
    error_arrays,
    hybrid_error,
    signed_residuals,
    truth_direction,
)
from study_adaptive_bounds import (  # noqa: E402
    BASE_MAX,
    BASE_MIN,
    config,
    make_specs,
    stable_seed,
)

UPPER_TARGETS = (4250.0, 4500.0, 4750.0, 5000.0, 5500.0, 6000.0, 7000.0, 8000.0, 10000.0, 12000.0, 20000.0)
LOWER_TARGETS = (1300.0, 1200.0, 1100.0, 1000.0, 900.0, 800.0, 700.0, 500.0, 250.0, 50.0)
TRIGGER_MASS = 0.10
TRIGGER_RESIDUAL_PP = 0.05
CURRENT_UPPER = 6000.0
CURRENT_LOWER = 700.0
TAIL_REGIMES = {
    "tail_high_6500": (1400.0, 6500.0),
    "tail_high_9000": (1400.0, 9000.0),
    "tail_low_500": (500.0, 4000.0),
    "tail_low_200": (200.0, 4000.0),
}
SPEEDS = (
    "slow",
    "medium",
    "fast",
    "extreme_late_reversal",
    "extreme_narrow_excursions",
    "extreme_monotone_full",
)
SCENARIOS = (
    "uniform",
    "front_loaded",
    "back_loaded",
    "multi_burst",
    "one_steady_others_burst",
    "extreme_silent_then_burst",
    "extreme_first_day_whale",
    "extreme_sample_edge_bursts",
)


@dataclass(frozen=True)
class StagedPolicy:
    name: str
    upper_targets: tuple[float, ...]
    lower_targets: tuple[float, ...]


STAGED_POLICIES = (
    StagedPolicy("staged_fine", UPPER_TARGETS, LOWER_TARGETS),
    StagedPolicy(
        "staged_medium",
        (4500.0, 5000.0, 5500.0, 6000.0, 7000.0, 8000.0, 10000.0, 12000.0, 20000.0),
        (1200.0, 1000.0, 900.0, 800.0, 700.0, 500.0, 250.0, 50.0),
    ),
    StagedPolicy(
        "staged_coarse",
        (5000.0, 6000.0, 8000.0, 12000.0, 20000.0),
        (1000.0, 700.0, 250.0, 50.0),
    ),
    StagedPolicy(
        "staged_ratio_like",
        (5000.0, 6000.0, 7000.0, 10000.0, 20000.0),
        (1100.0, 900.0, 700.0, 500.0, 250.0, 50.0),
    ),
    StagedPolicy(
        "staged_very_coarse",
        (6000.0, 10000.0, 20000.0),
        (700.0, 250.0, 50.0),
    ),
)


def target_id(upper: float, lower: float) -> str:
    digest = hashlib.sha256(f"{upper}:{lower}".encode("utf-8")).hexdigest()[:10]
    return f"oneshot_{digest}"


def make_tail_specs(
    cases_per_regime: int,
    replication_salt: str = "",
) -> list[tuple[str, str, SimulationSpec]]:
    jobs: list[tuple[str, str, SimulationSpec]] = []
    for regime, (capacity_min, capacity_max) in TAIL_REGIMES.items():
        for index in range(cases_per_regime):
            rng = np.random.default_rng(
                stable_seed(f"range_tail:{replication_salt}:{regime}:{index}")
            )
            case_id = f"expansion_range_{replication_salt}_{regime}_{index:03d}"
            spec = SimulationSpec(
                case_id=case_id,
                seed=stable_seed(case_id),
                speed=str(rng.choice(SPEEDS)),
                n_participants=int(rng.choice([2, 4, 6])),
                rights_profile=str(rng.choice(["balanced", "moderate_skew", "extreme_skew", "random"])),
                scenario=str(rng.choice(SCENARIOS)),
                sample_hours=float(rng.choice([1.0, 3.0, 6.0])),
                quantizer=str(rng.choice(["floor", "nearest", "ceil"])),
                horizon_hours=168.0,
                dt_hours=1.0 / 6.0,
                target_progress_low=float(rng.choice([8.0, 45.0, 88.0])),
                target_progress_high=float(rng.choice([25.0, 70.0, 99.0])),
                capacity_min_usd=capacity_min,
                capacity_max_usd=capacity_max,
            )
            if spec.target_progress_high <= spec.target_progress_low:
                spec = SimulationSpec(
                    **{
                        **asdict(spec),
                        "target_progress_low": 45.0,
                        "target_progress_high": 70.0,
                    }
                )
            jobs.append((regime, "tail", spec))
    return jobs


def reseed_specs(
    jobs: list[tuple[str, str, SimulationSpec]],
    replication_salt: str,
) -> list[tuple[str, str, SimulationSpec]]:
    if not replication_salt:
        return jobs
    reseeded = []
    for regime, split, spec in jobs:
        case_id = f"{spec.case_id}_{replication_salt}"
        reseeded.append(
            (
                regime,
                split,
                SimulationSpec(
                    **{
                        **asdict(spec),
                        "case_id": case_id,
                        "seed": stable_seed(case_id),
                    }
                ),
            )
        )
    return reseeded


def first_true_at_or_after(signal: np.ndarray, start: int) -> int | None:
    indices = np.flatnonzero(signal & (np.arange(len(signal)) >= start))
    return int(indices[0]) if len(indices) else None


def trigger_for_output(output, displayed: np.ndarray, direction: str) -> np.ndarray:
    upper_residual, lower_residual = signed_residuals(output, displayed)
    if direction == "upper":
        mass = np.asarray(output.diagnostics["upper_boundary_mass"])
        return (mass >= TRIGGER_MASS) & (upper_residual > TRIGGER_RESIDUAL_PP)
    mass = np.asarray(output.diagnostics["lower_boundary_mass"])
    return (mass >= TRIGGER_MASS) & (lower_residual > TRIGGER_RESIDUAL_PP)


def piecewise_metric(
    base_error: np.ndarray,
    base_capacity_error: np.ndarray,
    transitions: list[tuple[int, np.ndarray, np.ndarray]],
    obs,
) -> dict[str, float]:
    error = base_error.copy()
    capacity_error = base_capacity_error.copy()
    for promotion_index, expanded_error, expanded_capacity_error in transitions:
        switch_time_index = int(obs.sample_idx[promotion_index])
        error[switch_time_index:] = expanded_error[switch_time_index:]
        capacity_error[promotion_index:] = expanded_capacity_error[promotion_index:]
    return aggregate_error(error, capacity_error)


def staged_metric(
    direction: str | None,
    promotion: int | None,
    policy: StagedPolicy,
    outputs: dict[float, Any],
    errors: dict[float, tuple[np.ndarray, np.ndarray]],
    base_error: np.ndarray,
    base_capacity_error: np.ndarray,
    obs,
) -> tuple[dict[str, float], int, float | None, tuple[int, ...]]:
    if direction is None or promotion is None:
        return aggregate_error(base_error, base_capacity_error), 0, None, ()
    targets = policy.upper_targets if direction == "upper" else policy.lower_targets
    transitions: list[tuple[int, np.ndarray, np.ndarray]] = []
    switch_indices: list[int] = []
    current_promotion = promotion
    final_target: float | None = None
    for target in targets:
        expanded_error, expanded_capacity_error = errors[target]
        transitions.append((current_promotion, expanded_error, expanded_capacity_error))
        switch_indices.append(current_promotion)
        final_target = target
        next_signal = trigger_for_output(outputs[target], obs.z, direction)
        next_promotion = first_true_at_or_after(next_signal, current_promotion)
        if next_promotion is None:
            break
        current_promotion = next_promotion
    return (
        piecewise_metric(base_error, base_capacity_error, transitions, obs),
        len(transitions),
        final_target,
        tuple(switch_indices),
    )


def initial_trigger(base, displayed: np.ndarray) -> tuple[str | None, int | None]:
    upper_residual, lower_residual = signed_residuals(base, displayed)
    upper_mass = np.asarray(base.diagnostics["upper_boundary_mass"])
    lower_mass = np.asarray(base.diagnostics["lower_boundary_mass"])
    upper_score = np.minimum(upper_mass - TRIGGER_MASS, upper_residual - TRIGGER_RESIDUAL_PP)
    lower_score = np.minimum(lower_mass - TRIGGER_MASS, lower_residual - TRIGGER_RESIDUAL_PP)
    return choose_direction(
        upper_score >= 0.0,
        lower_score >= 0.0,
        1,
        upper_score,
        lower_score,
    )


def run_case(job: tuple[str, str, SimulationSpec]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    regime, split, spec = job
    truth = simulate_truth(spec)
    obs = make_observations(truth)
    seed = (spec.seed + 7919) % (2**32 - 1)

    started = perf_counter()
    base = particle_filter(obs, truth.rights, seed=seed, config=config(BASE_MIN, BASE_MAX))
    base_seconds = perf_counter() - started
    base_error, base_capacity_error = error_arrays(truth, obs, base)
    base_metric = aggregate_error(base_error, base_capacity_error)
    direction, promotion = initial_trigger(base, obs.z)
    needed_direction, _, _, _ = truth_direction(truth, obs)

    upper_outputs: dict[float, Any] = {}
    upper_errors: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    lower_outputs: dict[float, Any] = {}
    lower_errors: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    filter_seconds = 0.0
    for target in UPPER_TARGETS:
        started = perf_counter()
        output = particle_filter(obs, truth.rights, seed=seed, config=config(BASE_MIN, target))
        filter_seconds += perf_counter() - started
        upper_outputs[target] = output
        upper_errors[target] = error_arrays(truth, obs, output)
    for target in LOWER_TARGETS:
        started = perf_counter()
        output = particle_filter(obs, truth.rights, seed=seed, config=config(target, BASE_MAX))
        filter_seconds += perf_counter() - started
        lower_outputs[target] = output
        lower_errors[target] = error_arrays(truth, obs, output)

    started = perf_counter()
    always_wide = particle_filter(
        obs,
        truth.rights,
        seed=seed,
        config=config(min(LOWER_TARGETS), max(UPPER_TARGETS)),
    )
    wide_seconds = perf_counter() - started
    wide_error, wide_capacity_error = error_arrays(truth, obs, always_wide)
    wide_metric = aggregate_error(wide_error, wide_capacity_error)

    rows: list[dict[str, Any]] = []
    for upper_target in UPPER_TARGETS:
        for lower_target in LOWER_TARGETS:
            if direction == "upper" and promotion is not None:
                metric = hybrid_error(
                    base_error,
                    upper_errors[upper_target][0],
                    base_capacity_error,
                    upper_errors[upper_target][1],
                    obs,
                    promotion,
                )
                final_target = upper_target
            elif direction == "lower" and promotion is not None:
                metric = hybrid_error(
                    base_error,
                    lower_errors[lower_target][0],
                    base_capacity_error,
                    lower_errors[lower_target][1],
                    obs,
                    promotion,
                )
                final_target = lower_target
            else:
                metric = base_metric
                final_target = None
            rows.append(
                {
                    "candidate_id": target_id(upper_target, lower_target),
                    "family": "one_shot",
                    "upper_target_usd": upper_target,
                    "lower_target_usd": lower_target,
                    "case_id": spec.case_id,
                    "regime": regime,
                    "split": split,
                    "promoted_direction": direction,
                    "promotion_index": promotion,
                    "needed_direction": needed_direction,
                    "false_expansion": needed_direction is None and direction is not None,
                    "wrong_direction": direction is not None and needed_direction is not None and direction != needed_direction,
                    "expansion_count": int(direction is not None),
                    "final_target_usd": final_target,
                    "switch_indices": "" if promotion is None else str(promotion),
                    **metric,
                }
            )

    for policy in STAGED_POLICIES:
        outputs = upper_outputs if direction == "upper" else lower_outputs
        errors = upper_errors if direction == "upper" else lower_errors
        metric, count, final_target, switches = staged_metric(
            direction,
            promotion,
            policy,
            outputs,
            errors,
            base_error,
            base_capacity_error,
            obs,
        )
        rows.append(
            {
                "candidate_id": policy.name,
                "family": "staged",
                "upper_target_usd": policy.upper_targets[-1],
                "lower_target_usd": policy.lower_targets[-1],
                "case_id": spec.case_id,
                "regime": regime,
                "split": split,
                "promoted_direction": direction,
                "promotion_index": promotion,
                "needed_direction": needed_direction,
                "false_expansion": needed_direction is None and direction is not None,
                "wrong_direction": direction is not None and needed_direction is not None and direction != needed_direction,
                "expansion_count": count,
                "final_target_usd": final_target,
                "switch_indices": ",".join(map(str, switches)),
                **metric,
            }
        )

    baselines = [
        {
            "case_id": spec.case_id,
            "regime": regime,
            "split": split,
            "baseline": "fixed_standard",
            **base_metric,
        },
        {
            "case_id": spec.case_id,
            "regime": regime,
            "split": split,
            "baseline": "always_50_20000",
            **wide_metric,
        },
    ]
    timing = {
        "case_id": spec.case_id,
        "regime": regime,
        "split": split,
        "observations": len(obs.times),
        "base_seconds": base_seconds,
        "target_filter_seconds": filter_seconds,
        "always_wide_seconds": wide_seconds,
        "true_min_usd": float(np.min(truth.v)),
        "true_max_usd": float(np.max(truth.v)),
        "promoted_direction": direction,
        "promotion_index": promotion,
    }
    return rows, baselines, timing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-per-regime", type=int, default=160)
    parser.add_argument("--tail-cases-per-regime", type=int, default=80)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--prefix", default="expansion_range")
    parser.add_argument("--replication-salt", default="")
    args = parser.parse_args()

    standard_jobs = reseed_specs(
        make_specs(args.cases_per_regime),
        args.replication_salt,
    )
    jobs = standard_jobs + make_tail_specs(
        args.tail_cases_per_regime,
        args.replication_salt,
    )
    candidate_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, (rows, baselines, timing) in enumerate(pool.map(run_case, jobs, chunksize=1), 1):
            candidate_rows.extend(rows)
            baseline_rows.extend(baselines)
            timing_rows.append(timing)
            if index % 25 == 0 or index == len(jobs):
                print(f"{index}/{len(jobs)}", flush=True)

    results = ROOT / "results"
    results.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(candidate_rows).to_csv(
        results / f"{args.prefix}_raw.csv.gz",
        index=False,
        compression="gzip",
    )
    pd.DataFrame(baseline_rows).to_csv(results / f"{args.prefix}_baselines.csv", index=False)
    pd.DataFrame(timing_rows).to_csv(results / f"{args.prefix}_timing.csv", index=False)
    metadata = {
        "study": "boundary expansion magnitude and staging",
        "standard_range_usd": [BASE_MIN, BASE_MAX],
        "upper_targets_usd": list(UPPER_TARGETS),
        "lower_targets_usd": list(LOWER_TARGETS),
        "trigger": {
            "boundary_mass": TRIGGER_MASS,
            "display_residual_pp": TRIGGER_RESIDUAL_PP,
            "consecutive_hits": 1,
        },
        "current_policy": {"upper_target_usd": CURRENT_UPPER, "lower_target_usd": CURRENT_LOWER},
        "staged_policies": [asdict(policy) for policy in STAGED_POLICIES],
        "standard_cases": args.cases_per_regime * 5,
        "development_cases": args.cases_per_regime * 5 // 2,
        "frozen_test_cases": args.cases_per_regime * 5 // 2,
        "tail_cases": args.tail_cases_per_regime * len(TAIL_REGIMES),
        "tail_regimes": TAIL_REGIMES,
        "one_shot_candidate_count": len(UPPER_TARGETS) * len(LOWER_TARGETS),
        "staged_candidate_count": len(STAGED_POLICIES),
        "replication_salt": args.replication_salt,
    }
    (results / f"{args.prefix}_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
