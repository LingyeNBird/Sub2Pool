from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from .models import AlgorithmOutput, Observations
from .observation import display_cell, unknown_quantizer_cell



@dataclass
class IncrementBounds:
    dc_low: np.ndarray
    dc_high: np.ndarray
    dq_low: np.ndarray
    dq_high: np.ndarray


def amount_increment_bounds(
    c_obs: np.ndarray,
    *,
    capacity_min_usd: float = 1400.0,
    capacity_max_usd: float = 2100.0,
) -> IncrementBounds:
    dc = np.diff(c_obs, axis=0)
    dc_low = np.maximum(dc - 0.01, 0.0)
    dc_high = np.maximum(dc + 0.01, 0.0)
    dq_low = dc_low / (capacity_max_usd / 100.0)
    dq_high = dc_high / (capacity_min_usd / 100.0)
    return IncrementBounds(dc_low=dc_low, dc_high=dc_high, dq_low=dq_low, dq_high=dq_high)


def recursive_outer_set(
    obs: Observations,
    rights: np.ndarray,
    *,
    capacity_min_usd: float = 1400.0,
    capacity_max_usd: float = 2100.0,
) -> AlgorithmOutput:
    """Causal guaranteed outer set under bounds + unknown fixed quantizer."""
    k_count, n = obs.c_obs.shape
    inc = amount_increment_bounds(
        obs.c_obs,
        capacity_min_usd=capacity_min_usd,
        capacity_max_usd=capacity_max_usd,
    )
    q_low = np.zeros((k_count, n))
    q_high = np.zeros((k_count, n))
    p_low = np.zeros(k_count)
    p_high = np.zeros(k_count)
    p_low[0] = p_high[0] = 0.0

    infeasible_repairs = 0
    for k in range(1, k_count):
        cell_l, cell_u = unknown_quantizer_cell(int(obs.z[k]))
        l = inc.dq_low[k - 1]
        u = inc.dq_high[k - 1]
        sum_l = float(l.sum())
        sum_u = float(u.sum())

        new_p_l = max(cell_l, p_low[k - 1] + sum_l)
        new_p_u = min(cell_u, p_high[k - 1] + sum_u)
        if new_p_l > new_p_u + 1e-10:
            # The union-cell recursion can become numerically inconsistent at
            # exact boundaries. Repair conservatively rather than dropping truth.
            infeasible_repairs += 1
            new_p_l = min(cell_l, p_low[k - 1] + sum_l)
            new_p_u = max(cell_u, p_high[k - 1] + sum_u)
        p_low[k], p_high[k] = new_p_l, new_p_u

        dp_l = max(sum_l, new_p_l - p_high[k - 1], 0.0)
        dp_u = min(sum_u, new_p_u - p_low[k - 1])
        if dp_l > dp_u:
            dp_l, dp_u = sum_l, sum_u

        for i in range(n):
            other_u = sum_u - u[i]
            other_l = sum_l - l[i]
            yi_l = max(l[i], dp_l - other_u, 0.0)
            yi_u = min(u[i], dp_u - other_l)
            if yi_l > yi_u:
                yi_l, yi_u = l[i], u[i]
            q_low[k, i] = q_low[k - 1, i] + yi_l
            q_high[k, i] = q_high[k - 1, i] + yi_u

    h_pp = 100.0 * rights
    remaining_low = np.maximum(h_pp[None, :] - q_high, 0.0)
    remaining_high = np.maximum(h_pp[None, :] - q_low, 0.0)
    b_lower = (capacity_min_usd / 100.0) * remaining_low
    b_upper = (capacity_max_usd / 100.0) * remaining_high
    b_hat = 0.5 * (b_lower + b_upper)
    l_lower = obs.c_obs + b_lower
    l_upper = obs.c_obs + b_upper
    l_hat = obs.c_obs + b_hat
    return AlgorithmOutput(
        algorithm="set_outer_midpoint",
        times=obs.times,
        b_hat=b_hat,
        l_hat=l_hat,
        b_lower=b_lower,
        b_upper=b_upper,
        l_lower=l_lower,
        l_upper=l_upper,
        q_hat=0.5 * (q_low + q_high),
        v_hat=np.full(k_count, 0.5 * (capacity_min_usd + capacity_max_usd)),
        diagnostics={
            "q_lower": q_low,
            "q_upper": q_high,
            "p_lower": p_low,
            "p_upper": p_high,
            "infeasible_repairs": infeasible_repairs,
        },
    )


def _branch_lp_bounds(
    obs: Observations,
    k: int,
    participant: int,
    rule: str,
    *,
    capacity_min_usd: float,
    capacity_max_usd: float,
) -> tuple[float, float] | None:
    """Exact LP bounds for cumulative Q_i at sample k under one quantizer."""
    n = obs.c_obs.shape[1]
    inc = amount_increment_bounds(
        obs.c_obs[: k + 1],
        capacity_min_usd=capacity_min_usd,
        capacity_max_usd=capacity_max_usd,
    )
    m = k * n
    if m == 0:
        return 0.0, 0.0
    bounds = []
    for j in range(k):
        for i in range(n):
            bounds.append((float(inc.dq_low[j, i]), float(inc.dq_high[j, i])))

    a_ub: list[np.ndarray] = []
    b_ub: list[float] = []
    for r in range(1, k + 1):
        row = np.zeros(m)
        row[: r * n] = 1.0
        lo, hi = display_cell(int(obs.z[r]), rule)
        a_ub.append(row)
        b_ub.append(hi)
        a_ub.append(-row)
        b_ub.append(-lo)

    obj = np.zeros(m)
    for j in range(k):
        obj[j * n + participant] = 1.0
    kwargs = dict(A_ub=np.asarray(a_ub), b_ub=np.asarray(b_ub), bounds=bounds, method="highs")
    lo_res = linprog(obj, **kwargs)
    hi_res = linprog(-obj, **kwargs)
    if not lo_res.success or not hi_res.success:
        return None
    return float(lo_res.fun), float(-hi_res.fun)


def exact_lp_identification(
    obs: Observations,
    rights: np.ndarray,
    sample_positions: list[int] | None = None,
    *,
    capacity_min_usd: float = 1400.0,
    capacity_max_usd: float = 2100.0,
) -> dict[str, np.ndarray]:
    """Exact finite-dimensional identification polytope over sample bins.

    This is used as a validation/diagnostic implementation because solving all
    participant-by-time LPs is more expensive than the recursive outer set.
    """
    k_count, n = obs.c_obs.shape
    if sample_positions is None:
        sample_positions = list(range(k_count))
    q_lo = np.full((len(sample_positions), n), np.nan)
    q_hi = np.full((len(sample_positions), n), np.nan)
    feasible_branches = np.zeros(len(sample_positions), dtype=int)
    for out_k, k in enumerate(sample_positions):
        branch_feasible = set()
        for i in range(n):
            vals = []
            for rule in ("floor", "nearest", "ceil"):
                result = _branch_lp_bounds(
                    obs,
                    k,
                    i,
                    rule,
                    capacity_min_usd=capacity_min_usd,
                    capacity_max_usd=capacity_max_usd,
                )
                if result is not None:
                    vals.append(result)
                    branch_feasible.add(rule)
            if vals:
                q_lo[out_k, i] = min(v[0] for v in vals)
                q_hi[out_k, i] = max(v[1] for v in vals)
        feasible_branches[out_k] = len(branch_feasible)

    h_pp = 100.0 * rights
    s_lo = np.maximum(h_pp[None, :] - q_hi, 0.0)
    s_hi = np.maximum(h_pp[None, :] - q_lo, 0.0)
    b_lo = (capacity_min_usd / 100.0) * s_lo
    b_hi = (capacity_max_usd / 100.0) * s_hi
    return {
        "sample_positions": np.asarray(sample_positions),
        "q_lower": q_lo,
        "q_upper": q_hi,
        "b_lower": b_lo,
        "b_upper": b_hi,
        "feasible_quantizer_branches": feasible_branches,
    }
