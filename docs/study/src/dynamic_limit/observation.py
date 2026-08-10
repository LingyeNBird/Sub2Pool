from __future__ import annotations

import numpy as np

from .models import Observations, SimulationTruth


def apply_quantizer(p: np.ndarray, rule: str) -> np.ndarray:
    if rule == "floor":
        z = np.floor(p)
    elif rule == "nearest":
        z = np.floor(p + 0.5)
    elif rule == "ceil":
        z = np.ceil(p)
    else:
        raise ValueError(f"Unknown quantizer: {rule}")
    return np.clip(z, 0, 100).astype(int)


def make_observations(truth: SimulationTruth, round_amounts: bool = True) -> Observations:
    dt = truth.spec.dt_hours
    stride = int(round(truth.spec.sample_hours / dt))
    if stride <= 0:
        raise ValueError("Sampling interval must be positive")
    idx = np.arange(0, len(truth.t), stride, dtype=int)
    if idx[-1] != len(truth.t) - 1:
        idx = np.append(idx, len(truth.t) - 1)
    c_obs = np.round(truth.c[idx], 2) if round_amounts else truth.c[idx].copy()
    z = apply_quantizer(truth.p[idx], truth.spec.quantizer)
    return Observations(
        spec=truth.spec,
        sample_idx=idx,
        times=truth.t[idx],
        c_obs=c_obs,
        z=z,
        quantizer=truth.spec.quantizer,
    )


def true_limits(truth: SimulationTruth) -> tuple[np.ndarray, np.ndarray]:
    h_pp = 100.0 * truth.rights
    remaining = np.maximum(h_pp[None, :] - truth.q, 0.0)
    b = remaining * truth.v[:, None] / 100.0
    l = truth.c + b
    return b, l


def display_cell(z: int, rule: str) -> tuple[float, float]:
    """Closed conservative cell for one displayed integer percentage."""
    if rule == "floor":
        return max(0.0, float(z)), min(100.0, float(z + 1))
    if rule == "nearest":
        return max(0.0, float(z) - 0.5), min(100.0, float(z) + 0.5)
    if rule == "ceil":
        return max(0.0, float(z) - 1.0), min(100.0, float(z))
    raise ValueError(rule)


def unknown_quantizer_cell(z: int) -> tuple[float, float]:
    return max(0.0, float(z) - 1.0), min(100.0, float(z) + 1.0)
