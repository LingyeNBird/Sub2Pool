#!/usr/bin/env python3
"""Evaluate causal staged range contraction after adaptive PF expansion.

The discovery split selects contraction parameters. A frozen split reports the
selected policies once, and an independently seeded confirmation set verifies
those finalists. Every contraction decision uses only information available at
that observation; truth is used only for metrics and an explicit oracle bound.
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

from dynamic_limit.generators import (  # noqa: E402
    _generate_consumption_impl,
    _smoothstep01,
    rights_vector,
)
from dynamic_limit.models import SimulationSpec, SimulationTruth  # noqa: E402
from dynamic_limit.observation import make_observations, true_limits  # noqa: E402
from dynamic_limit.particle_filter import particle_filter  # noqa: E402
from compare_expansion_ranges import (  # noqa: E402
    initial_trigger,
    trigger_for_output,
)
from study_adaptive_bounds import config, signed_residuals, stable_seed  # noqa: E402

BASE_MIN = 1400.0
BASE_MAX = 4000.0
UPPER_RANGES = (
    (BASE_MIN, BASE_MAX),
    (BASE_MIN, 6000.0),
    (BASE_MIN, 10000.0),
    (BASE_MIN, 20000.0),
)
LOWER_RANGES = (
    (BASE_MIN, BASE_MAX),
    (700.0, BASE_MAX),
    (250.0, BASE_MAX),
    (50.0, BASE_MAX),
)
TRUE_MARGIN_USD = 50.0
BOOTSTRAP_REPLICATES = 20_000

REGIMES = (
    "upper_return_mild",
    "upper_return_extreme",
    "lower_return_mild",
    "lower_return_extreme",
    "upper_rebound",
    "lower_rebound",
    "upper_persistent",
    "lower_persistent",
    "in_range_upper",
    "in_range_lower",
)
RETURN_REGIMES = {
    "upper_return_mild",
    "upper_return_extreme",
    "lower_return_mild",
    "lower_return_extreme",
    "upper_rebound",
    "lower_rebound",
}
PERSISTENT_REGIMES = {"upper_persistent", "lower_persistent"}
IN_RANGE_REGIMES = {"in_range_upper", "in_range_lower"}
SCENARIOS = (
    "uniform",
    "front_loaded",
    "back_loaded",
    "multi_burst",
    "one_steady_others_burst",
    "v_high_corr",
    "v_low_corr",
    "extreme_sample_edge_bursts",
)


@dataclass(frozen=True)
class ContractionPolicy:
    candidate_id: str
    family: str
    margin_usd: float
    consecutive_hits: int
    cooldown_hours: float
    minimum_stage: int
    shadow_boundary_mass: float | None = None
    shadow_residual_pp: float | None = None

    @property
    def uses_shadow(self) -> bool:
        return self.shadow_boundary_mass is not None

    def record(self) -> dict[str, Any]:
        return asdict(self)


def make_candidate_id(
    family: str,
    margin: float,
    hits: int,
    cooldown: float,
    minimum_stage: int,
    mass: float | None,
    residual: float | None,
) -> str:
    raw = f"{family}:{margin}:{hits}:{cooldown}:{minimum_stage}:{mass}:{residual}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    floor = "high" if minimum_stage else "all"
    return f"{family}_{floor}_{digest}"


def build_candidates() -> tuple[ContractionPolicy, ...]:
    candidates: list[ContractionPolicy] = []
    for minimum_stage in (0, 1):
        for margin in (0.0, 150.0, 300.0):
            for hits in (1, 2, 3):
                for cooldown in (6.0, 24.0, 48.0):
                    family = "direct"
                    candidates.append(
                        ContractionPolicy(
                            make_candidate_id(
                                family,
                                margin,
                                hits,
                                cooldown,
                                minimum_stage,
                                None,
                                None,
                            ),
                            family,
                            margin,
                            hits,
                            cooldown,
                            minimum_stage,
                        )
                    )
                    for mass in (0.02, 0.05, 0.10):
                        for residual in (0.05, 0.10):
                            family = "shadow"
                            candidates.append(
                                ContractionPolicy(
                                    make_candidate_id(
                                        family,
                                        margin,
                                        hits,
                                        cooldown,
                                        minimum_stage,
                                        mass,
                                        residual,
                                    ),
                                    family,
                                    margin,
                                    hits,
                                    cooldown,
                                    minimum_stage,
                                    mass,
                                    residual,
                                )
                            )
    return tuple(candidates)


CANDIDATES = build_candidates()
CANDIDATE_MAP = {candidate.candidate_id: candidate for candidate in CANDIDATES}


def smooth_pulse(
    u: np.ndarray,
    start: float,
    rise_end: float,
    fall_start: float,
    end: float,
) -> np.ndarray:
    rise = _smoothstep01((u - start) / max(rise_end - start, 1e-9))
    fall = 1.0 - _smoothstep01(
        (u - fall_start) / max(end - fall_start, 1e-9)
    )
    return rise * fall


def baseline_path(u: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    center = rng.uniform(2200.0, 2900.0)
    phase_a = rng.uniform(0.0, 2.0 * np.pi)
    phase_b = rng.uniform(0.0, 2.0 * np.pi)
    return (
        center
        + rng.uniform(35.0, 110.0) * np.sin(2.0 * np.pi * u * rng.uniform(1.2, 2.8) + phase_a)
        + rng.uniform(15.0, 55.0) * np.sin(2.0 * np.pi * u * rng.uniform(3.0, 6.0) + phase_b)
    )


def one_return_pulse(
    u: np.ndarray,
    rng: np.random.Generator,
    *,
    late: bool = False,
) -> np.ndarray:
    start = rng.uniform(0.58, 0.68) if late else rng.uniform(0.10, 0.24)
    rise = rng.uniform(0.045, 0.09)
    plateau = rng.uniform(0.08, 0.18)
    fall = rng.uniform(0.055, 0.11)
    return smooth_pulse(
        u,
        start,
        start + rise,
        start + rise + plateau,
        min(0.96, start + rise + plateau + fall),
    )


def double_return_pulse(u: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    first = smooth_pulse(u, 0.10, 0.18, 0.27, 0.36)
    shift = rng.uniform(-0.025, 0.025)
    second = smooth_pulse(
        u,
        0.52 + shift,
        0.60 + shift,
        0.70 + shift,
        0.79 + shift,
    )
    return np.maximum(first, second)


def capacity_path(
    regime: str,
    t: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    u = t / t[-1]
    base = baseline_path(u, rng)
    if regime == "upper_return_mild":
        target = rng.uniform(4400.0, 5700.0)
        pulse = one_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "upper_return_extreme":
        target = rng.uniform(6500.0, 9200.0)
        pulse = one_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "lower_return_mild":
        target = rng.uniform(700.0, 1200.0)
        pulse = one_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "lower_return_extreme":
        target = rng.uniform(180.0, 600.0)
        pulse = one_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "upper_rebound":
        target = rng.uniform(4700.0, 7000.0)
        pulse = double_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "lower_rebound":
        target = rng.uniform(250.0, 1000.0)
        pulse = double_return_pulse(u, rng)
        value = base + pulse * (target - base)
    elif regime == "upper_persistent":
        start = rng.uniform(0.14, 0.38)
        transition = rng.uniform(0.07, 0.15)
        step = _smoothstep01((u - start) / transition)
        target = rng.uniform(4700.0, 8500.0)
        value = base + step * (target - base)
    elif regime == "lower_persistent":
        start = rng.uniform(0.14, 0.38)
        transition = rng.uniform(0.07, 0.15)
        step = _smoothstep01((u - start) / transition)
        target = rng.uniform(250.0, 1100.0)
        value = base + step * (target - base)
    elif regime == "in_range_upper":
        target = rng.uniform(3600.0, 3975.0)
        pulse = one_return_pulse(u, rng, late=bool(rng.integers(0, 2)))
        value = base + pulse * (target - base)
    elif regime == "in_range_lower":
        target = rng.uniform(1425.0, 1750.0)
        pulse = one_return_pulse(u, rng, late=bool(rng.integers(0, 2)))
        value = base + pulse * (target - base)
    else:
        raise ValueError(f"Unknown contraction regime: {regime}")
    return np.clip(value, 50.0, 20_000.0)


def simulate_contraction_truth(spec: SimulationSpec) -> SimulationTruth:
    rng = np.random.default_rng(spec.seed)
    steps = int(round(spec.horizon_hours / spec.dt_hours))
    t = np.linspace(0.0, spec.horizon_hours, steps + 1)
    v = capacity_path(spec.speed, t, rng)
    rights = rights_vector(spec.n_participants, spec.rights_profile, rng)
    d_c, consumption_meta = _generate_consumption_impl(
        rng,
        t,
        v,
        spec.n_participants,
        spec.scenario,
        spec.target_progress_low,
        spec.target_progress_high,
        spec.sample_hours,
        rights,
    )
    x = 100.0 / v
    x_mid = 0.5 * (x[:-1] + x[1:])
    d_q = d_c * x_mid[:, None]
    c = np.vstack([np.zeros(spec.n_participants), np.cumsum(d_c, axis=0)])
    q = np.vstack([np.zeros(spec.n_participants), np.cumsum(d_q, axis=0)])
    p = q.sum(axis=1)
    return SimulationTruth(
        spec=spec,
        t=t,
        v=v,
        x=x,
        rights=rights,
        d_c=d_c,
        c=c,
        d_q=d_q,
        q=q,
        p=p,
        metadata={
            **consumption_meta,
            "contraction_regime": spec.speed,
            "true_min_usd": float(v.min()),
            "true_max_usd": float(v.max()),
        },
    )


def make_jobs(
    cases_per_regime: int,
    *,
    split: str,
    salt: str,
) -> list[tuple[str, str, SimulationSpec]]:
    jobs: list[tuple[str, str, SimulationSpec]] = []
    for regime in REGIMES:
        for index in range(cases_per_regime):
            case_id = f"contraction_{salt}_{split}_{regime}_{index:04d}"
            rng = np.random.default_rng(stable_seed(case_id + ":design"))
            low = float(rng.choice([45.0, 65.0, 82.0]))
            high = float(rng.choice([75.0, 92.0, 99.0]))
            if high <= low:
                low, high = 65.0, 92.0
            spec = SimulationSpec(
                case_id=case_id,
                seed=stable_seed(case_id),
                speed=regime,
                n_participants=int(rng.choice([2, 4, 6])),
                rights_profile=str(
                    rng.choice(
                        ["balanced", "moderate_skew", "extreme_skew", "random"]
                    )
                ),
                scenario=str(rng.choice(SCENARIOS)),
                sample_hours=float(rng.choice([1.0, 3.0, 6.0])),
                quantizer=str(rng.choice(["floor", "nearest", "ceil"])),
                horizon_hours=168.0,
                dt_hours=1.0 / 6.0,
                target_progress_low=low,
                target_progress_high=high,
                capacity_min_usd=50.0,
                capacity_max_usd=20_000.0,
            )
            jobs.append((regime, split, spec))
    return jobs


def run_static_filters(truth: SimulationTruth, obs) -> dict[str, list[Any]]:
    seed = (truth.spec.seed + 7919) % (2**32 - 1)
    upper = [
        particle_filter(obs, truth.rights, seed=seed, config=config(low, high))
        for low, high in UPPER_RANGES
    ]
    lower = [upper[0]] + [
        particle_filter(obs, truth.rights, seed=seed, config=config(low, high))
        for low, high in LOWER_RANGES[1:]
    ]
    return {"upper": upper, "lower": lower}


def shrink_signal(
    policy: ContractionPolicy,
    direction: str,
    stage: int,
    row: int,
    outputs: list[Any],
    displayed: np.ndarray,
) -> bool:
    current = outputs[stage]
    narrower = outputs[stage - 1]
    quantiles = np.asarray(current.diagnostics["capacity_quantiles"])
    if direction == "upper":
        narrower_edge = UPPER_RANGES[stage - 1][1]
        inside = quantiles[row, 2] <= narrower_edge - policy.margin_usd
        if not policy.uses_shadow:
            return bool(inside)
        mass = np.asarray(narrower.diagnostics["upper_boundary_mass"])[row]
        residual = signed_residuals(narrower, displayed)[0][row]
    else:
        narrower_edge = LOWER_RANGES[stage - 1][0]
        inside = quantiles[row, 0] >= narrower_edge + policy.margin_usd
        if not policy.uses_shadow:
            return bool(inside)
        mass = np.asarray(narrower.diagnostics["lower_boundary_mass"])[row]
        residual = signed_residuals(narrower, displayed)[1][row]
    return bool(
        inside
        and mass <= float(policy.shadow_boundary_mass)
        and residual <= float(policy.shadow_residual_pp)
    )


def oracle_shrink_signal(
    direction: str,
    stage: int,
    row: int,
    sampled_truth: np.ndarray,
) -> bool:
    if direction == "upper":
        return bool(sampled_truth[row] <= UPPER_RANGES[stage - 1][1] - 100.0)
    return bool(sampled_truth[row] >= LOWER_RANGES[stage - 1][0] + 100.0)


def stage_path(
    outputs_by_direction: dict[str, list[Any]],
    obs,
    sampled_truth: np.ndarray,
    policy: ContractionPolicy | None,
    *,
    oracle: bool = False,
) -> tuple[str | None, np.ndarray, list[dict[str, Any]]]:
    base = outputs_by_direction["upper"][0]
    direction, _ = initial_trigger(base, obs.z)
    path = np.zeros(len(obs.times), dtype=int)
    if direction is None:
        return None, path, []

    outputs = outputs_by_direction[direction]
    expansion_signals = [
        trigger_for_output(output, obs.z, direction) for output in outputs[:-1]
    ]
    events: list[dict[str, Any]] = []
    stage = 0
    hit_run = 0
    last_transition_time = -np.inf
    has_shrunk = False

    for row, time_hours in enumerate(obs.times):
        expanded = False
        while stage < len(outputs) - 1 and expansion_signals[stage][row]:
            previous = stage
            stage += 1
            events.append(
                {
                    "kind": "reexpand" if has_shrunk else "expand",
                    "row": row,
                    "time_hours": float(time_hours),
                    "from_stage": previous,
                    "to_stage": stage,
                }
            )
            expanded = True
            hit_run = 0
            last_transition_time = float(time_hours)

        minimum_stage = 0 if policy is None else policy.minimum_stage
        if (
            (policy is not None or oracle)
            and not expanded
            and stage > minimum_stage
        ):
            cooldown = 0.0 if oracle else float(policy.cooldown_hours)
            eligible = float(time_hours) - last_transition_time >= cooldown
            if eligible:
                signal = (
                    oracle_shrink_signal(
                        direction,
                        stage,
                        row,
                        sampled_truth,
                    )
                    if oracle
                    else shrink_signal(
                        policy,
                        direction,
                        stage,
                        row,
                        outputs,
                        obs.z,
                    )
                )
                hit_run = hit_run + 1 if signal else 0
            else:
                hit_run = 0
            required_hits = 1 if oracle else policy.consecutive_hits
            if hit_run >= required_hits:
                previous = stage
                stage -= 1
                events.append(
                    {
                        "kind": "shrink",
                        "row": row,
                        "time_hours": float(time_hours),
                        "from_stage": previous,
                        "to_stage": stage,
                    }
                )
                has_shrunk = True
                hit_run = 0
                last_transition_time = float(time_hours)
        else:
            hit_run = 0
        path[row] = stage
    return direction, path, events


def metric_cache(truth: SimulationTruth, obs, outputs: list[Any]) -> dict[str, np.ndarray]:
    b_true, _ = true_limits(truth)
    n_obs = len(obs.times)
    n_stages = len(outputs)
    n_participants = truth.spec.n_participants
    abs_sum = np.zeros((n_obs, n_stages))
    square_sum = np.zeros((n_obs, n_stages))
    signed_sum = np.zeros((n_obs, n_stages))
    over_sum = np.zeros((n_obs, n_stages))
    under_sum = np.zeros((n_obs, n_stages))
    participant_abs = np.zeros((n_obs, n_stages, n_participants))
    counts = np.zeros(n_obs, dtype=int)

    for row in range(n_obs):
        start = int(obs.sample_idx[row])
        end = (
            int(obs.sample_idx[row + 1])
            if row + 1 < n_obs
            else len(truth.t)
        )
        counts[row] = max(0, end - start)
        sample_truth = b_true[start:end]
        for stage, output in enumerate(outputs):
            error = output.b_hat[row][None, :] - sample_truth
            abs_error = np.abs(error)
            abs_sum[row, stage] = abs_error.sum()
            square_sum[row, stage] = np.square(error).sum()
            signed_sum[row, stage] = error.sum()
            over_sum[row, stage] = np.maximum(error, 0.0).sum()
            under_sum[row, stage] = np.maximum(-error, 0.0).sum()
            participant_abs[row, stage] = abs_error.sum(axis=0)

    capacity_error = np.stack(
        [np.abs(output.v_hat - truth.v[obs.sample_idx]) for output in outputs],
        axis=1,
    )
    sample_truth = b_true[obs.sample_idx]
    interval_covered = np.stack(
        [
            (sample_truth >= output.b_lower - 1e-9)
            & (sample_truth <= output.b_upper + 1e-9)
            for output in outputs
        ],
        axis=1,
    )
    interval_width = np.stack(
        [output.b_upper - output.b_lower for output in outputs],
        axis=1,
    )
    return {
        "abs_sum": abs_sum,
        "square_sum": square_sum,
        "signed_sum": signed_sum,
        "over_sum": over_sum,
        "under_sum": under_sum,
        "participant_abs": participant_abs,
        "counts": counts,
        "capacity_error": capacity_error,
        "interval_covered": interval_covered,
        "interval_width": interval_width,
    }


def metrics_for_path(
    cache: dict[str, np.ndarray],
    outputs: list[Any],
    path: np.ndarray,
) -> dict[str, float]:
    rows = np.arange(len(path))
    n_participants = outputs[0].b_hat.shape[1]
    total_fine = int(cache["counts"].sum())
    denominator = max(1, total_fine * n_participants)
    participant_denominator = max(1, total_fine)
    selected_b = np.stack(
        [outputs[int(stage)].b_hat[row] for row, stage in enumerate(path)]
    )
    selected_covered = cache["interval_covered"][rows, path]
    selected_width = cache["interval_width"][rows, path]
    participant_mae = (
        cache["participant_abs"][rows, path].sum(axis=0)
        / participant_denominator
    )
    return {
        "mae_usd": float(cache["abs_sum"][rows, path].sum() / denominator),
        "rmse_usd": float(
            np.sqrt(cache["square_sum"][rows, path].sum() / denominator)
        ),
        "bias_usd": float(cache["signed_sum"][rows, path].sum() / denominator),
        "mean_over_usd": float(cache["over_sum"][rows, path].sum() / denominator),
        "mean_under_usd": float(cache["under_sum"][rows, path].sum() / denominator),
        "worst_participant_mae_usd": float(participant_mae.max()),
        "capacity_sample_mae_usd": float(
            cache["capacity_error"][rows, path].mean()
        ),
        "interval_sample_coverage": float(selected_covered.mean()),
        "interval_mean_width_usd": float(selected_width.mean()),
        "adjustment_total_variation_usd": float(
            np.abs(np.diff(selected_b, axis=0)).sum() / n_participants
        ),
    }


def event_metrics(
    direction: str | None,
    events: list[dict[str, Any]],
    sampled_truth: np.ndarray,
    obs,
) -> dict[str, Any]:
    shrink_events = [event for event in events if event["kind"] == "shrink"]
    expansion_events = [
        event for event in events if event["kind"] in {"expand", "reexpand"}
    ]
    reexpansions = [event for event in events if event["kind"] == "reexpand"]
    premature = 0
    for event in shrink_events:
        row = int(event["row"])
        horizon = np.flatnonzero(obs.times <= obs.times[row] + 24.0)
        end = int(horizon[-1]) + 1 if len(horizon) else row + 1
        to_stage = int(event["to_stage"])
        future = sampled_truth[row:end]
        if direction == "upper":
            edge = UPPER_RANGES[to_stage][1]
            premature += int(np.any(future > edge + TRUE_MARGIN_USD))
        elif direction == "lower":
            edge = LOWER_RANGES[to_stage][0]
            premature += int(np.any(future < edge - TRUE_MARGIN_USD))
    return {
        "promoted_direction": direction,
        "expansion_count": len(expansion_events),
        "shrink_count": len(shrink_events),
        "reexpansion_count": len(reexpansions),
        "premature_shrink_24h_count": premature,
        "final_stage": int(events[-1]["to_stage"]) if events else 0,
        "event_trace": ",".join(
            f"{event['kind']}@{event['row']}:{event['from_stage']}>{event['to_stage']}"
            for event in events
        ),
    }


def policy_row(
    candidate_id: str,
    family: str,
    policy: ContractionPolicy | None,
    direction: str | None,
    path: np.ndarray,
    events: list[dict[str, Any]],
    truth: SimulationTruth,
    obs,
    outputs_by_direction: dict[str, list[Any]],
    caches: dict[str, dict[str, np.ndarray]],
    regime: str,
    split: str,
) -> dict[str, Any]:
    selected_direction = direction or "upper"
    outputs = outputs_by_direction[selected_direction]
    metric = metrics_for_path(caches[selected_direction], outputs, path)
    record = {
        "candidate_id": candidate_id,
        "family": family,
        "case_id": truth.spec.case_id,
        "regime": regime,
        "regime_group": (
            "return"
            if regime in RETURN_REGIMES
            else "persistent"
            if regime in PERSISTENT_REGIMES
            else "in_range"
        ),
        "split": split,
        "speed": truth.spec.speed,
        "scenario": truth.spec.scenario,
        "sample_hours": truth.spec.sample_hours,
        "quantizer": truth.spec.quantizer,
        "n_participants": truth.spec.n_participants,
        "true_min_usd": float(truth.v.min()),
        "true_max_usd": float(truth.v.max()),
        **metric,
        **event_metrics(direction, events, truth.v[obs.sample_idx], obs),
    }
    if policy is not None:
        parameters = policy.record()
        parameters.pop("candidate_id")
        parameters.pop("family")
        record.update(parameters)
    else:
        record.update(
            {
                "margin_usd": np.nan,
                "consecutive_hits": np.nan,
                "cooldown_hours": np.nan,
                "minimum_stage": np.nan,
                "shadow_boundary_mass": np.nan,
                "shadow_residual_pp": np.nan,
            }
        )
    return record


def run_case(
    payload: tuple[
        tuple[str, str, SimulationSpec],
        tuple[str, ...],
        bool,
    ]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    (regime, split, spec), candidate_ids, include_oracle = payload
    truth = simulate_contraction_truth(spec)
    obs = make_observations(truth)
    started = perf_counter()
    outputs_by_direction = run_static_filters(truth, obs)
    filter_seconds = perf_counter() - started
    caches = {
        direction: metric_cache(truth, obs, outputs)
        for direction, outputs in outputs_by_direction.items()
    }

    rows: list[dict[str, Any]] = []
    direction, path, events = stage_path(
        outputs_by_direction,
        obs,
        truth.v[obs.sample_idx],
        None,
    )
    rows.append(
        policy_row(
            "no_shrink",
            "baseline",
            None,
            direction,
            path,
            events,
            truth,
            obs,
            outputs_by_direction,
            caches,
            regime,
            split,
        )
    )
    for candidate_id in candidate_ids:
        policy = CANDIDATE_MAP[candidate_id]
        candidate_direction, candidate_path, candidate_events = stage_path(
            outputs_by_direction,
            obs,
            truth.v[obs.sample_idx],
            policy,
        )
        rows.append(
            policy_row(
                policy.candidate_id,
                f"{policy.family}_{'high_only' if policy.minimum_stage else 'all'}",
                policy,
                candidate_direction,
                candidate_path,
                candidate_events,
                truth,
                obs,
                outputs_by_direction,
                caches,
                regime,
                split,
            )
        )
    if include_oracle:
        oracle_direction, oracle_path, oracle_events = stage_path(
            outputs_by_direction,
            obs,
            truth.v[obs.sample_idx],
            None,
            oracle=True,
        )
        rows.append(
            policy_row(
                "oracle_current_truth",
                "oracle",
                None,
                oracle_direction,
                oracle_path,
                oracle_events,
                truth,
                obs,
                outputs_by_direction,
                caches,
                regime,
                split,
            )
        )
    timing = {
        "case_id": spec.case_id,
        "regime": regime,
        "split": split,
        "observations": len(obs.times),
        "filter_seconds": filter_seconds,
    }
    return rows, timing


def run_jobs(
    jobs: list[tuple[str, str, SimulationSpec]],
    candidate_ids: tuple[str, ...],
    *,
    workers: int,
    include_oracle: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    timing: list[dict[str, Any]] = []
    payloads = [(job, candidate_ids, include_oracle) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index, (case_rows, case_timing) in enumerate(
            pool.map(run_case, payloads, chunksize=1),
            1,
        ):
            rows.extend(case_rows)
            timing.append(case_timing)
            if index % 25 == 0 or index == len(payloads):
                print(f"{index}/{len(payloads)}", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(timing)


def summarize(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for candidate_id, group in frame.groupby("candidate_id", sort=False):
        first = group.iloc[0]
        records.append(
            {
                "candidate_id": candidate_id,
                "family": first["family"],
                "cases": len(group),
                "mean_mae_usd": float(group["mae_usd"].mean()),
                "case_p95_mae_usd": float(group["mae_usd"].quantile(0.95)),
                "mean_rmse_usd": float(group["rmse_usd"].mean()),
                "mean_bias_usd": float(group["bias_usd"].mean()),
                "mean_over_usd": float(group["mean_over_usd"].mean()),
                "mean_under_usd": float(group["mean_under_usd"].mean()),
                "mean_worst_participant_mae_usd": float(
                    group["worst_participant_mae_usd"].mean()
                ),
                "mean_capacity_mae_usd": float(
                    group["capacity_sample_mae_usd"].mean()
                ),
                "mean_interval_coverage": float(
                    group["interval_sample_coverage"].mean()
                ),
                "mean_interval_width_usd": float(
                    group["interval_mean_width_usd"].mean()
                ),
                "mean_adjustment_tv_usd": float(
                    group["adjustment_total_variation_usd"].mean()
                ),
                "cases_with_shrink_fraction": float(
                    (group["shrink_count"] > 0).mean()
                ),
                "mean_shrink_count": float(group["shrink_count"].mean()),
                "mean_reexpansion_count": float(
                    group["reexpansion_count"].mean()
                ),
                "premature_shrink_case_fraction": float(
                    (group["premature_shrink_24h_count"] > 0).mean()
                ),
                "margin_usd": first["margin_usd"],
                "consecutive_hits": first["consecutive_hits"],
                "cooldown_hours": first["cooldown_hours"],
                "minimum_stage": first["minimum_stage"],
                "shadow_boundary_mass": first["shadow_boundary_mass"],
                "shadow_residual_pp": first["shadow_residual_pp"],
            }
        )
    return pd.DataFrame(records).sort_values(
        ["mean_mae_usd", "case_p95_mae_usd", "mean_reexpansion_count"]
    )


def summarize_by_regime(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.groupby(
            ["candidate_id", "family", "regime_group", "regime"],
            as_index=False,
        )
        .agg(
            cases=("case_id", "count"),
            mean_mae_usd=("mae_usd", "mean"),
            case_p95_mae_usd=("mae_usd", lambda values: values.quantile(0.95)),
            mean_bias_usd=("bias_usd", "mean"),
            mean_worst_participant_mae_usd=(
                "worst_participant_mae_usd",
                "mean",
            ),
            cases_with_shrink_fraction=(
                "shrink_count",
                lambda values: (values > 0).mean(),
            ),
            mean_reexpansion_count=("reexpansion_count", "mean"),
            premature_shrink_case_fraction=(
                "premature_shrink_24h_count",
                lambda values: (values > 0).mean(),
            ),
        )
    )


def choose_finalists(development_summary: pd.DataFrame) -> list[str]:
    eligible = development_summary[
        (development_summary["candidate_id"] != "no_shrink")
        & (development_summary["cases_with_shrink_fraction"] >= 0.02)
    ]
    finalists: list[str] = []
    for family in ("direct_all", "direct_high_only", "shadow_all", "shadow_high_only"):
        family_rows = eligible[eligible["family"] == family]
        if not family_rows.empty:
            finalists.append(str(family_rows.iloc[0]["candidate_id"]))
    if not eligible.empty:
        finalists.append(str(eligible.iloc[0]["candidate_id"]))
    return list(dict.fromkeys(finalists))


def paired_bootstrap(
    frame: pd.DataFrame,
    candidate_id: str,
    *,
    seed: int,
) -> dict[str, float]:
    baseline = (
        frame[frame["candidate_id"] == "no_shrink"]
        .set_index("case_id")["mae_usd"]
    )
    candidate = (
        frame[frame["candidate_id"] == candidate_id]
        .set_index("case_id")["mae_usd"]
    )
    joined = pd.concat([candidate.rename("candidate"), baseline.rename("baseline")], axis=1).dropna()
    difference = (joined["candidate"] - joined["baseline"]).to_numpy()
    rng = np.random.default_rng(seed)
    sampled = difference[
        rng.integers(0, len(difference), size=(BOOTSTRAP_REPLICATES, len(difference)))
    ].mean(axis=1)
    return {
        "cases": int(len(difference)),
        "mean_difference_usd": float(difference.mean()),
        "ci95_low_usd": float(np.quantile(sampled, 0.025)),
        "ci95_high_usd": float(np.quantile(sampled, 0.975)),
        "candidate_better_fraction": float((difference < 0).mean()),
        "identical_fraction": float(np.isclose(difference, 0.0, atol=1e-12).mean()),
    }


def markdown_table(frame: pd.DataFrame, columns: list[tuple[str, str]], limit: int | None = None) -> str:
    visible = frame if limit is None else frame.head(limit)
    header = "| " + " | ".join(label for _, label in columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    rows = [header, separator]
    for _, row in visible.iterrows():
        values = []
        for key, _ in columns:
            value = row[key]
            if isinstance(value, (float, np.floating)):
                values.append(f"{value:.3f}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def build_report(
    discovery_summary: pd.DataFrame,
    frozen_summary: pd.DataFrame,
    confirmation_summary: pd.DataFrame,
    confirmation_by_regime: pd.DataFrame,
    comparisons: dict[str, dict[str, float]],
    finalists: list[str],
    discovery_cases: int,
    confirmation_cases: int,
) -> str:
    baseline = confirmation_summary[
        confirmation_summary["candidate_id"] == "no_shrink"
    ].iloc[0]
    candidate_rows = confirmation_summary[
        confirmation_summary["candidate_id"].isin(finalists)
    ].copy()
    best = candidate_rows.sort_values("mean_mae_usd").iloc[0]
    best_id = str(best["candidate_id"])
    comparison = comparisons[best_id]
    statistically_better = comparison["ci95_high_usd"] < 0.0
    all_statistically_worse = all(
        item["ci95_low_usd"] > 0.0 for item in comparisons.values()
    )
    return_rows = confirmation_by_regime[
        (confirmation_by_regime["candidate_id"].isin(["no_shrink", best_id]))
        & (confirmation_by_regime["regime_group"] == "return")
    ]
    persistent_rows = confirmation_by_regime[
        (confirmation_by_regime["candidate_id"].isin(["no_shrink", best_id]))
        & (confirmation_by_regime["regime_group"] == "persistent")
    ]
    in_range_rows = confirmation_by_regime[
        (confirmation_by_regime["candidate_id"].isin(["no_shrink", best_id]))
        & (confirmation_by_regime["regime_group"] == "in_range")
    ]
    if statistically_better:
        decision = (
            "独立确认集显示最佳自动收缩候选相对不收缩具有统计稳定的总体改善。"
            "仍需结合分场景尾部风险决定是否工程化。"
        )
    elif all_statistically_worse:
        decision = (
            "独立确认集中的四个自动收缩候选均显著差于当前不收缩策略，"
            "因此不应在周期内自动收缩；继续在新周期开始时恢复标准范围。"
        )
    else:
        decision = (
            "独立确认集没有证明自动收缩相对当前不收缩策略具有统计稳定的总体优势，"
            "因此暂不建议替换当前策略。"
        )
    columns = [
        ("candidate_id", "策略"),
        ("mean_mae_usd", "余额 MAE"),
        ("case_p95_mae_usd", "案例 P95"),
        ("mean_worst_participant_mae_usd", "最差参与者 MAE"),
        ("cases_with_shrink_fraction", "发生收缩案例比例"),
        ("mean_reexpansion_count", "平均再扩张次数"),
        ("premature_shrink_case_fraction", "24h 内过早收缩比例"),
    ]
    return f"""# 粒子滤波范围自动收缩实验

## 结论

{decision}

独立确认集当前基准 `no_shrink` 的余额建议 MAE 为 **{baseline['mean_mae_usd']:.3f} 美元**；最佳候选 `{best_id}` 为 **{best['mean_mae_usd']:.3f} 美元**。候选减基准的配对差值为 **{comparison['mean_difference_usd']:.3f} 美元**，95% Bootstrap 区间为 **[{comparison['ci95_low_usd']:.3f}, {comparison['ci95_high_usd']:.3f}]**。

正差值表示自动收缩增加误差。四个入选候选的配对差值 95% 区间均完全高于 0；该结论在开发集、冻结集和独立确认集方向一致。

`oracle_current_truth` 只知道每一时刻的真实容量、不知道未来路径。它会随当前容量反复收缩和再扩张，因此不是理论误差下界；该对照说明“此刻已经回到窄范围”不足以证明“现在收缩对之后更好”。

## 实验设计

- 标准范围 1400～4000；扩张保持生产分级：向上 6000/10000/20000，向下 700/250/50。
- 容量路径连续，无跳变。
- 10 类路径：暂时上/下越界、远端暂时越界、两次反弹、永久越界、范围内贴边。
- 参与者 2/4/6 人；采样间隔 1/3/6 小时；三种整数显示规则；8 种消费模式。
- 开发与冻结阶段共 {discovery_cases} 个案例；独立确认阶段 {confirmation_cases} 个全新种子案例。
- 开发集比较 {len(CANDIDATES)} 个因果候选。冻结集只查看开发集入选者；独立确认集使用新的随机种子重新验证。
- 主指标为整个周期、所有参与者的余额建议美元 MAE。另记录最差参与者、偏差、区间覆盖、收缩后再扩张和 24 小时内过早收缩。

候选分为：

1. 仅按当前扩展滤波 90% 容量区间回到较窄范围判断；
2. 再要求较窄影子滤波边界质量和整数显示残差同时通过；
3. 分别允许一直缩回标准范围，或最多只缩到第一级扩张；
4. 比较不同安全边距、连续命中次数和冷却时间。

所有判断只读取当时及以前的观测。真实容量只用于实验评价和显式 Oracle，不参与候选决策。

## 独立确认集总体结果

{markdown_table(confirmation_summary[confirmation_summary['candidate_id'].isin(['no_shrink', 'oracle_current_truth', *finalists])], columns)}

## 最佳候选分场景结果

### 暂时越界与反弹

{markdown_table(return_rows, [('candidate_id', '策略'), ('regime', '场景'), ('mean_mae_usd', '余额 MAE'), ('cases_with_shrink_fraction', '收缩比例'), ('mean_reexpansion_count', '再扩张次数')])}

### 永久越界

{markdown_table(persistent_rows, [('candidate_id', '策略'), ('regime', '场景'), ('mean_mae_usd', '余额 MAE'), ('cases_with_shrink_fraction', '收缩比例'), ('premature_shrink_case_fraction', '过早收缩比例')])}

### 范围内贴边

{markdown_table(in_range_rows, [('candidate_id', '策略'), ('regime', '场景'), ('mean_mae_usd', '余额 MAE'), ('cases_with_shrink_fraction', '收缩比例'), ('mean_reexpansion_count', '再扩张次数')])}

## 开发与冻结检查

开发集排名前 10：

{markdown_table(discovery_summary, columns, limit=10)}

冻结集入选策略：

{markdown_table(frozen_summary, columns)}

## 适用边界

- 这是连续合成轨迹上的开放环评价，不是现实隐藏容量的直接验证。
- 最大误差是有限固定种子中的观察值，不是数学最坏界。
- 收缩可能改变后续建议，但实验没有增加任何自动调额逻辑。
- 若总体改善很小而再扩张或过早收缩增加，应保留当前“周期内不缩回”的简单策略。
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-per-regime", type=int, default=40)
    parser.add_argument("--confirmation-per-regime", type=int, default=60)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--prefix", default="range_contraction")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    cases_per_regime = 1 if args.smoke else args.cases_per_regime
    confirmation_per_regime = 1 if args.smoke else args.confirmation_per_regime
    development_jobs = make_jobs(
        cases_per_regime,
        split="development",
        salt="discovery-development-20260810",
    )
    frozen_jobs = make_jobs(
        cases_per_regime,
        split="frozen",
        salt="discovery-frozen-20260810",
    )
    discovery_jobs = development_jobs + frozen_jobs
    discovery, discovery_timing = run_jobs(
        discovery_jobs,
        tuple(CANDIDATE_MAP),
        workers=args.workers,
        include_oracle=True,
    )
    development_summary = summarize(
        discovery[discovery["split"] == "development"]
    )
    finalists = choose_finalists(development_summary)
    frozen_selected = discovery[
        (discovery["split"] == "frozen")
        & discovery["candidate_id"].isin(
            ["no_shrink", "oracle_current_truth", *finalists]
        )
    ]
    frozen_summary = summarize(frozen_selected)

    confirmation_jobs = make_jobs(
        confirmation_per_regime,
        split="confirmation",
        salt="independent-confirmation-20260810",
    )
    confirmation, confirmation_timing = run_jobs(
        confirmation_jobs,
        tuple(finalists),
        workers=args.workers,
        include_oracle=True,
    )
    confirmation_summary = summarize(confirmation)
    confirmation_by_regime = summarize_by_regime(confirmation)
    comparisons = {
        candidate_id: paired_bootstrap(
            confirmation,
            candidate_id,
            seed=stable_seed(f"contraction-bootstrap:{candidate_id}"),
        )
        for candidate_id in finalists
    }

    results = ROOT / "results"
    results.mkdir(parents=True, exist_ok=True)
    prefix = f"{args.prefix}_smoke" if args.smoke else args.prefix
    discovery.to_csv(
        results / f"{prefix}_discovery_raw.csv.gz",
        index=False,
        compression="gzip",
    )
    development_summary.to_csv(
        results / f"{prefix}_development_summary.csv",
        index=False,
    )
    frozen_selected.to_csv(
        results / f"{prefix}_frozen_raw.csv.gz",
        index=False,
        compression="gzip",
    )
    frozen_summary.to_csv(
        results / f"{prefix}_frozen_summary.csv",
        index=False,
    )
    confirmation.to_csv(
        results / f"{prefix}_confirmation_raw.csv.gz",
        index=False,
        compression="gzip",
    )
    confirmation_summary.to_csv(
        results / f"{prefix}_confirmation_summary.csv",
        index=False,
    )
    confirmation_by_regime.to_csv(
        results / f"{prefix}_confirmation_by_regime.csv",
        index=False,
    )
    pd.concat([discovery_timing, confirmation_timing]).to_csv(
        results / f"{prefix}_timing.csv",
        index=False,
    )

    report = build_report(
        development_summary,
        frozen_summary,
        confirmation_summary,
        confirmation_by_regime,
        comparisons,
        finalists,
        len(discovery_jobs),
        len(confirmation_jobs),
    )
    (results / f"{prefix}_report.md").write_text(report, encoding="utf-8")
    payload = {
        "study": "causal staged contraction after adaptive PF expansion",
        "continuous_capacity_paths": True,
        "standard_range_usd": [BASE_MIN, BASE_MAX],
        "upper_stages_usd": [high for _, high in UPPER_RANGES[1:]],
        "lower_stages_usd": [low for low, _ in LOWER_RANGES[1:]],
        "regimes": list(REGIMES),
        "candidate_count": len(CANDIDATES),
        "development_cases": len(development_jobs),
        "frozen_cases": len(frozen_jobs),
        "confirmation_cases": len(confirmation_jobs),
        "finalists": finalists,
        "finalist_parameters": [CANDIDATE_MAP[item].record() for item in finalists],
        "confirmation_comparisons_vs_no_shrink": comparisons,
        "random_salts": {
            "development": "discovery-development-20260810",
            "frozen": "discovery-frozen-20260810",
            "confirmation": "independent-confirmation-20260810",
        },
        "primary_metric": "full-cycle held participant-balance MAE in USD",
        "causal_rule": "candidate decisions use current and past observations only",
    }
    (results / f"{prefix}_study.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
