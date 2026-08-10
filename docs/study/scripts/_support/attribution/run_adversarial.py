"""Active search for difficult rate paths and participant event orderings.

This is not a proof of the global worst case. It is a reproducible adaptive search
that supplies lower bounds on attainable error under the fixed-offset ideal model.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from algorithms import (
    adaptive_multiphase,
    estimate_total_center,
    global_proportional,
    set_attribution,
    moving_local_window,
    multiphase_window,
    tv_attribution,
)
ROOT = Path(__file__).resolve().parents[3]
from metrics import evaluate
from models import CycleData
from quantizers import fixed_offset_quantize
from simulate import WEEK_MINUTES, X_MIN, X_MAX

RESULTS = ROOT / "results/raw/adversarial"
RESULTS.mkdir(parents=True, exist_ok=True)


@dataclass
class Genome:
    users: np.ndarray
    rates: np.ndarray
    costs: np.ndarray
    gaps: np.ndarray
    theta: float
    sampling_index: int

    def copy(self):
        return Genome(
            self.users.copy(),
            self.rates.copy(),
            self.costs.copy(),
            self.gaps.copy(),
            float(self.theta),
            int(self.sampling_index),
        )


def random_genome(rng, n_events=64):
    return Genome(
        rng.integers(0, 2, n_events, dtype=np.int8),
        rng.integers(0, 2, n_events, dtype=np.int8),
        rng.integers(-2, 3, n_events, dtype=np.int8),
        rng.integers(-2, 3, n_events, dtype=np.int8),
        float(rng.random()),
        int(rng.integers(0, 4)),
    )


def mutate(parent: Genome, rng):
    g = parent.copy()
    n = len(g.users)
    for arr, low, high in [
        (g.users, 0, 2),
        (g.rates, 0, 2),
        (g.costs, -2, 3),
        (g.gaps, -2, 3),
    ]:
        k = int(rng.integers(1, 7))
        idx = rng.choice(n, k, replace=False)
        if arr is g.costs or arr is g.gaps:
            arr[idx] = np.clip(arr[idx] + rng.choice([-1, 1], k), low, high - 1)
        else:
            arr[idx] = 1 - arr[idx]
    if rng.random() < 0.65:
        g.theta = float(np.mod(g.theta + rng.normal(0, 0.12), 1.0))
    if rng.random() < 0.25:
        g.sampling_index = int(rng.integers(0, 4))
    # Ensure both participants occur.
    if np.all(g.users == g.users[0]):
        g.users[int(rng.integers(0, n))] = 1 - g.users[0]
    return g


def crossover(a: Genome, b: Genome, rng):
    n = len(a.users)
    mask = rng.random(n) < 0.5
    child = a.copy()
    child.users[mask] = b.users[mask]
    child.rates[mask] = b.rates[mask]
    child.costs[mask] = b.costs[mask]
    child.gaps[mask] = b.gaps[mask]
    child.theta = a.theta if rng.random() < 0.5 else b.theta
    child.sampling_index = a.sampling_index if rng.random() < 0.5 else b.sampling_index
    return child


def genome_cycle(g: Genome, seed: int, target=60.0):
    n = len(g.users)
    # Search event spacing as well as participant ordering. Positive gap weights
    # produce strictly increasing deterministic times, so a genome has a fixed
    # objective value and repeated elite evaluation is noise-free.
    gap_weights = np.exp(0.9 * g.gaps.astype(float))
    cumulative_gaps = np.cumsum(gap_weights)
    times = (cumulative_gaps - 0.5 * gap_weights) / cumulative_gaps[-1] * WEEK_MINUTES
    users = g.users.astype(int)
    inverse = np.where(g.rates == 0, X_MIN, X_MAX).astype(float)
    raw_cost = np.exp(0.8 * g.costs.astype(float))
    # A weak deterministic modulation avoids all-equal event costs without injecting
    # another random search dimension.
    raw_cost *= 0.8 + 0.4 * (1 + np.sin(2 * np.pi * np.arange(n) / max(n, 1))) / 2
    costs = raw_cost * (target / max(np.sum(raw_cost * inverse), 1e-12))
    true_q = costs * inverse
    sampling_options = [10.0, 30.0, 60.0, 180.0]
    sampling = sampling_options[g.sampling_index]
    sample_times = np.arange(0.0, WEEK_MINUTES + 0.5 * sampling, sampling)
    if sample_times[-1] < WEEK_MINUTES:
        sample_times = np.r_[sample_times, WEEK_MINUTES]
    else:
        sample_times[-1] = WEEK_MINUTES
    cumulative = np.cumsum(true_q)
    idx = np.searchsorted(times, sample_times, side="right") - 1
    progress = np.where(idx >= 0, cumulative[np.maximum(idx, 0)], 0.0)
    z = fixed_offset_quantize(progress, g.theta)
    return CycleData(
        WEEK_MINUTES,
        times,
        users,
        costs,
        inverse,
        true_q,
        sample_times,
        progress,
        z,
        2,
        "fixed_offset",
        {"theta": float(g.theta)},
        "active_adversarial_search",
        int(seed),
        {
            "rate_process": "searched_event_extremes",
            "schedule": "searched_order",
            "sampling_minutes": sampling,
            "target_progress": target,
            "n_events": n,
        },
    )


def selected_result(cycle, target, selected):
    pcenter, _ = estimate_total_center(cycle)
    if target == "global":
        return global_proportional(cycle, pcenter)
    if target == "phase":
        cfg = selected["static_accuracy"]
        return multiphase_window(
            cycle,
            cfg["width"],
            cfg["n_phases"],
            pcenter,
            aggregation=cfg["aggregation"],
            phase_scheme=cfg["phase_scheme"],
            name="phase_selected",
        )
    if target == "adaptive":
        cfg = selected["adaptive_accuracy"]
        return adaptive_multiphase(
            cycle,
            tuple(w for w in [1,2,3,4,5,7,10,15,20,30] if w <= cfg["max_width"]),
            cfg["threshold"],
            cfg["n_phases"],
            pcenter,
            cfg["aggregation"],
            cfg["phase_scheme"],
            name="adaptive_selected",
        )
    if target == "moving":
        cfg = selected["moving_centered_accuracy"]
        return moving_local_window(
            cycle, cfg["width"], cfg["orientation"], pcenter, name="moving_selected"
        )
    if target == "tv":
        return tv_attribution(cycle, name="tv")
    if target in ("set", "set_width"):
        return set_attribution(cycle, selector="midpoint_lex", name="set_midpoint_lex")
    raise ValueError(target)


def score(g, target, selected, seed):
    cycle = genome_cycle(g, seed)
    result = selected_result(cycle, target, selected)
    row, _ = evaluate(cycle, result)
    if not row.get("success", False):
        return -np.inf, row, cycle
    # The business-critical direction is over-attribution; a separate target
    # actively searches irreducible set width. Tiny secondary terms break ties.
    if target == "set_width":
        value = float(row["interval_width_max"] + 1e-4 * row["max_over"])
    else:
        value = float(row["max_over"] + 1e-4 * row["max_abs"])
    return value, row, cycle


def encode_genome(g):
    return json.dumps(
        {
            "users": g.users.astype(int).tolist(),
            "rates": g.rates.astype(int).tolist(),
            "cost_levels": g.costs.astype(int).tolist(),
            "gap_levels": g.gaps.astype(int).tolist(),
            "theta": g.theta,
            "sampling_index": g.sampling_index,
        },
        separators=(",", ":"),
    )


def run_target(target, selected, seed, population=64, generations=48, restart=0):
    rng = np.random.default_rng(seed)
    pop = [random_genome(rng) for _ in range(population)]
    rows = []
    best = None
    best_score = -np.inf
    evaluations = 0
    for generation in range(generations):
        evaluated = []
        for index, genome in enumerate(pop):
            value, metric, cycle = score(genome, target, selected, seed + evaluations + 1)
            evaluations += 1
            evaluated.append((value, genome, metric, cycle))
            if value > best_score:
                best_score = value
                best = (genome.copy(), metric.copy(), cycle)
        evaluated.sort(key=lambda x: x[0], reverse=True)
        rows.append(
            {
                "target": target,
                "restart": int(restart),
                "search_seed": int(seed),
                "generation": generation,
                "evaluations": evaluations,
                "best_score": float(evaluated[0][0]),
                "median_score": float(np.median([x[0] for x in evaluated])),
            }
        )
        elite = [x[1] for x in evaluated[: max(8, population // 8)]]
        new_pop = [e.copy() for e in elite[:4]]
        while len(new_pop) < population:
            if rng.random() < 0.35:
                a, b = rng.choice(elite, 2, replace=True)
                child = crossover(a, b, rng)
                child = mutate(child, rng)
            else:
                child = mutate(elite[int(rng.integers(0, len(elite)))], rng)
            new_pop.append(child)
        pop = new_pop
    assert best is not None
    genome, metric, cycle = best
    # Evaluate every final method on the discovered candidate.
    all_metrics = []
    for alg in ["global", "phase", "adaptive", "moving", "tv", "set"]:
        result = selected_result(cycle, alg, selected)
        row, _ = evaluate(cycle, result)
        row.update(
            {
                "search_target": target,
                "restart": int(restart),
                "search_seed": int(seed),
                "evaluations": evaluations,
                "genome": encode_genome(genome),
                "theta": genome.theta,
                "sampling_minutes": cycle.metadata["sampling_minutes"],
            }
        )
        all_metrics.append(row)
    return rows, all_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", type=int, default=64)
    parser.add_argument("--generations", type=int, default=48)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--restarts", type=int, default=3)
    args = parser.parse_args()
    selected = json.loads((ROOT / "results/raw/phase/selected_phase_configs.json").read_text())
    targets = ["global", "phase", "adaptive", "moving"]
    search_jobs = [
        (k, target, restart)
        for k, target in enumerate(targets)
        for restart in range(max(1, int(args.restarts)))
    ]
    output = Parallel(n_jobs=max(1, int(args.jobs)), verbose=8)(
        delayed(run_target)(
            target,
            selected,
            20261011 + 100000 * k + 1009 * restart,
            args.population,
            args.generations,
            restart,
        )
        for k, target, restart in search_jobs
    )
    traces, metrics = [], []
    for trace, result in output:
        traces.extend(trace)
        metrics.extend(result)
    pd.DataFrame(traces).to_csv(RESULTS / "adversarial_search_trace.csv", index=False)
    pd.DataFrame(metrics).to_csv(RESULTS / "adversarial_search_best.csv", index=False)


if __name__ == "__main__":
    main()
