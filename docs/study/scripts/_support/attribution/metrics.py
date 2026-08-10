"""Evaluation metrics and uncertainty helpers."""
from __future__ import annotations
import numpy as np
from algorithms import AttributionResult
from models import CycleData


def evaluate(cycle: CycleData, result: AttributionResult):
    truth = cycle.true_user_totals
    if not result.success or np.any(~np.isfinite(result.estimate)):
        return {
            "success": False,
            "algorithm": result.name,
            "message": result.message,
        }, []
    estimate = np.asarray(result.estimate, float)
    error = estimate - truth
    over = np.maximum(error, 0.0)
    under = np.maximum(-error, 0.0)
    meta = result.metadata or {}
    row = {
        "success": True,
        "algorithm": result.name,
        "message": result.message,
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "max_abs": float(np.max(np.abs(error))),
        "max_over": float(np.max(over)),
        "max_under": float(np.max(under)),
        "mean_bias": float(np.mean(error)),
        "total_error": float(np.sum(estimate) - cycle.true_total),
        "estimated_total": float(np.sum(estimate)),
        "relative_mae_total": float(np.mean(np.abs(error)) / max(cycle.true_total, 1e-12)),
        "phase_dispersion": float(meta.get("phase_dispersion_iqr_max", np.nan)),
        "selected_width": float(meta.get("selected_width", meta.get("width", np.nan))),
        "settlement_wait_mean_minutes": float(meta.get("settlement_wait_mean_minutes", np.nan)),
        "settlement_wait_p95_minutes": float(meta.get("settlement_wait_p95_minutes", np.nan)),
        "set_radius": float(meta.get("radius", np.nan)),
        "set_unrestricted_radius": float(meta.get("minimax_unrestricted_radius", np.nan)),
        "center_certified": meta.get("center_temporally_certified", None),
        "n_intervals": float(meta.get("n_intervals", np.nan)),
        "n_time_groups": float(meta.get("n_time_groups", np.nan)),
        "simultaneous_group_count": float(meta.get("simultaneous_group_count", np.nan)),
        "tv_face_retry": bool(meta.get("tv_face_retry", False)),
    }
    if result.lower is not None and result.upper is not None:
        lower, upper = np.asarray(result.lower), np.asarray(result.upper)
        covered = (truth >= lower - 1e-7) & (truth <= upper + 1e-7)
        row.update({
            "interval_coverage_all": bool(np.all(covered)),
            "interval_coverage_fraction": float(np.mean(covered)),
            "interval_width_mean": float(np.mean(upper - lower)),
            "interval_width_max": float(np.max(upper - lower)),
        })
    else:
        row.update({
            "interval_coverage_all": np.nan,
            "interval_coverage_fraction": np.nan,
            "interval_width_mean": np.nan,
            "interval_width_max": np.nan,
        })
    users = []
    for i in range(cycle.n_users):
        users.append({
            "user": int(i),
            "true_q": float(truth[i]),
            "estimate_q": float(estimate[i]),
            "error": float(error[i]),
            "abs_error": float(abs(error[i])),
            "over": float(over[i]),
            "under": float(under[i]),
            "lower": float(result.lower[i]) if result.lower is not None else np.nan,
            "upper": float(result.upper[i]) if result.upper is not None else np.nan,
        })
    return row, users


def cvar(values, alpha=0.95):
    x = np.sort(np.asarray(values, float))
    if len(x) == 0:
        return np.nan
    k = min(len(x) - 1, max(0, int(np.ceil(alpha * len(x))) - 1))
    return float(np.mean(x[k:]))


def order_stat_interval(values, p=0.95, confidence=0.95):
    """Distribution-free binomial order-statistic interval for a quantile."""
    from scipy.stats import binom
    x = np.sort(np.asarray(values, float))
    n = len(x)
    if n == 0:
        return np.nan, np.nan
    alpha = 1 - confidence
    lo_rank = int(binom.ppf(alpha / 2, n, p))
    hi_rank = int(binom.ppf(1 - alpha / 2, n, p))
    lo_rank = int(np.clip(lo_rank, 0, n - 1))
    hi_rank = int(np.clip(hi_rank, 0, n - 1))
    return float(x[lo_rank]), float(x[hi_rank])
