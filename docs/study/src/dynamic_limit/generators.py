from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .models import SimulationSpec, SimulationTruth


SPEED_PARAMS = {
    "slow": {"tau": 72.0, "latent_sd": 0.72, "sine_period": 120.0, "sine_amp": 0.18},
    "medium": {"tau": 24.0, "latent_sd": 0.76, "sine_period": 48.0, "sine_amp": 0.22},
    "fast": {"tau": 6.0, "latent_sd": 0.80, "sine_period": 14.0, "sine_amp": 0.25},
}

SCENARIOS = (
    "uniform",
    "front_loaded",
    "back_loaded",
    "single_burst",
    "multi_burst",
    "one_steady_others_burst",
    "staggered",
    "overlapping",
    "skewed_heavy",
    "v_high_corr",
    "v_low_corr",
    "extreme_silent_then_burst",
    "extreme_first_day_whale",
    "extreme_alternating_spikes",
    "extreme_simultaneous_spike",
    "extreme_sample_edge_bursts",
    "extreme_v_opposed",
    "extreme_rights_mismatch",
    "extreme_micro_macro",
)


def rights_vector(n: int, profile: str, rng: np.random.Generator) -> np.ndarray:
    if n < 2:
        raise ValueError("At least two participants are required")
    if profile == "balanced":
        h = np.ones(n, dtype=float)
    elif profile == "moderate_skew":
        if n == 2:
            h = np.array([0.60, 0.40])
        elif n == 4:
            h = np.array([0.40, 0.30, 0.20, 0.10])
        elif n == 6:
            h = np.array([0.30, 0.22, 0.18, 0.13, 0.10, 0.07])
        else:
            h = np.linspace(n, 1, n, dtype=float)
    elif profile == "extreme_skew":
        if n == 2:
            h = np.array([0.90, 0.10])
        else:
            first = 0.55
            tail = np.geomspace(1.0, 0.25, n - 1)
            tail = tail / tail.sum() * (1.0 - first)
            h = np.concatenate([[first], tail])
    elif profile == "random":
        h = rng.dirichlet(np.linspace(2.5, 0.8, n))
    else:
        raise ValueError(f"Unknown rights profile: {profile}")
    return h / h.sum()


def _bounded_ou_path(
    rng: np.random.Generator,
    t: np.ndarray,
    speed: str,
    capacity_min_usd: float,
    capacity_max_usd: float,
) -> tuple[np.ndarray, dict[str, float]]:
    if speed not in SPEED_PARAMS:
        raise ValueError(f"Unknown stochastic speed regime: {speed}")
    pars = SPEED_PARAMS[speed]
    dt = float(t[1] - t[0])
    tau = pars["tau"]
    sd = pars["latent_sd"]
    phi = math.exp(-dt / tau)
    innovation_sd = sd * math.sqrt(max(1.0 - phi * phi, 1e-12))
    y = np.empty_like(t)
    y[0] = rng.normal(0.0, sd)
    eps = rng.normal(size=len(t) - 1)
    for k in range(1, len(t)):
        y[k] = phi * y[k - 1] + innovation_sd * eps[k - 1]
    phase = rng.uniform(0.0, 2.0 * math.pi)
    period = pars["sine_period"] * rng.uniform(0.8, 1.25)
    y = y + pars["sine_amp"] * np.sin(2.0 * math.pi * t / period + phase)
    capacity_mid = 0.5 * (capacity_min_usd + capacity_max_usd)
    capacity_half = 0.5 * (capacity_max_usd - capacity_min_usd)
    v = capacity_mid + capacity_half * np.tanh(y)
    # Tanh is strictly interior; the tiny clip protects numeric transformations.
    v = np.clip(v, capacity_min_usd + 1e-6, capacity_max_usd - 1e-6)
    meta = {
        "tau_hours": tau,
        "latent_stationary_sd": sd,
        "sine_period_hours": period,
    }
    return v, meta


def _smoothstep01(s: np.ndarray) -> np.ndarray:
    s = np.clip(s, 0.0, 1.0)
    return s * s * (3.0 - 2.0 * s)


def _stress_path(
    t: np.ndarray,
    speed: str,
    rng: np.random.Generator,
    capacity_min_usd: float,
    capacity_max_usd: float,
) -> tuple[np.ndarray, dict[str, float]]:
    u = t / t[-1]
    if speed == "stress_monotone_down":
        smooth = 0.5 - 0.5 * np.cos(np.pi * u)
        v = 2050.0 - 600.0 * smooth
    elif speed == "stress_monotone_up":
        smooth = 0.5 - 0.5 * np.cos(np.pi * u)
        v = 1450.0 + 600.0 * smooth
    elif speed == "stress_oscillatory":
        phase = rng.uniform(0, 2 * np.pi)
        v = 1750.0 + 320.0 * np.sin(2 * np.pi * t / 10.0 + phase) * (0.85 + 0.15 * np.sin(2 * np.pi * t / 41.0))
    elif speed == "stress_endpoint_reversal":
        # Long stable history followed by a continuous rapid endpoint move.
        start = 0.90
        smooth = _smoothstep01((u - start) / (1.0 - start))
        direction = rng.choice([-1.0, 1.0])
        v = 1750.0 + direction * 340.0 * smooth
    elif speed == "extreme_fast_full_sweep":
        # Repeated near-boundary sweeps with a 3--7 hour period.
        period = rng.uniform(3.0, 7.0)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        v = 1750.0 + 349.0 * np.sin(2.0 * np.pi * t / period + phase)
    elif speed == "extreme_chirp":
        # Oscillation frequency increases throughout the week without jumps.
        phase = 2.0 * np.pi * (1.0 * u + rng.uniform(18.0, 32.0) * u * u)
        v = 1750.0 + 349.0 * np.sin(phase + rng.uniform(0.0, 2.0 * np.pi))
    elif speed == "extreme_late_reversal":
        # An apparently stable history is followed by a full-range smooth reversal.
        start = rng.uniform(0.72, 0.92)
        smooth = _smoothstep01((u - start) / (1.0 - start))
        direction = rng.choice([-1.0, 1.0])
        v = 1750.0 - direction * 330.0 + direction * 660.0 * smooth
    elif speed == "extreme_narrow_excursions":
        # Short continuous spikes and dips make interval averages stale quickly.
        v = np.full_like(t, rng.uniform(1650.0, 1850.0))
        for _ in range(int(rng.integers(4, 9))):
            center = rng.uniform(0.04, 0.96)
            width = rng.uniform(0.0015, 0.012)
            amplitude = rng.choice([-1.0, 1.0]) * rng.uniform(300.0, 650.0)
            v += amplitude * np.exp(-0.5 * ((u - center) / width) ** 2)
    elif speed == "extreme_boundary_switch":
        # Smoothly alternates between long visits near both physical boundaries.
        period = rng.uniform(5.0, 14.0)
        phase = rng.uniform(0.0, 2.0 * np.pi)
        wave = np.tanh(5.0 * np.sin(2.0 * np.pi * t / period + phase)) / np.tanh(5.0)
        v = 1750.0 + 349.0 * wave
    elif speed == "extreme_random_fourier":
        # A continuous out-of-model path combining several unrelated time scales.
        signal = np.zeros_like(t)
        for period in rng.uniform(2.5, 36.0, size=8):
            signal += rng.normal() * np.sin(2.0 * np.pi * t / period + rng.uniform(0.0, 2.0 * np.pi))
        signal /= max(float(np.max(np.abs(signal))), 1e-12)
        v = 1750.0 + 349.0 * signal
    elif speed == "extreme_monotone_full":
        direction = rng.choice([-1.0, 1.0])
        smooth = _smoothstep01(u)
        v = 1750.0 + direction * 349.0 * (2.0 * smooth - 1.0)
    elif speed == "extreme_double_reversal":
        phase = rng.uniform(0.0, 2.0 * np.pi)
        v = 1750.0 + 349.0 * np.cos(4.0 * np.pi * u + phase)
    else:
        raise ValueError(f"Unknown stress path: {speed}")
    capacity_mid = 0.5 * (capacity_min_usd + capacity_max_usd)
    capacity_half = 0.5 * (capacity_max_usd - capacity_min_usd)
    v = capacity_mid + (v - 1750.0) * (capacity_half / 350.0)
    v = np.clip(v, capacity_min_usd, capacity_max_usd)
    return v, {"stress_path": 1.0}


def generate_v_path(
    rng: np.random.Generator,
    t: np.ndarray,
    speed: str,
    capacity_min_usd: float,
    capacity_max_usd: float,
) -> tuple[np.ndarray, dict[str, float]]:
    if speed in SPEED_PARAMS:
        return _bounded_ou_path(
            rng,
            t,
            speed,
            capacity_min_usd,
            capacity_max_usd,
        )
    return _stress_path(
        t,
        speed,
        rng,
        capacity_min_usd,
        capacity_max_usd,
    )


def _gaussian(u: np.ndarray, center: float, width: float) -> np.ndarray:
    width = max(width, 1e-3)
    return np.exp(-0.5 * ((u - center) / width) ** 2)


def _base_profiles(
    scenario: str,
    n: int,
    u: np.ndarray,
    v_mid: np.ndarray,
    rng: np.random.Generator,
    sample_hours: float,
    rights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    m = len(u)
    profiles = np.zeros((m, n), dtype=float)
    dollar_weights = rng.lognormal(mean=0.0, sigma=0.45, size=n)

    if scenario == "uniform":
        profiles[:] = 1.0
    elif scenario == "front_loaded":
        base = (1.0 - u + 0.03) ** 2.8
        profiles[:] = base[:, None]
    elif scenario == "back_loaded":
        base = (u + 0.03) ** 2.8
        profiles[:] = base[:, None]
    elif scenario == "single_burst":
        for i in range(n):
            center = rng.uniform(0.15, 0.85)
            width = rng.uniform(0.008, 0.035)
            profiles[:, i] = _gaussian(u, center, width)
    elif scenario == "multi_burst":
        for i in range(n):
            count = int(rng.integers(3, 7))
            for _ in range(count):
                profiles[:, i] += rng.uniform(0.5, 1.5) * _gaussian(
                    u, rng.uniform(0.03, 0.97), rng.uniform(0.006, 0.025)
                )
    elif scenario == "one_steady_others_burst":
        profiles[:, 0] = 1.0
        dollar_weights[0] *= 1.4
        for i in range(1, n):
            center = rng.uniform(0.1, 0.9)
            profiles[:, i] = _gaussian(u, center, rng.uniform(0.008, 0.03))
    elif scenario == "staggered":
        centers = np.linspace(0.08, 0.92, n)
        centers += rng.normal(0.0, 0.02, size=n)
        for i, center in enumerate(centers):
            profiles[:, i] = _gaussian(u, float(np.clip(center, 0.03, 0.97)), rng.uniform(0.012, 0.045))
    elif scenario == "overlapping":
        centers = rng.uniform(0.15, 0.85, size=int(rng.integers(2, 5)))
        widths = rng.uniform(0.012, 0.045, size=len(centers))
        common = sum(_gaussian(u, c, w) for c, w in zip(centers, widths))
        for i in range(n):
            profiles[:, i] = common * rng.uniform(0.8, 1.2) + 0.08
    elif scenario == "skewed_heavy":
        heavy = int(rng.integers(0, n))
        profiles[:] = 0.15
        profiles[:, heavy] += 1.0 + _gaussian(u, rng.uniform(0.2, 0.8), rng.uniform(0.02, 0.08))
        dollar_weights[:] *= 0.45
        dollar_weights[heavy] *= 7.0
    elif scenario in {"v_high_corr", "v_low_corr"}:
        rank = (v_mid - v_mid.min()) / max(v_mid.max() - v_mid.min(), 1e-9)
        if scenario == "v_low_corr":
            rank = 1.0 - rank
        common = 0.03 + rank**3
        for i in range(n):
            lag = int(rng.integers(-6, 7))
            profiles[:, i] = np.roll(common, lag) * rng.uniform(0.8, 1.2)
    elif scenario == "extreme_silent_then_burst":
        centers = rng.uniform(0.90, 0.995, size=n)
        for i, center in enumerate(centers):
            profiles[:, i] = _gaussian(u, center, rng.uniform(0.001, 0.004))
    elif scenario == "extreme_first_day_whale":
        profiles[:, 0] = _gaussian(u, rng.uniform(0.005, 0.05), rng.uniform(0.001, 0.006))
        dollar_weights[0] *= 20.0
        for i in range(1, n):
            profiles[:, i] = _gaussian(u, rng.uniform(0.55, 0.98), rng.uniform(0.002, 0.012))
    elif scenario == "extreme_alternating_spikes":
        for i in range(n):
            for center in np.linspace(0.04 + i / (3.0 * n), 0.96, 5):
                profiles[:, i] += _gaussian(u, float(center % 0.98), rng.uniform(0.0008, 0.0025))
    elif scenario == "extreme_simultaneous_spike":
        center = rng.uniform(0.05, 0.95)
        common = _gaussian(u, center, rng.uniform(0.0008, 0.003))
        for i in range(n):
            profiles[:, i] = common
    elif scenario == "extreme_sample_edge_bursts":
        sample_fraction = max(sample_hours / 168.0, 1.0 / len(u))
        edge_count = max(3, min(12, int(round(1.0 / sample_fraction)) - 1))
        edge_indices = rng.choice(np.arange(1, max(2, int(1.0 / sample_fraction))), size=edge_count, replace=False)
        for i in range(n):
            side = -1.0 if i % 2 == 0 else 1.0
            for edge in edge_indices:
                center = edge * sample_fraction + side * rng.uniform(0.0005, 0.002)
                profiles[:, i] += _gaussian(u, float(np.clip(center, 0.001, 0.999)), rng.uniform(0.0006, 0.0018))
    elif scenario == "extreme_v_opposed":
        rank = (v_mid - v_mid.min()) / max(v_mid.max() - v_mid.min(), 1e-9)
        for i in range(n):
            preferred = rank if i % 2 == 0 else 1.0 - rank
            profiles[:, i] = 1e-8 + preferred**10
    elif scenario == "extreme_rights_mismatch":
        smallest = int(np.argmin(rights))
        largest = int(np.argmax(rights))
        for i in range(n):
            center = 0.08 + 0.84 * i / max(n - 1, 1)
            profiles[:, i] = _gaussian(u, center, rng.uniform(0.001, 0.006))
        dollar_weights[smallest] *= 30.0
        dollar_weights[largest] *= 0.15
    elif scenario == "extreme_micro_macro":
        profiles[:] = 1e-5
        whale = int(rng.integers(0, n))
        for i in range(n):
            profiles[:, i] += _gaussian(u, rng.uniform(0.02, 0.98), rng.uniform(0.0008, 0.0025))
        dollar_weights[whale] *= 25.0
    else:
        raise ValueError(f"Unknown consumption scenario: {scenario}")

    # Multiplicative micro-bursting. A small floor is retained only for profiles
    # intended to be continuous; truly bursty profiles may remain zero.
    noise = rng.lognormal(mean=-0.5 * 0.55**2, sigma=0.55, size=(m, n))
    profiles *= noise
    if scenario in {"uniform", "front_loaded", "back_loaded", "overlapping", "v_high_corr", "v_low_corr"}:
        profiles += 1e-5
    return profiles, dollar_weights


def _generate_consumption_impl(
    rng: np.random.Generator,
    t: np.ndarray,
    v: np.ndarray,
    n: int,
    scenario: str,
    target_progress_low: float,
    target_progress_high: float,
    sample_hours: float,
    rights: np.ndarray,
) -> tuple[np.ndarray, dict[str, float]]:
    u = ((t[:-1] + t[1:]) * 0.5) / t[-1]
    v_mid = 0.5 * (v[:-1] + v[1:])
    profiles, dollar_weights = _base_profiles(scenario, n, u, v_mid, rng, sample_hours, rights)
    d_c = np.zeros_like(profiles)
    for i in range(n):
        s = profiles[:, i].sum()
        if s <= 0:
            profiles[int(rng.integers(0, len(u))), i] = 1.0
            s = 1.0
        d_c[:, i] = profiles[:, i] / s * dollar_weights[i]

    x_mid = 0.5 * (100.0 / v[:-1] + 100.0 / v[1:])
    raw_progress = float((d_c * x_mid[:, None]).sum())
    target = float(rng.uniform(target_progress_low, target_progress_high))
    scale = target / max(raw_progress, 1e-12)
    d_c *= scale

    # Round only at observation time, not in the hidden truth. Tiny numerical
    # amounts are set to zero to create genuine silent periods.
    d_c[d_c < 1e-9] = 0.0
    meta = {
        "target_total_progress_pp": target,
        "total_dollars": float(d_c.sum()),
        "largest_participant_dollar_share": float(d_c.sum(axis=0).max() / max(d_c.sum(), 1e-12)),
    }
    return d_c, meta


def simulate_truth(spec: SimulationSpec) -> SimulationTruth:
    rng = np.random.default_rng(spec.seed)
    steps = int(round(spec.horizon_hours / spec.dt_hours))
    t = np.linspace(0.0, spec.horizon_hours, steps + 1)
    v, v_meta = generate_v_path(
        rng,
        t,
        spec.speed,
        spec.capacity_min_usd,
        spec.capacity_max_usd,
    )
    rights = rights_vector(spec.n_participants, spec.rights_profile, rng)
    d_c, c_meta = _generate_consumption_impl(
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
    metadata = {**v_meta, **c_meta}
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
        metadata=metadata,
    )
