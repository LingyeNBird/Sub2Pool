"""Joint-parameter evidence from raw costs and raw quota increments ONLY.

Per-account short-segment capacity is a nuisance scale profiled from the raw
observations, not an imported particle-filter or constant-average estimate.
No local fit/rank/sample-count gate and no local prior or winner selection.
"""
from dataclasses import dataclass
from functools import lru_cache
import numpy as np
from .pooled_protocol import candidates, QUALITY_KEYS


@dataclass(frozen=True)
class Interval:
    start: float
    end: float
    quota: float
    known: float
    target: tuple[float, float, float, float]


@lru_cache(maxsize=1)
def grid():
    points = np.array([p for _, p in candidates()], dtype=float)
    null = int(np.where(np.all(points == 1, axis=1))[0][0])
    return points, null


def groups(intervals):
    """Upper bounds for cost/working-drift accuracy, never admission minima."""
    result = []
    for row in intervals:
        if (not result or len(result[-1]) == 32
                or row.end - result[-1][0].start > 6
                or abs(row.start - result[-1][-1].end) > 1e-7):
            result.append([])
        result[-1].append(row)
    return result


def group_evidence(rows):
    points, null = grid()
    z = np.array([[r.known, *r.target] for r in rows], dtype=float)
    y = np.array([r.quota for r in rows], dtype=float)
    n = len(rows)
    if not n or not np.all(np.isfinite(z)) or not np.all(np.isfinite(y)) or np.any(z < 0) or np.any(y < 0) or np.any(z.sum(axis=1) <= 0):
        raise ValueError("invalid raw research group")
    for i, row in enumerate(rows):
        if not np.isfinite(row.start + row.end) or row.end <= row.start or (i and row.start < rows[i-1].end - 1e-7):
            raise ValueError("invalid raw research interval")
    vectors = np.column_stack([np.ones(len(points)), points])
    cost = z @ vectors.T
    quota = (y[:, None] * (z[:, 1:] @ points.T) / cost).sum(axis=0)
    info = np.zeros((4, 4))
    curves = np.zeros((3, len(points)))
    # A free capacity and a single observation identify no price contrast.
    if n == 1:
        return curves, quota, info
    rounding = np.eye(n) * (1 / 6 + .01)
    for i in range(n - 1):
        if abs(rows[i].end - rows[i+1].start) < 1e-7:
            rounding[i, i+1] = rounding[i+1, i] = -1 / 12
    centers = np.array([(r.start + r.end) / 2 for r in rows])
    elapsed = np.maximum(0, centers - centers[0])
    brownian = np.minimum.outer(elapsed, elapsed)
    quota_scale = np.maximum(y, .5)
    # Common currency scaling must not change the parameter evidence.
    scale = max(float(z.max()), 1e-100)
    z = z / scale
    for index, drift in enumerate((0., .15, .4)):
        covariance = rounding + drift**2 * np.outer(quota_scale, quota_scale) * brownian
        chol = np.linalg.cholesky(covariance)
        wz = np.linalg.solve(chol, z)
        wy = np.linalg.solve(chol, y)
        gram, h, q = wz.T @ wz, wz.T @ wy, float(wy @ wy)
        denominator = np.einsum("ij,jk,ik->i", vectors, gram, vectors)
        numerator = np.maximum(vectors @ h, 0)
        residual = np.maximum(0., q - numerator**2 / np.maximum(denominator, 1e-200))
        ll = -.5 * (4 + n - 1) / 2 * np.log1p(residual / 4)
        curves[index] = ll - ll[null]
        if index == 1:
            null_cost = wz.sum(axis=1)
            denom = float(null_cost @ null_cost)
            a = max(float(null_cost @ wy), 0) / max(denom, 1e-200)
            j = a * wz[:, 1:]
            projection = j - np.outer(null_cost, null_cost @ j) / max(denom, 1e-200)
            info = projection.T @ projection
    return curves, quota, info


def summarize(intervals, *, requests=0, gpt6_requests=0, raw_usd=0., gpt6_raw_usd=0., quality=None, gateway_only=False):
    parts = groups(intervals)
    points, null = grid()
    curves = np.zeros((3, len(points)))
    attribution = np.zeros(len(points))
    info = np.zeros((4, 4))
    for part in parts:
        ll, quota, information = group_evidence(part)
        curves += ll
        attribution += quota
        info += information
    labels = {k: int((quality or {}).get(k, 0)) for k in QUALITY_KEYS}
    if not gateway_only:
        labels["external_usage_uncontrolled"] = len(intervals)
    # A JSON-only allowlist; no fitted capacity, posterior, or account field.
    return {
        "requests": requests, "gpt6_requests": gpt6_requests,
        "other_requests": requests - gpt6_requests,
        "raw_usd": round(raw_usd, 8), "gpt6_raw_usd": round(gpt6_raw_usd, 8),
        "quota_points": round(sum(r.quota for r in intervals), 8),
        "intervals": len(intervals), "groups": len(parts),
        "contrasts": len(intervals) - len(parts), "gateway_only": bool(gateway_only),
        "quality": labels, "log_evidence": np.round(curves, 10).tolist(),
        "gpt6_quota": np.round(attribution, 8).tolist(),
        "information": np.round((info + info.T) / 2, 8).tolist(),
    }
