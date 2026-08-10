"""Synthetic generators for the V2 shared-resource attribution study.

The data-generating truth is q_j = c_j * x(t_j), where x=100/V is shared by
all events with exactly the same timestamp. The main model uses a fixed-offset
unit quantizer; alternative quantizers are reserved for stress tests.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from models import CycleData
from quantizers import QuantizerSpec, apply_quantizer

WEEK_MINUTES = 7 * 24 * 60
V_MIN, V_MAX = 1400.0, 2100.0
X_MIN, X_MAX = 100.0 / V_MAX, 100.0 / V_MIN

@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    n_users: int = 2
    n_events: int = 300
    target_progress: float = 60.0
    sampling_minutes: float = 10.0
    schedule: str = "mixed"
    rate_process: str = "smooth"
    cost_sigma: float = 1.0
    dominance: float = 0.0
    quantizer: str = "fixed_offset"
    quantizer_theta: float | None = None
    quantizer_irregularity: float = 0.15
    heavy_event_prob: float = 0.02
    seed: int = 0


def _reflect(values, lo=V_MIN, hi=V_MAX):
    values = np.asarray(values, float).copy()
    width = hi - lo
    y = lo + np.mod(values - lo, 2 * width)
    mask = y > hi
    y[mask] = hi - (y[mask] - hi)
    return np.clip(y, lo, hi)


def _user_weights(rng, n, dominance):
    if dominance > 0 and n > 1:
        d = float(np.clip(dominance, 1 / n, 0.95))
        w = np.full(n, (1 - d) / (n - 1))
        w[0] = d
        return w
    return rng.dirichlet(np.full(n, 1.5))


def _times_users(rng, N, n, duration, schedule, dominance):
    users = rng.choice(n, N, p=_user_weights(rng, n, dominance))
    if schedule == "mixed":
        times = rng.uniform(0, duration, N)
    elif schedule == "staggered":
        centers = (np.arange(n) + 0.5) / n * duration
        sd = duration / (4 * max(n, 2))
        times = np.array([
            rng.uniform(0, duration) if rng.random() < 0.15 else rng.normal(centers[u], sd)
            for u in users
        ])
        times = np.mod(times, duration)
    elif schedule == "early_late":
        centers = np.linspace(0.18, 0.82, n) * duration
        times = np.mod(rng.normal(centers[users], 0.08 * duration), duration)
    elif schedule == "bursty":
        clusters = max(3, int(np.sqrt(N) / 2))
        centers = rng.uniform(0, duration, (n, clusters))
        sd = duration / (35 + 2 * clusters)
        times = np.array([
            np.clip(rng.normal(rng.choice(centers[u]), sd), 0, duration)
            for u in users
        ])
    elif schedule == "alternating":
        bands = max(8, 2 * n)
        band = rng.integers(0, bands, N)
        users = band % n
        times = (band + rng.random(N)) / bands * duration
    elif schedule == "paired_ties":
        # Create exact cross-participant timestamp ties. Every base timestamp has
        # at least two events assigned to different users when n>1.
        group_count = max(1, (N + 1) // 2)
        base = np.sort(rng.uniform(0, duration, group_count))
        times = np.repeat(base, 2)[:N]
        users = np.empty(N, int)
        for g in range(group_count):
            a = 2 * g
            if a < N:
                users[a] = int(rng.integers(0, n))
            if a + 1 < N:
                users[a + 1] = (users[a] + int(rng.integers(1, n))) % n if n > 1 else 0
    elif schedule == "single_user_blocks":
        block = np.minimum((rng.uniform(0, 1, N) * n).astype(int), n - 1)
        users = block
        times = (block + rng.uniform(0.05, 0.95, N)) / n * duration
    else:
        raise ValueError(f"unknown schedule: {schedule}")
    order = np.argsort(times, kind="mergesort")
    return np.asarray(times, float)[order], np.asarray(users, int)[order]


def _v_values(rng, times, duration, process, users):
    frac = times / max(duration, 1e-12)
    if process == "constant":
        v = np.full_like(times, rng.uniform(V_MIN, V_MAX), dtype=float)
    elif process == "smooth":
        base = rng.uniform(1580, 1920)
        a1 = rng.uniform(100, 250)
        a2 = rng.uniform(20, 90)
        p1, p2 = rng.uniform(0, 2 * np.pi, 2)
        v = _reflect(base + a1 * np.sin(2 * np.pi * frac + p1) + a2 * np.sin(6 * np.pi * frac + p2))
    elif process in ("mean_reverting", "random_walk"):
        grid_n = max(32, int(duration / 90) + 1)
        grid = np.linspace(0, duration, grid_n)
        vals = np.empty(grid_n)
        vals[0] = rng.uniform(V_MIN, V_MAX)
        mu = rng.uniform(1600, 1900)
        phi = rng.uniform(0.88, 0.98)
        sigma = rng.uniform(25, 65)
        for k in range(1, grid_n):
            if process == "mean_reverting":
                vals[k] = mu + phi * (vals[k - 1] - mu) + rng.normal(0, sigma)
            else:
                vals[k] = vals[k - 1] + rng.normal(0, sigma)
            vals[k] = _reflect([vals[k]])[0]
        v = np.interp(times, grid, vals)
    elif process == "piecewise":
        segments = int(rng.integers(2, 7))
        cuts = np.sort(rng.uniform(0.08, 0.92, segments - 1))
        levels = rng.uniform(V_MIN, V_MAX, segments)
        v = levels[np.searchsorted(cuts, frac)]
    elif process == "jump":
        cut = rng.uniform(0.3, 0.7)
        left, right = (V_MIN, V_MAX) if rng.random() < 0.5 else (V_MAX, V_MIN)
        v = np.where(frac < cut, left, right)
    elif process == "correlated_extreme":
        band = np.minimum((frac * 8).astype(int), 7)
        v = np.where(band % 2 == 0, V_MIN, V_MAX)
    elif process == "user_aligned_adversarial":
        v = np.where(users % 2 == 0, V_MAX, V_MIN)
    elif process == "monotone":
        direction = 1 if rng.random() < 0.5 else -1
        lo, hi = (V_MIN, V_MAX) if direction > 0 else (V_MAX, V_MIN)
        power = rng.uniform(0.6, 2.2)
        v = lo + (hi - lo) * frac**power
    elif process == "rapid_switching":
        switches = int(rng.integers(10, 31))
        band = np.minimum((frac * switches).astype(int), switches - 1)
        low = rng.uniform(V_MIN, 1550)
        high = rng.uniform(1950, V_MAX)
        v = np.where(band % 2 == 0, low, high)
    elif process == "chirp":
        phase = 2 * np.pi * (1.5 * frac + rng.uniform(4, 10) * frac**2)
        base = rng.uniform(1670, 1830)
        amp = rng.uniform(240, 350)
        v = _reflect(base + amp * np.sin(phase + rng.uniform(0, 2 * np.pi)))
    elif process == "multi_jump":
        segments = int(rng.integers(6, 16))
        cuts = np.sort(rng.uniform(0.02, 0.98, segments - 1))
        levels = rng.choice([V_MIN, V_MAX, 1500.0, 1800.0, 2000.0], size=segments)
        v = levels[np.searchsorted(cuts, frac)]
    elif process == "narrow_spike":
        base = rng.uniform(1650, 1850)
        center = rng.uniform(0.2, 0.8)
        width = rng.uniform(0.004, 0.02)
        sign = 1 if rng.random() < 0.5 else -1
        v = base + sign * rng.uniform(400, 650) * np.exp(-0.5 * ((frac - center) / width) ** 2)
        v = np.clip(v, V_MIN, V_MAX)
    elif process == "sawtooth":
        cycles = int(rng.integers(3, 12))
        y = np.mod(frac * cycles + rng.uniform(), 1.0)
        if rng.random() < 0.5:
            y = 1 - y
        v = V_MIN + (V_MAX - V_MIN) * y
    else:
        raise ValueError(f"unknown rate process: {process}")
    return np.clip(v, V_MIN, V_MAX)


def simulate_cycle(spec: ScenarioSpec) -> CycleData:
    rng = np.random.default_rng(spec.seed)
    duration = float(WEEK_MINUTES)
    times, users = _times_users(rng, spec.n_events, spec.n_users, duration, spec.schedule, spec.dominance)
    v = _v_values(rng, times, duration, spec.rate_process, users)
    inverse_rates = 100.0 / v
    raw_cost = rng.lognormal(0.0, spec.cost_sigma, spec.n_events)
    heavy = rng.random(spec.n_events) < spec.heavy_event_prob
    if np.any(heavy):
        raw_cost[heavy] *= rng.uniform(5, 20, int(np.sum(heavy)))
    raw_q = float(np.sum(raw_cost * inverse_rates))
    costs = raw_cost * (float(spec.target_progress) / max(raw_q, 1e-12))
    q = costs * inverse_rates

    step = float(spec.sampling_minutes)
    sample_times = np.arange(0.0, duration + 0.5 * step, step)
    if sample_times[-1] < duration:
        sample_times = np.append(sample_times, duration)
    else:
        sample_times[-1] = duration
    cumulative_q = np.cumsum(q)
    event_index = np.searchsorted(times, sample_times, side="right") - 1
    progress = np.where(event_index >= 0, cumulative_q[np.maximum(event_index, 0)], 0.0)

    theta = float(rng.uniform()) if spec.quantizer_theta is None else float(spec.quantizer_theta)
    qspec = QuantizerSpec(
        spec.quantizer,
        theta,
        spec.quantizer_irregularity,
        0.5,
        float(rng.uniform()) if spec.quantizer == "switching_offset" else None,
    )
    observed, params = apply_quantizer(progress, sample_times, qspec, rng)
    return CycleData(
        duration,
        times,
        users,
        costs,
        inverse_rates,
        q,
        sample_times,
        progress,
        observed,
        spec.n_users,
        spec.quantizer,
        params,
        spec.name,
        spec.seed,
        {
            "rate_process": spec.rate_process,
            "schedule": spec.schedule,
            "sampling_minutes": spec.sampling_minutes,
            "target_progress": spec.target_progress,
            "n_events": spec.n_events,
            "cost_sigma": spec.cost_sigma,
            "dominance": spec.dominance,
            "v_min_realized": float(v.min()),
            "v_max_realized": float(v.max()),
            "simultaneous_groups": int(np.sum(np.unique(times, return_counts=True)[1] > 1)),
        },
    )
