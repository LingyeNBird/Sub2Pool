"""Deterministic synthetic quota observations; never real participant data."""
import numpy as np
from monitor.research.data import Block


def simulate(factors=(1, 1, 2, 1), seed=1, cycles=5, drift=.025):
    rng = np.random.default_rng(seed)
    result = []
    for j in range(cycles):
        rows, pct, last = [], 0, 0
        capacity = 1200. * np.exp(rng.normal(0, .3))
        t = j * 200
        for i in range(20):
            capacity *= np.exp(rng.normal(0, drift))
            share = rng.uniform(.05, .95)
            fractions = rng.dirichlet(np.ones(4) * 1.2)
            dp = rng.uniform(3.6, 4.4)
            pct += dp
            endpoint = round(pct)
            delta, last = endpoint - last, endpoint
            raw = dp / 100 * capacity / ((1-share) + share * (fractions @ np.array(factors)))
            rows.append(Block(t+i, t+i+1, delta, raw*(1-share), tuple(raw*share*fractions), 20, 30))
        result.append(rows)
    return result
