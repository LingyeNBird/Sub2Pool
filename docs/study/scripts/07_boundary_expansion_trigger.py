#!/usr/bin/env python3
"""Compare direct boundary expansion with shadow-confirmed expansion.

Candidate conditions are selected only on the development split. The frozen test
split is then used once for the reported comparison. The primary target is the
held participant-balance recommendation MAE over the full continuous trajectory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "_support" / "engineering"))

from dynamic_limit.metrics import applied_series  # noqa: E402
from dynamic_limit.generators import simulate_truth  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from dynamic_limit.particle_filter import particle_filter  # noqa: E402
from study_adaptive_bounds import (  # noqa: E402
    BASE_MAX,
    BASE_MIN,
    LOWER_SHADOW_MIN,
    UPPER_SHADOW_MAX,
    config,
    first_consecutive,
    make_specs,
    signed_residuals,
)

POINT_DISTANCES = (25.0, 50.0, 100.0, 200.0, 400.0, 800.0)
MASS_THRESHOLDS = (0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 0.70)
HIT_COUNTS = (1, 2, 3, 4)
EVIDENCE_MASSES = (0.05, 0.10, 0.20, 0.35, 0.50)
RESIDUAL_TOLERANCES = (0.0, 0.05, 0.10, 0.25, 0.50)
EVIDENCE_HITS = (1, 2, 3)
SHADOW_CROSSINGS = (0.0, 25.0, 50.0, 100.0)
TRUE_BOUNDARY_MARGIN_USD = 50.0
OBSERVABLE_RESIDUAL_PP = 0.10
BOOTSTRAP_REPLICATES = 20_000


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    family: str
    distance_usd: float | None = None
    mass_threshold: float | None = None
    residual_tolerance_pp: float | None = None
    consecutive_hits: int = 1
    shadow_crossing_usd: float | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family": self.family,
            "distance_usd": self.distance_usd,
            "mass_threshold": self.mass_threshold,
            "residual_tolerance_pp": self.residual_tolerance_pp,
            "consecutive_hits": self.consecutive_hits,
            "shadow_crossing_usd": self.shadow_crossing_usd,
        }


def candidate_id(prefix: str, *values: object) -> str:
    digest = hashlib.sha256(":".join(map(str, values)).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def build_candidates() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for distance in POINT_DISTANCES:
        for hits in HIT_COUNTS:
            candidates.append(
                Candidate(
                    candidate_id("direct_point", distance, hits),
                    "direct_point",
                    distance_usd=distance,
                    consecutive_hits=hits,
                )
            )
    for mass in MASS_THRESHOLDS:
        for hits in HIT_COUNTS:
            candidates.append(
                Candidate(
                    candidate_id("direct_mass", mass, hits),
                    "direct_mass",
                    mass_threshold=mass,
                    consecutive_hits=hits,
                )
            )
    for mass in EVIDENCE_MASSES:
        for residual in RESIDUAL_TOLERANCES:
            for hits in EVIDENCE_HITS:
                candidates.append(
                    Candidate(
                        candidate_id("direct_evidence", mass, residual, hits),
                        "direct_evidence",
                        mass_threshold=mass,
                        residual_tolerance_pp=residual,
                        consecutive_hits=hits,
                    )
                )
                for crossing in SHADOW_CROSSINGS:
                    candidates.append(
                        Candidate(
                            candidate_id(
                                "shadow_confirmed",
                                mass,
                                residual,
                                hits,
                                crossing,
                            ),
                            "shadow_confirmed",
                            mass_threshold=mass,
                            residual_tolerance_pp=residual,
                            consecutive_hits=hits,
                            shadow_crossing_usd=crossing,
                        )
                    )
    return tuple(candidates)


CANDIDATES = build_candidates()
CANDIDATE_MAP = {candidate.candidate_id: candidate for candidate in CANDIDATES}


def choose_direction(
    upper_signal: np.ndarray,
    lower_signal: np.ndarray,
    hits: int,
    upper_score: np.ndarray,
    lower_score: np.ndarray,
) -> tuple[str | None, int | None]:
    upper_index = first_consecutive(upper_signal, hits)
    lower_index = first_consecutive(lower_signal, hits)
    if upper_index is None and lower_index is None:
        return None, None
    if lower_index is None or (upper_index is not None and upper_index < lower_index):
        return "upper", upper_index
    if upper_index is None or lower_index < upper_index:
        return "lower", lower_index
    assert upper_index is not None and lower_index is not None
    if upper_score[upper_index] >= lower_score[lower_index]:
        return "upper", upper_index
    return "lower", lower_index


def signals_for_candidate(
    candidate: Candidate,
    base,
    upper_shadow,
    lower_shadow,
    upper_residual: np.ndarray,
    lower_residual: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int | None]:
    upper_mass = np.asarray(base.diagnostics["upper_boundary_mass"])
    lower_mass = np.asarray(base.diagnostics["lower_boundary_mass"])
    shadow_launch_index: int | None = None

    if candidate.family == "direct_point":
        distance = float(candidate.distance_usd)
        upper_score = np.asarray(base.v_hat) - (BASE_MAX - distance)
        lower_score = (BASE_MIN + distance) - np.asarray(base.v_hat)
        return upper_score >= 0.0, lower_score >= 0.0, upper_score, lower_score, None

    mass = float(candidate.mass_threshold)
    upper_score = upper_mass - mass
    lower_score = lower_mass - mass
    if candidate.family == "direct_mass":
        return upper_score >= 0.0, lower_score >= 0.0, upper_score, lower_score, None

    residual = float(candidate.residual_tolerance_pp)
    upper_base = (upper_mass >= mass) & (upper_residual > residual)
    lower_base = (lower_mass >= mass) & (lower_residual > residual)
    upper_base_score = np.minimum(upper_score, upper_residual - residual)
    lower_base_score = np.minimum(lower_score, lower_residual - residual)

    if candidate.family == "direct_evidence":
        return upper_base, lower_base, upper_base_score, lower_base_score, None

    crossing = float(candidate.shadow_crossing_usd)
    launch_direction, launch_index = choose_direction(
        upper_base,
        lower_base,
        candidate.consecutive_hits,
        upper_base_score,
        lower_base_score,
    )
    if launch_direction is not None:
        shadow_launch_index = launch_index
    upper_shadow_score = np.asarray(upper_shadow.v_hat) - (BASE_MAX + crossing)
    lower_shadow_score = (BASE_MIN - crossing) - np.asarray(lower_shadow.v_hat)
    upper_signal = upper_base & (upper_shadow_score > 0.0)
    lower_signal = lower_base & (lower_shadow_score > 0.0)
    return (
        upper_signal,
        lower_signal,
        np.minimum(upper_base_score, upper_shadow_score),
        np.minimum(lower_base_score, lower_shadow_score),
        shadow_launch_index,
    )


def error_arrays(truth, obs, output) -> tuple[np.ndarray, np.ndarray]:
    b_true, _ = true_limits(truth)
    applied = applied_series(output.b_hat, obs.sample_idx, len(truth.t))
    return applied - b_true, np.asarray(output.v_hat) - truth.v[obs.sample_idx]


def aggregate_error(error: np.ndarray, capacity_error: np.ndarray) -> dict[str, float]:
    abs_error = np.abs(error)
    return {
        "mae_usd": float(abs_error.mean()),
        "rmse_usd": float(np.sqrt(np.mean(error * error))),
        "bias_usd": float(error.mean()),
        "mean_over_usd": float(np.maximum(error, 0.0).mean()),
        "mean_under_usd": float(np.maximum(-error, 0.0).mean()),
        "worst_participant_mae_usd": float(abs_error.mean(axis=0).max()),
        "capacity_sample_mae_usd": float(np.abs(capacity_error).mean()),
    }


def hybrid_error(
    base_error: np.ndarray,
    expanded_error: np.ndarray,
    base_capacity_error: np.ndarray,
    expanded_capacity_error: np.ndarray,
    obs,
    promotion_index: int,
) -> dict[str, float]:
    switch = int(obs.sample_idx[promotion_index])
    error = np.concatenate((base_error[:switch], expanded_error[switch:]), axis=0)
    capacity_error = np.concatenate(
        (
            base_capacity_error[:promotion_index],
            expanded_capacity_error[promotion_index:],
        )
    )
    return aggregate_error(error, capacity_error)


def truth_direction(truth, obs) -> tuple[str | None, int | None, str | None, int | None]:
    sampled = truth.v[obs.sample_idx]
    upper_needed = sampled > BASE_MAX + TRUE_BOUNDARY_MARGIN_USD
    lower_needed = sampled < BASE_MIN - TRUE_BOUNDARY_MARGIN_USD
    first_upper = int(np.flatnonzero(upper_needed)[0]) if upper_needed.any() else None
    first_lower = int(np.flatnonzero(lower_needed)[0]) if lower_needed.any() else None
    if first_upper is not None and (first_lower is None or first_upper <= first_lower):
        needed_direction, first_needed = "upper", first_upper
    elif first_lower is not None:
        needed_direction, first_needed = "lower", first_lower
    else:
        needed_direction, first_needed = None, None
    return needed_direction, first_needed, upper_needed, lower_needed


def run_case(job) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
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
    started = perf_counter()
    always_wide = particle_filter(
        obs,
        truth.rights,
        seed=seed,
        config=config(LOWER_SHADOW_MIN, UPPER_SHADOW_MAX),
    )
    wide_seconds = perf_counter() - started

    base_error, base_capacity_error = error_arrays(truth, obs, base)
    upper_error, upper_capacity_error = error_arrays(truth, obs, upper_shadow)
    lower_error, lower_capacity_error = error_arrays(truth, obs, lower_shadow)
    wide_error, wide_capacity_error = error_arrays(truth, obs, always_wide)
    base_metric = aggregate_error(base_error, base_capacity_error)
    upper_metric = aggregate_error(upper_error, upper_capacity_error)
    lower_metric = aggregate_error(lower_error, lower_capacity_error)
    wide_metric = aggregate_error(wide_error, wide_capacity_error)

    needed_direction, first_needed, upper_needed, lower_needed = truth_direction(truth, obs)
    upper_residual, lower_residual = signed_residuals(base, obs.z)
    upper_observable = upper_needed & (upper_residual > OBSERVABLE_RESIDUAL_PP)
    lower_observable = lower_needed & (lower_residual > OBSERVABLE_RESIDUAL_PP)
    first_upper_observable = (
        int(np.flatnonzero(upper_observable)[0]) if upper_observable.any() else None
    )
    first_lower_observable = (
        int(np.flatnonzero(lower_observable)[0]) if lower_observable.any() else None
    )
    if first_upper_observable is not None and (
        first_lower_observable is None or first_upper_observable <= first_lower_observable
    ):
        observable_direction, first_observable = "upper", first_upper_observable
    elif first_lower_observable is not None:
        observable_direction, first_observable = "lower", first_lower_observable
    else:
        observable_direction, first_observable = None, None

    metric_cache: dict[tuple[str | None, int | None], dict[str, float]] = {
        (None, None): base_metric
    }

    def metric_for(direction: str | None, promotion: int | None) -> dict[str, float]:
        key = (direction, promotion)
        if key in metric_cache:
            return metric_cache[key]
        assert promotion is not None and direction is not None
        if direction == "upper":
            metric = hybrid_error(
                base_error,
                upper_error,
                base_capacity_error,
                upper_capacity_error,
                obs,
                promotion,
            )
        else:
            metric = hybrid_error(
                base_error,
                lower_error,
                base_capacity_error,
                lower_capacity_error,
                obs,
                promotion,
            )
        metric_cache[key] = metric
        return metric

    rows: list[dict[str, Any]] = []
    observation_count = len(obs.times)
    for candidate in CANDIDATES:
        upper_signal, lower_signal, upper_score, lower_score, shadow_launch = signals_for_candidate(
            candidate,
            base,
            upper_shadow,
            lower_shadow,
            upper_residual,
            lower_residual,
        )
        direction, promotion = choose_direction(
            upper_signal,
            lower_signal,
            candidate.consecutive_hits,
            upper_score,
            lower_score,
        )
        metric = metric_for(direction, promotion)
        correct = needed_direction is not None and direction == needed_direction
        observable_correct = (
            observable_direction is not None and direction == observable_direction
        )
        delay = (
            max(0.0, float(obs.times[promotion] - obs.times[first_needed]))
            if correct and promotion is not None and first_needed is not None
            else None
        )
        observable_delay = (
            max(0.0, float(obs.times[promotion] - obs.times[first_observable]))
            if observable_correct
            and promotion is not None
            and first_observable is not None
            else None
        )
        if candidate.family == "shadow_confirmed":
            if shadow_launch is None:
                compute_factor = 1.0
            elif promotion is None:
                compute_factor = 2.0
            else:
                compute_factor = 1.0 + (promotion + 1) / observation_count
        else:
            compute_factor = (
                1.0
                if promotion is None
                else 1.0 + (promotion + 1) / observation_count
            )
        rows.append(
            {
                **candidate.as_record(),
                "case_id": spec.case_id,
                "regime": regime,
                "split": split,
                "needed_direction": needed_direction,
                "observable_direction": observable_direction,
                "promoted_direction": direction,
                "promotion_index": promotion,
                "promotion_time_hours": float(obs.times[promotion]) if promotion is not None else None,
                "correctly_detected": bool(correct),
                "observable_detected": bool(observable_correct),
                "false_expansion": needed_direction is None and direction is not None,
                "wrong_direction": (
                    direction is not None
                    and needed_direction is not None
                    and direction != needed_direction
                ),
                "delay_hours": delay,
                "observable_delay_hours": observable_delay,
                "estimated_compute_factor": compute_factor,
                **metric,
                "base_mae_usd": base_metric["mae_usd"],
            }
        )

    oracle_metric = (
        upper_metric
        if needed_direction == "upper"
        else lower_metric
        if needed_direction == "lower"
        else base_metric
    )
    baselines = []
    for name, metric in (
        ("fixed_standard", base_metric),
        ("always_wide", wide_metric),
        ("oracle_direction", oracle_metric),
    ):
        baselines.append(
            {
                "case_id": spec.case_id,
                "regime": regime,
                "split": split,
                "baseline": name,
                **metric,
            }
        )
    timing = {
        "case_id": spec.case_id,
        "regime": regime,
        "split": split,
        "observations": observation_count,
        "base_seconds": base_seconds,
        "upper_shadow_seconds": upper_seconds,
        "lower_shadow_seconds": lower_seconds,
        "always_wide_seconds": wide_seconds,
    }
    return rows, baselines, timing


def summarize_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    records = []
    for candidate_id_value, group in frame.groupby("candidate_id", sort=False):
        first = group.iloc[0]
        needed = group[group["needed_direction"].notna()]
        in_range = group[group["needed_direction"].isna()]
        observable = group[group["observable_direction"].notna()]
        detected = needed[needed["correctly_detected"]]
        observable_detected = observable[observable["observable_detected"]]
        records.append(
            {
                **{key: first[key] for key in CANDIDATES[0].as_record()},
                "candidate_id": candidate_id_value,
                "cases": len(group),
                "mean_mae_usd": float(group["mae_usd"].mean()),
                "median_case_mae_usd": float(group["mae_usd"].median()),
                "p95_case_mae_usd": float(group["mae_usd"].quantile(0.95)),
                "max_case_mae_usd": float(group["mae_usd"].max()),
                "mean_rmse_usd": float(group["rmse_usd"].mean()),
                "mean_worst_participant_mae_usd": float(
                    group["worst_participant_mae_usd"].mean()
                ),
                "mean_capacity_mae_usd": float(group["capacity_sample_mae_usd"].mean()),
                "mean_bias_usd": float(group["bias_usd"].mean()),
                "mean_over_usd": float(group["mean_over_usd"].mean()),
                "mean_under_usd": float(group["mean_under_usd"].mean()),
                "mean_gain_vs_base_usd": float(
                    (group["base_mae_usd"] - group["mae_usd"]).mean()
                ),
                "promotion_rate": float(group["promoted_direction"].notna().mean()),
                "false_expansion_rate": float(in_range["false_expansion"].mean())
                if len(in_range)
                else 0.0,
                "detection_rate": float(needed["correctly_detected"].mean())
                if len(needed)
                else 0.0,
                "observable_detection_rate": float(
                    observable["observable_detected"].mean()
                )
                if len(observable)
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
                "mean_compute_factor": float(group["estimated_compute_factor"].mean()),
            }
        )
    return pd.DataFrame(records)


def summarize_baselines(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby("baseline", as_index=False)
        .agg(
            cases=("case_id", "size"),
            mean_mae_usd=("mae_usd", "mean"),
            median_case_mae_usd=("mae_usd", "median"),
            p95_case_mae_usd=("mae_usd", lambda values: values.quantile(0.95)),
            max_case_mae_usd=("mae_usd", "max"),
            mean_rmse_usd=("rmse_usd", "mean"),
            mean_worst_participant_mae_usd=("worst_participant_mae_usd", "mean"),
            mean_capacity_mae_usd=("capacity_sample_mae_usd", "mean"),
            mean_bias_usd=("bias_usd", "mean"),
        )
    )


def select_by_family(summary: pd.DataFrame) -> pd.DataFrame:
    selected = []
    for _, group in summary.groupby("family", sort=False):
        selected.append(
            group.sort_values(
                ["mean_mae_usd", "p95_case_mae_usd", "max_case_mae_usd"],
                ascending=True,
            ).iloc[0]
        )
    return pd.DataFrame(selected)


def bootstrap_difference(
    frame: pd.DataFrame,
    strategy_id: str,
    comparator: pd.Series,
    seed: int,
) -> dict[str, float]:
    strategy = (
        frame[frame["candidate_id"] == strategy_id]
        .set_index("case_id")["mae_usd"]
        .sort_index()
    )
    comparator = comparator.sort_index()
    common = strategy.index.intersection(comparator.index)
    differences = strategy.loc[common].to_numpy() - comparator.loc[common].to_numpy()
    rng = np.random.default_rng(seed)
    sampled = rng.choice(differences, size=(BOOTSTRAP_REPLICATES, len(differences)), replace=True)
    means = sampled.mean(axis=1)
    return {
        "cases": len(differences),
        "mean_difference_usd": float(differences.mean()),
        "ci95_low_usd": float(np.quantile(means, 0.025)),
        "ci95_high_usd": float(np.quantile(means, 0.975)),
        "strategy_win_rate": float((differences < 0.0).mean()),
        "tie_rate": float(np.isclose(differences, 0.0).mean()),
    }


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-per-regime", type=int, default=160)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--prefix", default="expansion_strategy")
    args = parser.parse_args()

    jobs = make_specs(args.cases_per_regime)
    candidate_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for index, (rows, baselines, timing) in enumerate(
            pool.map(run_case, jobs, chunksize=1),
            1,
        ):
            candidate_rows.extend(rows)
            baseline_rows.extend(baselines)
            timing_rows.append(timing)
            if index % 50 == 0 or index == len(jobs):
                print(f"{index}/{len(jobs)}", flush=True)

    results = ROOT / "results"
    candidate_frame = pd.DataFrame(candidate_rows)
    baseline_frame = pd.DataFrame(baseline_rows)
    timing_frame = pd.DataFrame(timing_rows)
    candidate_frame.to_csv(
        results / f"{args.prefix}_raw.csv.gz",
        index=False,
        compression="gzip",
    )
    baseline_frame.to_csv(results / f"{args.prefix}_baselines.csv", index=False)
    timing_frame.to_csv(results / f"{args.prefix}_timing.csv", index=False)

    development = candidate_frame[candidate_frame["split"] == "development"]
    frozen = candidate_frame[candidate_frame["split"] == "test"]
    development_summary = summarize_candidates(development)
    selected_development = select_by_family(development_summary)
    selected_ids = selected_development["candidate_id"].tolist()
    frozen_selected = frozen[frozen["candidate_id"].isin(selected_ids)]
    frozen_summary = summarize_candidates(frozen_selected)
    frozen_summary = frozen_summary.merge(
        selected_development[["candidate_id", "mean_mae_usd"]].rename(
            columns={"mean_mae_usd": "development_mean_mae_usd"}
        ),
        on="candidate_id",
        how="left",
    )
    frozen_baselines = baseline_frame[baseline_frame["split"] == "test"]
    baseline_summary = summarize_baselines(frozen_baselines)

    development_summary.to_csv(
        results / f"{args.prefix}_development_candidates.csv",
        index=False,
    )
    frozen_summary.to_csv(
        results / f"{args.prefix}_frozen_selected.csv",
        index=False,
    )

    selected_lookup = {
        row["family"]: row["candidate_id"]
        for _, row in selected_development.iterrows()
    }
    best_shadow_id = selected_lookup["shadow_confirmed"]
    best_shadow = CANDIDATE_MAP[best_shadow_id]
    matched_direct = next(
        candidate
        for candidate in CANDIDATES
        if candidate.family == "direct_evidence"
        and candidate.mass_threshold == best_shadow.mass_threshold
        and candidate.residual_tolerance_pp == best_shadow.residual_tolerance_pp
        and candidate.consecutive_hits == best_shadow.consecutive_hits
    )
    comparisons: dict[str, Any] = {}
    base_series = (
        frozen_baselines[frozen_baselines["baseline"] == "fixed_standard"]
        .set_index("case_id")["mae_usd"]
    )
    for family, candidate_id_value in selected_lookup.items():
        comparisons[f"{family}_vs_fixed_standard"] = bootstrap_difference(
            frozen,
            candidate_id_value,
            base_series,
            seed=20260810 + len(comparisons),
        )
    direct_point_series = (
        frozen[frozen["candidate_id"] == selected_lookup["direct_point"]]
        .set_index("case_id")["mae_usd"]
    )
    direct_evidence_series = (
        frozen[frozen["candidate_id"] == selected_lookup["direct_evidence"]]
        .set_index("case_id")["mae_usd"]
    )
    matched_direct_series = (
        frozen[frozen["candidate_id"] == matched_direct.candidate_id]
        .set_index("case_id")["mae_usd"]
    )
    comparisons["best_shadow_vs_best_direct_point"] = bootstrap_difference(
        frozen,
        best_shadow_id,
        direct_point_series,
        seed=20260821,
    )
    comparisons["best_shadow_vs_best_direct_evidence"] = bootstrap_difference(
        frozen,
        best_shadow_id,
        direct_evidence_series,
        seed=20260822,
    )
    comparisons["best_shadow_vs_matched_direct_evidence"] = bootstrap_difference(
        frozen,
        best_shadow_id,
        matched_direct_series,
        seed=20260823,
    )

    direct_point_profiles = [
        (800.0, 1),
        (400.0, 1),
        (200.0, 1),
        (100.0, 2),
        (50.0, 3),
        (25.0, 4),
    ]
    sensitivity_ids = [
        candidate.candidate_id
        for distance, hits in direct_point_profiles
        for candidate in CANDIDATES
        if candidate.family == "direct_point"
        and candidate.distance_usd == distance
        and candidate.consecutive_hits == hits
    ]
    sensitivity_summary = summarize_candidates(
        frozen[frozen["candidate_id"].isin(sensitivity_ids)]
    ).sort_values(["distance_usd", "consecutive_hits"], ascending=[False, True])
    sensitivity_summary.to_csv(
        results / f"{args.prefix}_sensitivity_profiles.csv",
        index=False,
    )

    timing = {
        "mean_base_ms": float(timing_frame["base_seconds"].mean() * 1000.0),
        "mean_upper_expanded_ms": float(
            timing_frame["upper_shadow_seconds"].mean() * 1000.0
        ),
        "mean_lower_expanded_ms": float(
            timing_frame["lower_shadow_seconds"].mean() * 1000.0
        ),
        "mean_always_wide_ms": float(
            timing_frame["always_wide_seconds"].mean() * 1000.0
        ),
    }
    payload = {
        "study": "direct boundary expansion versus shadow-confirmed expansion",
        "standard_range_usd": [BASE_MIN, BASE_MAX],
        "expanded_upper_range_usd": [BASE_MIN, UPPER_SHADOW_MAX],
        "expanded_lower_range_usd": [LOWER_SHADOW_MIN, BASE_MAX],
        "cases": len(jobs),
        "development_cases": len(jobs) // 2,
        "frozen_test_cases": len(jobs) // 2,
        "candidate_count": len(CANDIDATES),
        "primary_selection_metric": (
            "Minimum development-set mean held participant-balance MAE; "
            "case p95 and maximum break ties. No false-expansion constraint."
        ),
        "families": {
            "direct_point": "Expand when the base capacity median enters a boundary distance.",
            "direct_mass": "Expand when enough posterior mass enters the nearest 5% boundary band.",
            "direct_evidence": "Directly expand when boundary mass and display residual agree.",
            "shadow_confirmed": (
                "Require the same base evidence and an expanded one-sided filter median "
                "to cross the old boundary before expansion."
            ),
        },
        "development_selected": records(selected_development),
        "frozen_selected": records(frozen_summary),
        "frozen_baselines": records(baseline_summary),
        "paired_bootstrap_case_mae": comparisons,
        "matched_shadow_direct_candidate": {
            "shadow": best_shadow.as_record(),
            "direct": matched_direct.as_record(),
        },
        "direct_point_sensitivity_profiles": records(sensitivity_summary),
        "timing": timing,
        "cautions": [
            "The test split was frozen before candidate selection and used once.",
            "Synthetic trajectories are continuous; no estimator can detect an excursion that leaves no cost/percentage evidence.",
            "Direct and shadow methods use the same expanded filter after promotion; their difference is the trigger decision.",
            "Estimated policy compute factors exclude database I/O and upstream requests.",
        ],
    }
    output = results / f"{args.prefix}_study.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
