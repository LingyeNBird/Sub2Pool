"""Attribution estimators used in the V2 study."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
import numpy as np

from models import CycleData, aggregate_cycle
from feasible import identification_region, total_interval, tv_rate_estimate, _project_box_sum
from simulate import X_MIN, X_MAX

@dataclass
class AttributionResult:
    name: str
    estimate: np.ndarray
    success: bool = True
    message: str = "ok"
    lower: np.ndarray | None = None
    upper: np.ndarray | None = None
    metadata: dict | None = None


def project_simplex(v, z):
    """Euclidean projection onto the nonnegative simplex of mass z."""
    v = np.asarray(v, float)
    z = float(z)
    if len(v) == 0:
        return v.copy()
    if z <= 0:
        return np.zeros_like(v)
    u = np.sort(v)[::-1]
    css = np.cumsum(u) - z
    ind = np.arange(1, len(v) + 1)
    mask = u - css / ind > 0
    if not np.any(mask):
        return np.full_like(v, z / len(v))
    rho = ind[mask][-1]
    theta = css[mask][-1] / rho
    return np.maximum(v - theta, 0.0)


def reconcile_total(raw, total, rule="euclidean"):
    raw = np.asarray(raw, float)
    total = float(max(total, 0.0))
    if rule == "euclidean":
        return project_simplex(raw, total)
    if rule == "proportional":
        positive = np.maximum(raw, 0.0)
        if positive.sum() <= 1e-15:
            return np.full_like(positive, total / max(len(positive), 1))
        return positive * total / positive.sum()
    raise ValueError(f"unknown reconciliation rule: {rule}")


def estimate_total_center(cycle, known_theta=None, obs_slack=0.0, exact=False):
    """Estimate terminal continuous progress.

    The default is a cheap deterministic interval from the final integer plus
    physical cost bounds. ``exact=True`` solves the full fixed-offset feasibility
    LP and is used only in set diagnostics, not the 100k-cycle fast benchmark.
    """
    if exact:
        interval = total_interval(cycle, known_theta, obs_slack)
        if interval is not None:
            return 0.5 * (interval[0] + interval[1]), interval
    cost_total = float(cycle.event_costs.sum())
    physical_lo, physical_hi = cost_total * X_MIN, cost_total * X_MAX
    z = float(cycle.observed_final)
    if known_theta is None:
        quant_lo, quant_hi = max(0.0, z - 1.0 - obs_slack), z + 1.0 + obs_slack
    else:
        quant_lo = max(0.0, z - float(known_theta) - obs_slack)
        quant_hi = z + 1.0 - float(known_theta) + obs_slack
    lo, hi = max(physical_lo, quant_lo), min(physical_hi, quant_hi)
    if lo <= hi:
        return 0.5 * (lo + hi), (float(lo), float(hi))
    # Explicit model-mismatch fallback: clip the display to physical bounds.
    guess = float(np.clip(z, physical_lo, physical_hi))
    return guess, None


def global_proportional(cycle, pcenter=None, reconciliation="euclidean"):
    if pcenter is None:
        pcenter, interval = estimate_total_center(cycle)
    else:
        interval = None
    costs = cycle.cost_user_totals
    raw = np.zeros(cycle.n_users) if costs.sum() <= 0 else pcenter * costs / costs.sum()
    q = reconcile_total(raw, pcenter, reconciliation)
    return AttributionResult(
        "global_proportional",
        q,
        metadata={"estimated_total": pcenter, "total_interval": interval, "reconciliation": reconciliation},
    )


def adjacent_proportional(cycle, pcenter=None, reconciliation="euclidean"):
    if pcenter is None:
        pcenter, interval = estimate_total_center(cycle)
    else:
        interval = None
    agg = aggregate_cycle(cycle)
    increments = np.diff(np.r_[0.0, agg.observed_z.astype(float)])
    raw = np.zeros(cycle.n_users)
    for k, delta in enumerate(increments):
        costs = agg.costs_by_interval_user[k]
        if costs.sum() > 0 and delta != 0:
            raw += delta * costs / costs.sum()
    if raw.sum() <= 0:
        raw = cycle.cost_user_totals.copy()
    q = reconcile_total(raw, pcenter, reconciliation)
    return AttributionResult(
        "adjacent_integer",
        q,
        metadata={"estimated_total": pcenter, "total_interval": interval, "reconciliation": reconciliation},
    )


def _proxy_event_progress(cycle, pcenter):
    if len(cycle.event_times) == 0:
        return np.array([], float)
    z = cycle.observed_z.astype(float)
    t = cycle.sample_times.astype(float)
    z_final = float(z[-1])
    if z_final > 0:
        sample_proxy = np.maximum.accumulate(z * (pcenter / z_final))
        event_proxy = np.interp(cycle.event_times, t, sample_proxy)
    else:
        cumulative_cost = np.cumsum(cycle.event_costs)
        event_proxy = pcenter * cumulative_cost / max(cumulative_cost[-1], 1e-12)
    # Midpoint correction avoids placing all events exactly on a flat integer edge.
    order_cost = np.cumsum(cycle.event_costs)
    cost_proxy = pcenter * (order_cost - 0.5 * cycle.event_costs) / max(order_cost[-1], 1e-12)
    return np.clip(0.65 * event_proxy + 0.35 * cost_proxy, 0.0, pcenter)


def _oracle_event_progress(cycle):
    return np.cumsum(cycle.event_true_q) - 0.5 * cycle.event_true_q


def _event_coordinate(cycle, pcenter, coordinate="proxy_progress"):
    """Construct deployable or oracle event coordinates for window placement.

    All returned coordinates are mapped to the same [0, pcenter] scale so that
    a width expressed in percentage points remains comparable across schemes.
    """
    coordinate = str(coordinate)
    if coordinate in ("proxy", "proxy_progress", "continuous_proxy"):
        return _proxy_event_progress(cycle, pcenter)
    if coordinate in ("oracle", "oracle_progress", "true_progress"):
        return _oracle_event_progress(cycle)
    if len(cycle.event_times) == 0:
        return np.array([], float)
    if coordinate in ("display", "display_interp"):
        z = np.asarray(cycle.observed_z, float)
        t = np.asarray(cycle.sample_times, float)
        if len(z) and z[-1] > 0:
            return np.clip(np.interp(cycle.event_times, t, np.maximum.accumulate(z)) * pcenter / z[-1], 0.0, pcenter)
        coordinate = "cost_progress"
    if coordinate in ("cost", "cost_progress"):
        cumulative = np.cumsum(cycle.event_costs) - 0.5 * cycle.event_costs
        return np.clip(pcenter * cumulative / max(float(cycle.event_costs.sum()), 1e-12), 0.0, pcenter)
    if coordinate in ("time", "time_progress"):
        t0 = float(cycle.sample_times[0]) if len(cycle.sample_times) else 0.0
        t1 = float(cycle.sample_times[-1]) if len(cycle.sample_times) else float(cycle.event_times[-1])
        span = max(t1 - t0, 1e-12)
        return np.clip(pcenter * (cycle.event_times - t0) / span, 0.0, pcenter)
    raise ValueError(f"unknown event coordinate: {coordinate}")


def phase_offsets(width, n_phases, scheme="uniform"):
    width = float(width)
    m = int(max(1, n_phases))
    if m == 1:
        return np.array([0.0])
    if scheme == "uniform":
        offsets = np.linspace(0.0, width, m, endpoint=False)
    elif scheme == "halfshift":
        offsets = ((np.arange(m) + 0.5) / m * width) % width
    elif scheme == "golden":
        phi = (np.sqrt(5.0) - 1.0) / 2.0
        offsets = np.mod(np.arange(m) * phi, 1.0) * width
    elif scheme == "integer":
        grid = np.arange(max(1, int(np.ceil(width))), dtype=float)
        idx = np.floor(np.linspace(0, len(grid), m, endpoint=False)).astype(int)
        offsets = grid[np.clip(idx, 0, len(grid) - 1)] % width
    else:
        raise ValueError(f"unknown phase scheme: {scheme}")
    return np.unique(np.round(offsets, 12))


def _phase_estimate(cycle, width, phase, pcenter, event_progress):
    """Allocate progress-bin widths by participant cost inside each shifted bin."""
    if pcenter <= 0 or len(event_progress) == 0:
        return np.zeros(cycle.n_users), np.zeros(len(event_progress))
    width = float(max(width, 1e-9))
    m0 = int(np.floor(-phase / width)) - 1
    m1 = int(np.ceil((pcenter - phase) / width)) + 1
    interior = phase + width * np.arange(m0, m1 + 1)
    boundaries = np.unique(np.r_[0.0, interior[(interior > 0) & (interior < pcenter)], pcenter])
    bin_index = np.clip(np.searchsorted(boundaries, event_progress, side="right") - 1, 0, len(boundaries) - 2)
    raw = np.zeros(cycle.n_users)
    right_edges = boundaries[bin_index + 1]
    for b, (left, right) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True)):
        mask = bin_index == b
        if not np.any(mask):
            continue
        costs = np.bincount(
            cycle.event_users[mask],
            weights=cycle.event_costs[mask],
            minlength=cycle.n_users,
        ).astype(float)
        if costs.sum() > 0:
            raw += float(right - left) * costs / costs.sum()
    return raw, right_edges


def _huber_location(values, weights=None, c=1.345, iterations=30):
    x = np.asarray(values, float)
    if x.ndim == 1:
        x = x[:, None]
    m = x.shape[0]
    base_w = np.ones(m) if weights is None else np.asarray(weights, float)
    base_w = np.maximum(base_w, 1e-12)
    loc = np.average(x, axis=0, weights=base_w)
    for _ in range(iterations):
        residual = x - loc
        scale = 1.4826 * np.median(np.abs(residual), axis=0) + 1e-9
        ratio = np.abs(residual) / (c * scale)
        robust = np.where(ratio <= 1.0, 1.0, 1.0 / np.maximum(ratio, 1e-12))
        w = robust * base_w[:, None]
        new = np.sum(w * x, axis=0) / np.maximum(np.sum(w, axis=0), 1e-12)
        if np.max(np.abs(new - loc)) < 1e-10:
            loc = new
            break
        loc = new
    return loc


def _phase_reliability(estimates):
    estimates = np.asarray(estimates, float)
    center = np.median(estimates, axis=0)
    distance = np.sqrt(np.mean((estimates - center) ** 2, axis=1))
    scale = np.median(distance) + 1e-9
    weight = 1.0 / (1.0 + distance / scale)
    return weight / weight.sum()


def _aggregate_phase_vectors(estimates, aggregation="median"):
    estimates = np.asarray(estimates, float)
    m = len(estimates)
    if m == 1:
        return estimates[0].copy()
    if aggregation == "mean":
        return estimates.mean(axis=0)
    if aggregation == "median":
        return np.median(estimates, axis=0)
    if aggregation in ("trimmed", "trimmed_mean"):
        trim = max(1, int(np.floor(0.2 * m))) if m >= 5 else (1 if m > 2 else 0)
        sorted_values = np.sort(estimates, axis=0)
        return sorted_values[trim : m - trim].mean(axis=0) if trim else sorted_values.mean(axis=0)
    if aggregation == "huber":
        return _huber_location(estimates)
    weights = _phase_reliability(estimates)
    if aggregation == "weighted":
        return np.average(estimates, axis=0, weights=weights)
    if aggregation == "weighted_huber":
        return _huber_location(estimates, weights=weights)
    raise ValueError(f"unknown aggregation: {aggregation}")


def _sample_proxy_progress(cycle, pcenter):
    z = cycle.observed_z.astype(float)
    if z[-1] > 0:
        return np.maximum.accumulate(z * pcenter / z[-1])
    cumulative = np.r_[0.0, np.cumsum(cycle.event_costs)]
    event_grid = np.r_[0.0, cycle.event_times]
    return pcenter * np.interp(cycle.sample_times, event_grid, cumulative) / max(cumulative[-1], 1e-12)


def _settlement_times_from_right_edges(cycle, right_edges, pcenter):
    """First saved sample whose proxy progress reaches each required right edge."""
    proxy = _sample_proxy_progress(cycle, pcenter)
    idx = np.searchsorted(proxy, np.asarray(right_edges, float), side="left")
    idx = np.clip(idx, 0, len(cycle.sample_times) - 1)
    return cycle.sample_times[idx]


def phase_ensemble(
    cycle,
    width,
    n_phases=5,
    pcenter=None,
    aggregation="median",
    phase_scheme="uniform",
    oracle_boundaries=False,
    reconciliation="euclidean",
    coordinate="proxy_progress",
):
    if pcenter is None:
        pcenter, interval = estimate_total_center(cycle)
    else:
        interval = None
    offsets = phase_offsets(width, n_phases, phase_scheme)
    effective_coordinate = "oracle_progress" if oracle_boundaries else coordinate
    event_progress = _event_coordinate(cycle, pcenter, effective_coordinate)
    vectors, right_edges = [], []
    for phase in offsets:
        raw, edges = _phase_estimate(cycle, float(width), float(phase), pcenter, event_progress)
        vectors.append(reconcile_total(raw, pcenter, reconciliation))
        right_edges.append(edges)
    estimates = np.vstack(vectors)
    raw_aggregate = _aggregate_phase_vectors(estimates, aggregation)
    q = reconcile_total(raw_aggregate, pcenter, reconciliation)
    q25, q75 = np.quantile(estimates, [0.25, 0.75], axis=0)
    if len(cycle.event_times):
        required_edge = np.max(np.vstack(right_edges), axis=0)
        settlement = _settlement_times_from_right_edges(cycle, required_edge, pcenter)
        wait = np.maximum(0.0, settlement - cycle.event_times)
        weights = cycle.event_costs / max(cycle.event_costs.sum(), 1e-12)
        wait_mean = float(np.sum(weights * wait))
        wait_p95 = float(np.quantile(wait, 0.95))
    else:
        wait_mean = wait_p95 = 0.0
    metadata = {
        "estimated_total": pcenter,
        "total_interval": interval,
        "width": float(width),
        "n_phases": int(len(offsets)),
        "requested_n_phases": int(n_phases),
        "phase_scheme": phase_scheme,
        "aggregation": aggregation,
        "phase_dispersion_iqr_max": float(np.max(q75 - q25)),
        "phase_dispersion_iqr_mean": float(np.mean(q75 - q25)),
        "settlement_wait_mean_minutes": wait_mean,
        "settlement_wait_p95_minutes": wait_p95,
        "oracle_boundaries": bool(oracle_boundaries),
        "coordinate": effective_coordinate,
        "reconciliation": reconciliation,
    }
    return q, estimates, metadata


def fixed_window(cycle, width, pcenter=None, reconciliation="euclidean", name=None):
    q, _, meta = phase_ensemble(
        cycle, width, 1, pcenter, "mean", "uniform", False, reconciliation
    )
    return AttributionResult(name or f"window_w{width:g}", q, metadata=meta)


def multiphase_window(
    cycle,
    width=5.0,
    n_phases=5,
    pcenter=None,
    aggregation="median",
    phase_scheme="uniform",
    oracle_boundaries=False,
    reconciliation="euclidean",
    name=None,
    coordinate="proxy_progress",
):
    q, _, meta = phase_ensemble(
        cycle,
        width,
        n_phases,
        pcenter,
        aggregation,
        phase_scheme,
        oracle_boundaries,
        reconciliation,
        coordinate,
    )
    prefix = "oracle_multiphase" if oracle_boundaries else "multiphase"
    return AttributionResult(name or f"{prefix}_w{width:g}_m{n_phases}_{aggregation}", q, metadata=meta)


def adaptive_multiphase(
    cycle,
    candidate_widths=tuple(range(1, 21)),
    threshold=0.25,
    n_phases=5,
    pcenter=None,
    aggregation="median",
    phase_scheme="uniform",
    reconciliation="euclidean",
    name=None,
):
    if pcenter is None:
        pcenter, interval = estimate_total_center(cycle)
    else:
        interval = None
    trace = []
    selected = None
    for width in candidate_widths:
        q, _, meta = phase_ensemble(
            cycle,
            float(width),
            n_phases,
            pcenter,
            aggregation,
            phase_scheme,
            False,
            reconciliation,
        )
        trace.append((float(width), float(meta["phase_dispersion_iqr_max"])))
        selected = (q, meta)
        if meta["phase_dispersion_iqr_max"] <= threshold:
            break
    assert selected is not None
    q, meta = selected
    meta = {
        **meta,
        "total_interval": interval,
        "selected_width": float(meta["width"]),
        "selected_dispersion": float(meta["phase_dispersion_iqr_max"]),
        "threshold": float(threshold),
        "trace": trace,
    }
    return AttributionResult(name or f"adaptive_t{threshold:g}", q, metadata=meta)


def moving_local_window(
    cycle,
    width,
    orientation="centered",
    pcenter=None,
    reconciliation="euclidean",
    name=None,
):
    """Event-local slope estimator in proxy-progress coordinates."""
    if pcenter is None:
        pcenter, interval = estimate_total_center(cycle)
    else:
        interval = None
    ep = _proxy_event_progress(cycle, pcenter)
    raw_event = np.zeros(len(ep))
    right_edges = np.zeros(len(ep))
    for j, center in enumerate(ep):
        if orientation == "backward":
            left, right = center - width, center
        elif orientation == "centered":
            left, right = center - 0.5 * width, center + 0.5 * width
        elif orientation == "forward":
            left, right = center, center + width
        else:
            raise ValueError(orientation)
        left, right = max(0.0, left), min(pcenter, right)
        mask = (ep >= left - 1e-12) & (ep <= right + 1e-12)
        local_cost = float(cycle.event_costs[mask].sum())
        local_span = max(right - left, 1e-9)
        local_x = local_span / max(local_cost, 1e-12)
        raw_event[j] = cycle.event_costs[j] * local_x
        right_edges[j] = right
    raw = np.bincount(cycle.event_users, weights=raw_event, minlength=cycle.n_users).astype(float)
    q = reconcile_total(raw, pcenter, reconciliation)
    settlement = _settlement_times_from_right_edges(cycle, right_edges, pcenter) if len(ep) else np.array([])
    wait = np.maximum(0.0, settlement - cycle.event_times) if len(ep) else np.array([0.0])
    weights = cycle.event_costs / max(cycle.event_costs.sum(), 1e-12) if len(ep) else np.array([1.0])
    meta = {
        "estimated_total": pcenter,
        "total_interval": interval,
        "width": float(width),
        "orientation": orientation,
        "settlement_wait_mean_minutes": float(np.sum(weights * wait)),
        "settlement_wait_p95_minutes": float(np.quantile(wait, 0.95)),
        "reconciliation": reconciliation,
    }
    return AttributionResult(name or f"moving_{orientation}_w{width:g}", q, metadata=meta)


def tv_attribution(cycle, known_theta=None, obs_slack=0.0, prior=None, prior_weight=0.0, name="tv"):
    result = tv_rate_estimate(cycle, known_theta, obs_slack, prior, prior_weight)
    if not result.success:
        return AttributionResult(name, np.full(cycle.n_users, np.nan), False, result.message, metadata=result.metadata)
    return AttributionResult(name, result.estimate, metadata={"theta": result.theta, **(result.metadata or {})})


def set_attribution(cycle, selector="midpoint_lex", anchor=None, known_theta=None, obs_slack=0.0, name=None):
    result = identification_region(
        cycle,
        known_theta=known_theta,
        obs_slack=obs_slack,
        selector=selector,
        anchor=anchor,
    )
    if not result.success:
        return AttributionResult(name or f"set_{selector}", np.full(cycle.n_users, np.nan), False, result.message, metadata=result.metadata)
    return AttributionResult(
        name or f"set_{selector}",
        result.estimate,
        True,
        result.message,
        result.lower,
        result.upper,
        {
            "radius": result.radius,
            "theta": result.theta,
            "total_interval": result.total_interval,
            **(result.metadata or {}),
        },
    )


def set_box_midpoint_from_result(set_result: AttributionResult, name="set_box_midpoint"):
    if not set_result.success or set_result.lower is None or set_result.upper is None:
        return AttributionResult(name, np.full_like(set_result.estimate, np.nan), False, "missing set")
    interval = (set_result.metadata or {}).get("total_interval")
    total = 0.5 * sum(interval) if interval is not None else float(np.sum(set_result.estimate))
    q = _project_box_sum(0.5 * (set_result.lower + set_result.upper), total, set_result.lower, set_result.upper)
    return AttributionResult(
        name,
        q,
        True,
        "box midpoint",
        set_result.lower,
        set_result.upper,
        {
            **(set_result.metadata or {}),
            "center_temporally_certified": False,
            "selection_rule": "bounded_simplex_projection_of_coordinate_midpoint",
        },
    )


def oracle_attribution(cycle):
    return AttributionResult(
        "oracle_rate",
        cycle.true_user_totals.copy(),
        metadata={"estimated_total": cycle.true_total},
    )
