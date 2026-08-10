from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np

from .models import AlgorithmOutput, Observations

X_MIN = 1.0 / 21.0
X_MAX = 1.0 / 14.0
X_PRIOR = 1.0 / 17.5
V_PRIOR = 1750.0


def _clip_x(x: float | np.ndarray) -> float | np.ndarray:
    return np.clip(x, X_MIN, X_MAX)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    total = w.sum()
    if total <= 0:
        return float(np.median(v))
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, 0.5 * total, side="left")])


def _window_estimate(obs: Observations, j: int, k: int, shrink_pp: float = 1.5) -> tuple[float, float, float, float]:
    if j >= k:
        return X_PRIOR, 0.0, 0.0, 0.0
    dc = float((obs.c_obs[k] - obs.c_obs[j]).sum())
    dz = float(obs.z[k] - obs.z[j])
    if dc <= 1e-9 or dz <= 0.0:
        return X_PRIOR, 0.0, dc, dz
    raw = float(_clip_x(dz / dc))
    # Integer endpoint uncertainty is <1 pp for a fixed quantizer. Shrink short
    # windows toward the minimax midpoint instead of treating tiny dz as exact.
    weight = dz * dz / (dz * dz + shrink_pp * shrink_pp)
    x = float(_clip_x(weight * raw + (1.0 - weight) * X_PRIOR))
    reliability = weight * min(1.0, dz / 3.0)
    return x, reliability, dc, dz


def _limits_from_x_history(
    obs: Observations,
    rights: np.ndarray,
    x_hat: np.ndarray,
    algorithm: str,
    diagnostics: dict | None = None,
) -> AlgorithmOutput:
    k_count, n = obs.c_obs.shape
    q_hat = np.zeros((k_count, n))
    dc = np.diff(obs.c_obs, axis=0)
    dc = np.maximum(dc, 0.0)
    for k in range(1, k_count):
        q_hat[k] = q_hat[k - 1] + dc[k - 1] * x_hat[k]
    h_pp = 100.0 * rights
    remaining = np.maximum(h_pp[None, :] - q_hat, 0.0)
    v_hat = 100.0 / np.asarray(_clip_x(x_hat))
    b_hat = remaining * v_hat[:, None] / 100.0
    l_hat = obs.c_obs + b_hat
    return AlgorithmOutput(
        algorithm=algorithm,
        times=obs.times,
        b_hat=b_hat,
        l_hat=l_hat,
        q_hat=q_hat,
        v_hat=v_hat,
        diagnostics=diagnostics or {},
    )


def static_allocation(obs: Observations, rights: np.ndarray, v_ref: float = V_PRIOR) -> AlgorithmOutput:
    cumulative_cap = rights * v_ref
    b_hat = np.maximum(cumulative_cap[None, :] - obs.c_obs, 0.0)
    l_hat = obs.c_obs + b_hat
    x = np.full(len(obs.times), 100.0 / v_ref)
    q_hat = obs.c_obs * x[:, None]
    return AlgorithmOutput(
        algorithm="static_1750",
        times=obs.times,
        b_hat=b_hat,
        l_hat=l_hat,
        q_hat=q_hat,
        v_hat=np.full(len(obs.times), v_ref),
    )


def amount_proportion(obs: Observations, rights: np.ndarray, shrink_pp: float = 3.0) -> AlgorithmOutput:
    k_count, n = obs.c_obs.shape
    q_hat = np.zeros((k_count, n))
    x_hat = np.full(k_count, X_PRIOR)
    for k in range(1, k_count):
        total_c = float(obs.c_obs[k].sum())
        p = float(obs.z[k])
        if total_c > 0 and p > 0:
            raw_x = float(_clip_x(p / total_c))
            w = p * p / (p * p + shrink_pp * shrink_pp)
            x_hat[k] = float(_clip_x(w * raw_x + (1.0 - w) * X_PRIOR))
            shares = obs.c_obs[k] / total_c
            q_hat[k] = p * shares
        else:
            x_hat[k] = X_PRIOR
    h_pp = 100.0 * rights
    remaining = np.maximum(h_pp[None, :] - q_hat, 0.0)
    v_hat = 100.0 / x_hat
    b_hat = remaining * v_hat[:, None] / 100.0
    l_hat = obs.c_obs + b_hat
    return AlgorithmOutput(
        algorithm="amount_proportion",
        times=obs.times,
        b_hat=b_hat,
        l_hat=l_hat,
        q_hat=q_hat,
        v_hat=v_hat,
    )


def _trailing_start_for_progress(obs: Observations, k: int, width_pp: float) -> int:
    target = float(obs.z[k]) - width_pp
    if target <= 0:
        return 0
    candidates = np.flatnonzero(obs.z[:k] <= target)
    if len(candidates) == 0:
        return 0
    return int(candidates[-1])


def single_window(
    obs: Observations,
    rights: np.ndarray,
    width_pp: float = 6.0,
    shrink_pp: float = 1.5,
) -> AlgorithmOutput:
    x_hat = np.full(len(obs.times), X_PRIOR)
    starts = np.zeros(len(obs.times), dtype=int)
    reliability = np.zeros(len(obs.times))
    for k in range(1, len(obs.times)):
        j = _trailing_start_for_progress(obs, k, width_pp)
        starts[k] = j
        x_hat[k], reliability[k], _, _ = _window_estimate(obs, j, k, shrink_pp)
    return _limits_from_x_history(
        obs,
        rights,
        x_hat,
        algorithm=f"single_window_{width_pp:g}pp",
        diagnostics={"window_start": starts, "reliability": reliability},
    )


def multiscale_window(
    obs: Observations,
    rights: np.ndarray,
    widths_pp: Iterable[float] = (3.0, 6.0, 12.0),
    shrink_pp: float = 1.5,
) -> AlgorithmOutput:
    widths = tuple(float(w) for w in widths_pp)
    x_hat = np.full(len(obs.times), X_PRIOR)
    count = np.zeros(len(obs.times), dtype=int)
    spread = np.zeros(len(obs.times))
    for k in range(1, len(obs.times)):
        vals, weights = [], []
        for width in widths:
            j = _trailing_start_for_progress(obs, k, width)
            x, rel, _, dz = _window_estimate(obs, j, k, shrink_pp)
            if dz > 0:
                vals.append(x)
                # Favor informative but local estimates; the sqrt limits the
                # dominance of long windows.
                weights.append(max(rel, 0.05) / math.sqrt(max(width, 1.0)))
        if vals:
            arr = np.asarray(vals)
            w = np.asarray(weights)
            x_hat[k] = _weighted_median(arr, w)
            count[k] = len(arr)
            spread[k] = float((arr.max() - arr.min()) / max(np.median(arr), 1e-12))
        else:
            x_hat[k] = x_hat[k - 1]
    return _limits_from_x_history(
        obs,
        rights,
        x_hat,
        algorithm="multiscale_window",
        diagnostics={"estimate_count": count, "relative_spread": spread},
    )


def _phase_start(obs: Observations, k: int, width: int, phase: int) -> int:
    """Observable progress-lattice boundary nearest the nominal trailing start."""
    z_cur = int(obs.z[k])
    target = z_cur - width
    if target <= 0:
        return 0
    m = int(round((target - phase) / width))
    boundary = phase + m * width
    while boundary >= z_cur:
        boundary -= width
    while boundary < 0:
        boundary += width
        if boundary >= z_cur:
            return 0
    candidates = np.flatnonzero(obs.z[:k] <= boundary)
    if len(candidates) == 0:
        return 0
    return int(candidates[-1])


def _phase_estimates(
    obs: Observations,
    k: int,
    width_pp: int,
    shrink_pp: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if width_pp < 3:
        phases = (0,)
    else:
        phases = tuple(sorted(set((0, width_pp // 3, (2 * width_pp) // 3))))
    vals, weights, starts = [], [], []
    for phase in phases:
        j = _phase_start(obs, k, width_pp, phase)
        x, rel, dc, dz = _window_estimate(obs, j, k, shrink_pp)
        if dz > 0 and dc > 0:
            vals.append(x)
            weights.append(max(rel, 0.05))
            starts.append(j)
    return np.asarray(vals), np.asarray(weights), np.asarray(starts, dtype=int)


def multiphase_window(
    obs: Observations,
    rights: np.ndarray,
    width_pp: int = 6,
    shrink_pp: float = 1.5,
) -> AlgorithmOutput:
    x_hat = np.full(len(obs.times), X_PRIOR)
    spread = np.full(len(obs.times), np.nan)
    count = np.zeros(len(obs.times), dtype=int)
    start_min = np.zeros(len(obs.times), dtype=int)
    start_max = np.zeros(len(obs.times), dtype=int)
    for k in range(1, len(obs.times)):
        vals, weights, starts = _phase_estimates(obs, k, int(width_pp), shrink_pp)
        if len(vals):
            x_hat[k] = _weighted_median(vals, weights)
            spread[k] = float((vals.max() - vals.min()) / max(np.median(vals), 1e-12))
            count[k] = len(vals)
            start_min[k], start_max[k] = int(starts.min()), int(starts.max())
        else:
            x_hat[k] = x_hat[k - 1]
    return _limits_from_x_history(
        obs,
        rights,
        x_hat,
        algorithm=f"multiphase_{width_pp}pp",
        diagnostics={
            "relative_phase_spread": spread,
            "phase_count": count,
            "start_min": start_min,
            "start_max": start_max,
        },
    )


def adaptive_phase_extension(
    obs: Observations,
    rights: np.ndarray,
    widths_pp: Iterable[int] = (3, 6, 9, 12, 18),
    spread_threshold: float = 0.08,
    shrink_pp: float = 1.5,
) -> AlgorithmOutput:
    widths = tuple(int(w) for w in widths_pp)
    x_hat = np.full(len(obs.times), X_PRIOR)
    chosen = np.zeros(len(obs.times), dtype=int)
    spread_arr = np.full(len(obs.times), np.nan)
    phase_count = np.zeros(len(obs.times), dtype=int)
    for k in range(1, len(obs.times)):
        last_candidate = None
        for width in widths:
            vals, weights, _ = _phase_estimates(obs, k, width, shrink_pp)
            if len(vals) == 0:
                continue
            x = _weighted_median(vals, weights)
            spread = float((vals.max() - vals.min()) / max(np.median(vals), 1e-12))
            last_candidate = (width, x, spread, len(vals))
            if len(vals) >= 2 and spread <= spread_threshold:
                break
        if last_candidate is None:
            x_hat[k] = x_hat[k - 1]
            continue
        width, x, spread, cnt = last_candidate
        x_hat[k] = x
        chosen[k] = width
        spread_arr[k] = spread
        phase_count[k] = cnt
    return _limits_from_x_history(
        obs,
        rights,
        x_hat,
        algorithm="adaptive_phase_extension",
        diagnostics={
            "chosen_width_pp": chosen,
            "relative_phase_spread": spread_arr,
            "phase_count": phase_count,
            "spread_threshold": spread_threshold,
        },
    )
