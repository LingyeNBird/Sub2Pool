"""Quantizer families used in synthetic experiments."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class QuantizerSpec:
    name: str = "fixed_offset"
    theta: float = 0.0
    irregularity: float = 0.0
    switch_fraction: float = 0.5
    theta_after: float | None = None


def fixed_offset_quantize(p, theta, cap=100):
    z = np.floor(np.asarray(p, float) + theta + 1e-12).astype(int)
    return np.clip(z, 0, cap)


def irregular_thresholds(rng, theta, irregularity, cap=100):
    gaps = rng.uniform(1-irregularity, 1+irregularity, cap)
    gaps /= gaps.mean()
    b = np.empty(cap)
    b[0] = max(1e-9, 1-theta)
    if cap > 1:
        b[1:] = b[0] + np.cumsum(gaps[1:])
    return b


def apply_quantizer(p, sample_times, spec: QuantizerSpec, rng, cap=100):
    if spec.name in ("fixed_offset", "floor", "nearest"):
        theta = spec.theta if spec.name == "fixed_offset" else (0.0 if spec.name == "floor" else 0.5)
        return fixed_offset_quantize(p, theta, cap), {"theta": float(theta)}
    if spec.name == "irregular":
        b = irregular_thresholds(rng, spec.theta, spec.irregularity, cap)
        z = np.searchsorted(b, np.asarray(p), side="right")
        return np.clip(z, 0, cap).astype(int), {"theta": float(spec.theta), "irregularity": float(spec.irregularity), "thresholds": b.tolist()}
    if spec.name == "switching_offset":
        theta2 = spec.theta_after if spec.theta_after is not None else float(rng.uniform())
        st = sample_times[0] + spec.switch_fraction*(sample_times[-1]-sample_times[0])
        z = np.empty(len(p), int)
        m = sample_times < st
        z[m] = fixed_offset_quantize(np.asarray(p)[m], spec.theta, cap)
        z[~m] = fixed_offset_quantize(np.asarray(p)[~m], theta2, cap)
        return z, {"theta": float(spec.theta), "theta_after": float(theta2), "switch_time": float(st)}
    raise ValueError(spec.name)
