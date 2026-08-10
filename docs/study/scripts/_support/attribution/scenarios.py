"""Frozen scenario families and reproducible seed generation."""
from __future__ import annotations
import numpy as np
from simulate import ScenarioSpec

MAIN_FAMILIES = [
    "constant_mixed",
    "smooth_mixed",
    "piecewise_mixed",
    "smooth_staggered",
    "jump_staggered",
    "bursty_mean_reverting",
    "coarse_sampling",
    "sparse_low_utilization",
    "correlated_extreme",
    "adversarial_alignment",
]

OOD_FAMILIES = [
    "monotone_staggered",
    "rapid_switch_mixed",
    "chirp_staggered",
    "multi_jump_bursty",
    "narrow_spike_large_event",
]


def main_spec(family: str, seed: int) -> ScenarioSpec:
    rng = np.random.default_rng(seed)
    n = int(rng.choice([2, 2, 2, 5, 10]))
    events = int(rng.integers(100, 601))
    target = float(rng.uniform(20, 90))
    sampling = float(rng.choice([2, 5, 10, 30]))
    schedule = "mixed"
    process = "smooth"
    sigma = float(rng.uniform(0.55, 1.35))
    dominance = float(rng.choice([0.0, 0.0, 0.7]))
    heavy = float(rng.uniform(0.0, 0.04))
    if family == "constant_mixed":
        process = "constant"
    elif family == "smooth_mixed":
        process = "smooth"
    elif family == "piecewise_mixed":
        process = "piecewise"
    elif family == "smooth_staggered":
        process, schedule = "smooth", "staggered"
    elif family == "jump_staggered":
        process, schedule = "jump", "early_late"
        n = int(rng.choice([2, 2, 5]))
    elif family == "bursty_mean_reverting":
        process, schedule = "mean_reverting", "bursty"
        sigma = float(rng.uniform(1.0, 1.6))
        heavy = float(rng.uniform(0.03, 0.08))
    elif family == "coarse_sampling":
        process = str(rng.choice(["smooth", "piecewise", "jump"]))
        schedule = str(rng.choice(["mixed", "staggered", "bursty"]))
        sampling = float(rng.choice([60, 120, 180]))
        events = int(rng.integers(80, 401))
    elif family == "sparse_low_utilization":
        process = str(rng.choice(["constant", "smooth", "piecewise"]))
        schedule = str(rng.choice(["mixed", "staggered"]))
        target = float(rng.uniform(5, 25))
        events = int(rng.integers(25, 121))
        sampling = float(rng.choice([10, 30, 60]))
        n = int(rng.choice([2, 2, 5]))
    elif family == "correlated_extreme":
        process, schedule = "correlated_extreme", "alternating"
        n = int(rng.choice([2, 2, 5]))
        events = int(rng.integers(150, 501))
        sampling = float(rng.choice([5, 10, 30]))
    elif family == "adversarial_alignment":
        process, schedule = "user_aligned_adversarial", "early_late"
        n = 2
        events = int(rng.integers(120, 401))
        sampling = float(rng.choice([5, 10, 30]))
        target = float(rng.uniform(30, 85))
    else:
        raise ValueError(family)
    return ScenarioSpec(
        family, n, events, target, sampling, schedule, process, sigma,
        dominance, "fixed_offset", None, 0.15, heavy, int(seed)
    )


def ood_spec(family: str, seed: int) -> ScenarioSpec:
    rng = np.random.default_rng(seed)
    n = int(rng.choice([2, 2, 5, 10]))
    events = int(rng.integers(100, 651))
    target = float(rng.uniform(15, 92))
    sampling = float(rng.choice([2, 5, 10, 30, 60]))
    schedule, process = "mixed", "monotone"
    sigma = float(rng.uniform(0.6, 1.5))
    heavy = float(rng.uniform(0.0, 0.04))
    if family == "monotone_staggered":
        schedule, process = "staggered", "monotone"
    elif family == "rapid_switch_mixed":
        schedule, process = "mixed", "rapid_switching"
    elif family == "chirp_staggered":
        schedule, process = "staggered", "chirp"
    elif family == "multi_jump_bursty":
        schedule, process = "bursty", "multi_jump"
        heavy = float(rng.uniform(0.02, 0.08))
    elif family == "narrow_spike_large_event":
        schedule, process = "early_late", "narrow_spike"
        heavy = float(rng.uniform(0.10, 0.22))
        sigma = float(rng.uniform(1.1, 1.8))
    else:
        raise ValueError(family)
    return ScenarioSpec(
        family, n, events, target, sampling, schedule, process, sigma,
        0.0, "fixed_offset", None, 0.15, heavy, int(seed)
    )


def seed_jobs(families, repetitions, master_seed):
    sequence = np.random.SeedSequence(int(master_seed))
    children = sequence.spawn(len(families) * int(repetitions))
    jobs, k = [], 0
    for family in families:
        for _ in range(int(repetitions)):
            jobs.append((family, int(children[k].generate_state(1)[0])))
            k += 1
    return jobs
