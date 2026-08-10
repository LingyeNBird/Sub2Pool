"""Strict target-time / decision-time delay experiment.

For a target snapshot T and a later decision snapshot D >= T, every estimator is
constructed only from events and aggregate snapshots available by D.  The loss is
computed against participant resource consumption accumulated only through T.
This separates real information delay from the retrospective support-context proxy
used by the offline window-development study.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from algorithms import (
    _aggregate_phase_vectors,
    _proxy_event_progress,
    estimate_total_center,
    phase_offsets,
    project_simplex,
)
from scenarios import main_spec, seed_jobs
ROOT = Path(__file__).resolve().parents[3]
from feasible import tv_rate_estimate
from models import aggregate_cycle, truncate_cycle
import yaml
CFG = yaml.safe_load((ROOT / "config" / "attribution_study.yaml").read_text())
from simulate import simulate_cycle

RESULTS = ROOT / "results/raw/delay"
RESULTS.mkdir(parents=True, exist_ok=True)


def _single_phase_event_alloc(cycle, width, phase, pcenter, event_progress):
    """Event-level form of one fixed progress-window partition."""
    n_events = len(cycle.event_times)
    if pcenter <= 0 or n_events == 0:
        return np.zeros(n_events), {
            "full_coverage_fraction": 1.0,
            "empty_block_fraction": 0.0,
        }
    m0 = int(np.floor(-phase / width)) - 1
    m1 = int(np.ceil((pcenter - phase) / width)) + 1
    intersections = phase + width * np.arange(m0, m1 + 1)
    boundaries = np.unique(
        np.r_[0.0, intersections[(intersections > 0) & (intersections < pcenter)], pcenter]
    )
    mids = 0.5 * (boundaries[:-1] + boundaries[1:])
    block_idx = np.clip(
        np.searchsorted(boundaries, event_progress, side="right") - 1,
        0,
        len(boundaries) - 2,
    )
    alloc = np.zeros(n_events, float)
    empty = 0
    full_width = 0.0
    for block, (left, right) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
        delta = float(right - left)
        if abs(delta - width) <= 1e-9:
            full_width += delta
        mask = block_idx == block
        if not np.any(mask):
            empty += 1
            nearest = int(np.argmin(np.abs(event_progress - mids[block])))
            alloc[nearest] += delta
        else:
            alloc[mask] += delta * cycle.event_costs[mask] / max(
                float(cycle.event_costs[mask].sum()), 1e-12
            )
    return alloc, {
        "full_coverage_fraction": float(full_width / max(pcenter, 1e-12)),
        "empty_block_fraction": float(empty / max(len(boundaries) - 1, 1)),
    }


def _participant_sum(cycle, event_values, mask=None):
    if mask is None:
        mask = np.ones(len(event_values), bool)
    return np.bincount(
        cycle.event_users[mask],
        weights=np.asarray(event_values)[mask],
        minlength=cycle.n_users,
    ).astype(float)


def _reconcile_target(raw_target, raw_full, final_full, target_cost, full_cost):
    """Map a target subtotal consistently into the reconciled full participant total.

    The ratio rule guarantees that when target time equals decision time, the target
    estimate equals the reported full estimate.  The cost fraction is a deterministic
    fallback only if an aggregation produces a zero raw participant total.
    """
    raw_target = np.asarray(raw_target, float)
    raw_full = np.asarray(raw_full, float)
    final_full = np.asarray(final_full, float)
    frac = np.divide(
        raw_target,
        raw_full,
        out=np.divide(target_cost, full_cost, out=np.zeros_like(target_cost), where=full_cost > 1e-12),
        where=raw_full > 1e-12,
    )
    return final_full * np.clip(frac, 0.0, 1.0)


def _phase_target(
    cycle,
    target_time,
    width,
    n_phases,
    phase_scheme,
    aggregation,
    pcenter,
):
    phases = phase_offsets(float(width), int(n_phases), str(phase_scheme))
    ep = _proxy_event_progress(cycle, pcenter)
    target_mask = cycle.event_times <= target_time + 1e-12
    full_vectors, target_vectors, diagnostics = [], [], []
    for phase in phases:
        alloc, diag = _single_phase_event_alloc(cycle, float(width), float(phase), pcenter, ep)
        full_vectors.append(_participant_sum(cycle, alloc))
        target_vectors.append(_participant_sum(cycle, alloc, target_mask))
        diagnostics.append(diag)
    raw_full = _aggregate_phase_vectors(full_vectors, aggregation)
    raw_target = _aggregate_phase_vectors(target_vectors, aggregation)
    final_full = project_simplex(raw_full, pcenter)
    full_cost = cycle.cost_user_totals
    target_cost = np.bincount(
        cycle.event_users[target_mask],
        weights=cycle.event_costs[target_mask],
        minlength=cycle.n_users,
    ).astype(float)
    q = _reconcile_target(raw_target, raw_full, final_full, target_cost, full_cost)
    est = np.vstack(full_vectors)
    q25, q75 = np.quantile(est, [0.25, 0.75], axis=0)
    return q, {
        "width": float(width),
        "n_phases": int(len(phases)),
        "phase_dispersion": float(np.max(q75 - q25)),
        "aggregation": str(aggregation),
        "phase_scheme": str(phase_scheme),
    }


def _adaptive_target(cycle, target_time, cfg, pcenter):
    trace = []
    chosen = None
    for width in [w for w in [1,2,3,4,5,7,10,15,20,30] if w <= int(cfg["max_width"])]:
        q, meta = _phase_target(
            cycle,
            target_time,
            width,
            cfg["n_phases"],
            cfg["phase_scheme"],
            cfg["aggregation"],
            pcenter,
        )
        trace.append((width, meta["phase_dispersion"]))
        chosen = (q, meta)
        if meta["phase_dispersion"] <= float(cfg["threshold"]):
            break
    assert chosen is not None
    q, meta = chosen
    return q, {
        **meta,
        "selected_width": float(meta["width"]),
        "threshold": float(cfg["threshold"]),
        "trace": trace,
    }


def _moving_target(cycle, target_time, width, orientation, pcenter):
    ep = _proxy_event_progress(cycle, pcenter)
    q_event = np.zeros(len(ep), float)
    for j, progress in enumerate(ep):
        if orientation == "backward":
            left, right = progress - width, progress
        elif orientation == "centered":
            left, right = progress - width / 2.0, progress + width / 2.0
        elif orientation == "forward":
            left, right = progress, progress + width
        else:
            raise ValueError(orientation)
        left = max(0.0, float(left))
        right = min(float(pcenter), float(right))
        mask = (ep >= left - 1e-12) & (ep <= right + 1e-12)
        local_cost = float(cycle.event_costs[mask].sum())
        q_event[j] = cycle.event_costs[j] * (right - left) / max(local_cost, 1e-12)
    target_mask = cycle.event_times <= target_time + 1e-12
    raw_full = _participant_sum(cycle, q_event)
    raw_target = _participant_sum(cycle, q_event, target_mask)
    final_full = project_simplex(raw_full, pcenter)
    full_cost = cycle.cost_user_totals
    target_cost = np.bincount(
        cycle.event_users[target_mask],
        weights=cycle.event_costs[target_mask],
        minlength=cycle.n_users,
    ).astype(float)
    return _reconcile_target(raw_target, raw_full, final_full, target_cost, full_cost), {
        "width": float(width), "orientation": orientation
    }


def _tv_target(cycle, target_time):
    fit = tv_rate_estimate(cycle)
    if not fit.success:
        return None, {"message": fit.message}
    agg = aggregate_cycle(cycle)
    xhat = np.asarray(fit.metadata["xhat"], float)
    interval_idx = np.searchsorted(agg.interval_end_times, cycle.event_times, side="left")
    interval_idx = np.clip(interval_idx, 0, len(xhat) - 1)
    qevent = cycle.event_costs * xhat[interval_idx]
    mask = cycle.event_times <= target_time + 1e-12
    return _participant_sum(cycle, qevent, mask), {
        "tv": float(fit.metadata["tv"]),
        "n_intervals": int(fit.metadata["n_intervals"]),
    }


def _global_target(cycle, target_time, pcenter):
    mask = cycle.event_times <= target_time + 1e-12
    target_cost = np.bincount(
        cycle.event_users[mask],
        weights=cycle.event_costs[mask],
        minlength=cycle.n_users,
    ).astype(float)
    xbar = pcenter / max(float(cycle.event_costs.sum()), 1e-12)
    return target_cost * xbar, {"mean_inverse_rate": float(xbar)}


def _loss_row(q, truth):
    e = np.asarray(q, float) - np.asarray(truth, float)
    return {
        "mae": float(np.mean(np.abs(e))),
        "max_abs": float(np.max(np.abs(e))),
        "max_over": float(np.max(np.maximum(e, 0.0))),
        "max_under": float(np.max(np.maximum(-e, 0.0))),
        "total_error": float(np.sum(e)),
        "estimate_json": json.dumps(np.asarray(q, float).tolist(), separators=(",", ":")),
        "truth_json": json.dumps(np.asarray(truth, float).tolist(), separators=(",", ":")),
    }


def _one_cycle(family, seed, selected, target_fractions, requested_lags):
    cycle = simulate_cycle(main_spec(family, int(seed)))
    rows = []
    for target_fraction in target_fractions:
        target_nominal = float(target_fraction) * cycle.duration_minutes
        target_idx = int(np.searchsorted(cycle.sample_times, target_nominal, side="left"))
        target_idx = int(np.clip(target_idx, 1, len(cycle.sample_times) - 1))
        target_time = float(cycle.sample_times[target_idx])
        target_cycle = truncate_cycle(cycle, target_idx)
        truth = target_cycle.true_user_totals
        for requested_lag in requested_lags:
            decision_nominal = target_time + float(requested_lag)
            decision_idx = int(np.searchsorted(cycle.sample_times, decision_nominal, side="left"))
            decision_idx = int(np.clip(decision_idx, target_idx, len(cycle.sample_times) - 1))
            decision_time = float(cycle.sample_times[decision_idx])
            decision = truncate_cycle(cycle, decision_idx)
            pcenter, total_interval = estimate_total_center(decision)
            methods = []
            methods.append(("global_delayed",) + _global_target(decision, target_time, pcenter))

            for key, name in [
                ("static_accuracy", "phase_accuracy_delayed"),
                ("static_tail", "phase_tail_delayed"),
            ]:
                cfg = selected[key]
                q, meta = _phase_target(
                    decision,
                    target_time,
                    cfg["width"],
                    cfg["n_phases"],
                    cfg["phase_scheme"],
                    cfg["aggregation"],
                    pcenter,
                )
                methods.append((name, q, meta))

            for key, name in [
                ("adaptive_accuracy", "adaptive_accuracy_delayed"),
                ("adaptive_tail", "adaptive_tail_delayed"),
            ]:
                q, meta = _adaptive_target(decision, target_time, selected[key], pcenter)
                methods.append((name, q, meta))

            for orientation in ("backward", "centered", "forward"):
                cfg = selected[f"moving_{orientation}_accuracy"]
                q, meta = _moving_target(
                    decision, target_time, cfg["width"], orientation, pcenter
                )
                methods.append((f"moving_{orientation}_delayed", q, meta))

            qtv, mtv = _tv_target(decision, target_time)
            if qtv is not None:
                methods.append(("tv_delayed", qtv, mtv))

            for name, q, meta in methods:
                row = {
                    "scenario": family,
                    "seed": int(seed),
                    "algorithm": name,
                    "n_users": cycle.n_users,
                    "n_events_full": len(cycle.event_times),
                    "target_fraction": float(target_fraction),
                    "target_time": target_time,
                    "decision_time": decision_time,
                    "requested_lag_minutes": float(requested_lag),
                    "actual_lag_minutes": decision_time - target_time,
                    "sampling_minutes": cycle.metadata["sampling_minutes"],
                    "true_target_total": float(np.sum(truth)),
                    "estimated_decision_total": float(pcenter),
                    "decision_total_interval_width": (
                        float(total_interval[1] - total_interval[0])
                        if total_interval is not None else np.nan
                    ),
                    "selected_width": float(meta.get("selected_width", meta.get("width", np.nan))),
                    "phase_dispersion": float(meta.get("phase_dispersion", np.nan)),
                    "orientation": meta.get("orientation", ""),
                }
                row.update(_loss_row(q, truth))
                rows.append(row)
    return rows


def _add_revision_metrics(df):
    df = df.sort_values(
        ["scenario", "seed", "target_fraction", "algorithm", "actual_lag_minutes"]
    ).copy()
    prev_map = {}
    zero_map = {}
    inc, from_zero = [], []
    for row in df.itertuples(index=False):
        key = (row.scenario, int(row.seed), float(row.target_fraction), row.algorithm)
        q = np.asarray(json.loads(row.estimate_json), float)
        if key not in zero_map:
            zero_map[key] = q.copy()
            prev_map[key] = q.copy()
            inc.append(0.0)
            from_zero.append(0.0)
        else:
            inc.append(float(np.max(np.abs(q - prev_map[key]))))
            from_zero.append(float(np.max(np.abs(q - zero_map[key]))))
            prev_map[key] = q.copy()
    df["revision_increment_linf"] = inc
    df["revision_from_zero_linf"] = from_zero
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--per-family", type=int, default=int(CFG["sample_sizes"]["delay_cycles_per_family"]))
    args = parser.parse_args()
    families = ["constant_mixed","smooth_mixed","piecewise_mixed","smooth_staggered","jump_staggered","bursty_mean_reverting"]
    target_fractions = [0.25, 0.50, 0.75]
    requested_lags = [0.0, 360.0, 720.0, 1440.0, 2880.0]
    selected = json.loads((ROOT / "results/raw/phase/selected_phase_configs.json").read_text())
    jobs = seed_jobs(families, int(args.per_family), int(CFG["seeds"]["delay"]))
    pd.DataFrame(jobs, columns=["scenario", "seed"]).to_csv(
        RESULTS / "online_delay_manifest.csv", index=False
    )
    output = Parallel(n_jobs=args.jobs, verbose=8)(
        delayed(_one_cycle)(f, s, selected, target_fractions, requested_lags)
        for f, s in jobs
    )
    df = pd.DataFrame([r for group in output for r in group])
    df = _add_revision_metrics(df)
    df.to_csv(RESULTS / "online_delay_metrics.csv.gz", index=False, compression="gzip")
    summary = (
        df.groupby(["algorithm", "requested_lag_minutes"])
        .agg(
            cycles=("seed", "count"),
            MAE_mean=("mae", "mean"),
            max_over_P95=("max_over", lambda x: x.quantile(0.95)),
            max_abs_P95=("max_abs", lambda x: x.quantile(0.95)),
            total_abs_mean=("total_error", lambda x: np.mean(np.abs(x))),
            revision_increment_mean=("revision_increment_linf", "mean"),
            revision_from_zero_P95=("revision_from_zero_linf", lambda x: x.quantile(0.95)),
            actual_lag_mean=("actual_lag_minutes", "mean"),
        )
        .reset_index()
    )
    summary.to_csv(RESULTS / "online_delay_summary.csv", index=False)


if __name__ == "__main__":
    main()
