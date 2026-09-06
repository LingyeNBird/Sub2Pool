"""Blocked, out-of-cycle predictive comparison with latent capacity drift.

This is an observational model-comparison experiment, NOT an estimate of
OpenAI's internal billing code. See docs/research-method.md for assumptions,
identifiability, priors, discretization and the meaning of support percentages.
"""
from functools import lru_cache
import math
import numpy as np
from .data import Block
from .protocol import (
    candidates, FAMILIES, DRIFT, MIN_BLOCKS, MIN_CYCLES, MIN_FAMILY_REQUESTS,
    MIN_REQUESTS, WINDOW_DAYS, EXCLUSION_KEYS,
)


def logsumexp(values, axis=None):
    maximum = np.max(values, axis=axis, keepdims=True)
    result = maximum + np.log(np.sum(np.exp(values - maximum), axis=axis, keepdims=True))
    return result.squeeze(axis) if axis is not None else result.item()


@lru_cache(maxsize=1)
def _grid():
    grid = candidates()
    factors = np.array([point for _, point in grid])
    groups = [np.array([i for i, (family, _) in enumerate(grid) if family == name]) for name in FAMILIES]
    return factors, groups


def cycle_likelihood(blocks):
    factors, _ = _grid()
    d = np.array([b.quota for b in blocks])
    baseline = np.array([b.baseline for b in blocks])
    target = np.array([b.target for b in blocks])
    cost = baseline[:, None] + target @ factors.T
    # First differences eliminate the account-cycle's unknown log capacity.
    z = np.log(cost / (d[:, None] / 100))
    differences = np.diff(z, axis=0)
    n = len(blocks)
    # Integer endpoints have uniform rounding variance 1/12 pp^2. Adjacent
    # blocks share an endpoint, so their errors are negatively correlated.
    r = np.diag(1 / (6 * d**2) + .03**2)
    for i in range(n - 1):
        if abs(blocks[i].end - blocks[i+1].start) < 1e-8:
            r[i, i+1] = r[i+1, i] = -1 / (12 * d[i] * d[i+1])
    diff = np.diff(np.eye(n), axis=0)
    centers = np.array([(b.start + b.end) / 2 for b in blocks])
    dt = np.maximum(.01, np.diff(centers))
    likelihoods, adequacy = [], []
    for drift in DRIFT:
        covariance = diff @ r @ diff.T + np.diag(drift**2 * dt)
        chol = np.linalg.cholesky(covariance)
        residuals = np.linalg.solve(chol, differences)
        squares = np.sum(residuals**2, axis=0)
        likelihoods.append(-.5 * (squares + 2*np.log(np.diag(chol)).sum() + (n-1)*math.log(2*math.pi)))
        adequacy.append(squares / (n-1))
    # Same fixed drift prior for every candidate. Never reuses capacity estimates
    # produced by Sub2Pool's currently configured billing-correction policy.
    ll = logsumexp(np.array(likelihoods), axis=0) - math.log(len(DRIFT))
    shares = target / (baseline + target.sum(axis=1))[:, None]
    design = np.diff(shares, axis=0)
    return ll, np.array(likelihoods), min(adequacy[-1]), design


def predictive_scores(log_likelihoods, contrasts):
    """Leave an entire account-cycle out, integrating parameters on other cycles.

    A common family prior and a uniform predeclared within-family grid avoid
    rewarding the mixed family just because it tries more parameter vectors.
    """
    _, groups = _grid()
    total = log_likelihoods.sum(axis=0)
    scores = np.zeros((len(log_likelihoods), len(FAMILIES)))
    for cycle, ll in enumerate(log_likelihoods):
        for family, indices in enumerate(groups):
            train = total[indices] - ll[indices]
            train -= logsumexp(train)
            scores[cycle, family] = logsumexp(train + ll[indices]) / contrasts[cycle]
    # Per-contrast gain over unchanged, bounded to limit outlier influence.
    return np.clip(scores - scores[:, [0]], -4, 4)


def bootstrap_support(scores, draws=1024):
    if len(scores) == 0:
        return np.zeros(len(FAMILIES))
    rng = np.random.default_rng(60806)
    weights = rng.exponential(size=(draws, len(scores)))
    means = (weights @ scores) / weights.sum(axis=1)[:, None]
    winners = np.isclose(means, means.max(axis=1)[:, None], rtol=0, atol=1e-8)
    # Split exact ties instead of manufacturing a confident arbitrary winner.
    return (winners / winners.sum(axis=1)[:, None]).mean(axis=0)


def analyze(cycles: list[list[Block]], exclusions=None, *, gateway_only=False):
    blocks = [b for cycle in cycles for b in cycle]
    total = sum(b.baseline + sum(b.target) for b in blocks)
    target_cost = sum(sum(b.target) for b in blocks)
    base_count = sum(b.baseline_requests for b in blocks)
    target_count = sum(b.target_requests for b in blocks)
    summary = {
        "window_days": WINDOW_DAYS, "requests": base_count + target_count,
        "baseline_requests": base_count, "gpt6_requests": target_count,
        "raw_usd": round(total), "gpt6_raw_usd": min(round(target_cost), round(total)),
        "quota_points": round(sum(b.quota for b in blocks), 1),
        "cycles": len(cycles), "blocks": len(blocks), "eligible": False,
        "gateway_only": bool(gateway_only), "status": "insufficient_data",
        "identifiable": [False]*4, "design_rank": 0,
        "exclusions": {key: int((exclusions or {}).get(key, 0)) for key in EXCLUSION_KEYS},
        "score_mean": [0.0]*7, "score_cov": [[0.0]*7 for _ in range(7)],
        "support": [0.0]*7, "factor_estimates": [[1.0]*4 for _ in range(7)],
    }
    if len(cycles) < MIN_CYCLES or len(blocks) < MIN_BLOCKS or min(base_count, target_count) < MIN_FAMILY_REQUESTS or summary["requests"] < MIN_REQUESTS:
        return summary
    # Defensive validation at the pure-function boundary as well as at extraction.
    for cycle in cycles:
        if len(cycle) < 2:
            raise ValueError("research cycle needs at least two blocks")
        for b in cycle:
            if b.end <= b.start or b.quota < 3 or not all(math.isfinite(x) and x >= 0 for x in [b.baseline, *b.target]):
                raise ValueError("invalid research block")
    likelihoods, drift_likelihoods, designs, fit = [], [], [], []
    for cycle in cycles:
        ll, by_drift, adequacy, design = cycle_likelihood(cycle)
        likelihoods.append(ll); drift_likelihoods.append(by_drift)
        designs.append(design); fit.append(adequacy)
    design = np.vstack(designs)
    singular = np.linalg.svd(design, compute_uv=False)
    rank = int(np.sum(singular > max(.03, singular[0]*.02)))
    summary["design_rank"] = rank
    norms = np.linalg.norm(design, axis=0)
    # Individual exposure variation is necessary, but not sufficient for full
    # separation; rank is reported independently rather than hidden.
    active = norms > .05
    resolved = []
    for column in range(4):
        others = design[:, [i for i in range(4) if i != column and active[i]]]
        residual = design[:, column]
        if others.shape[1]:
            residual = residual - others @ np.linalg.lstsq(others, residual, rcond=.02)[0]
        resolved.append(bool(active[column] and np.linalg.norm(residual) > .05))
    summary["identifiable"] = resolved
    shares = [np.array([sum(b.target)/(b.baseline+sum(b.target)) for b in cycle]) for cycle in cycles]
    energy = np.sum(design**2, axis=1)
    concentrated = np.sort(energy)[-3:].sum() > .6 * max(energy.sum(), 1e-12)
    # One coincident model switch and quota shock is observationally confounded;
    # correlated token mixtures cannot identify which component changed price.
    if rank == 0 or rank < int(active.sum()) or not all(resolved[i] for i in range(4) if active[i]) or concentrated or max(np.std(v) for v in shares) < .05:
        summary["status"] = "unidentifiable"
        return summary
    ll = np.array(likelihoods)
    contrasts = np.array([len(cycle)-1 for cycle in cycles])
    scores = predictive_scores(ll, contrasts)
    summary["score_mean"] = np.round(scores.mean(axis=0), 8).tolist()
    summary["score_cov"] = np.round(np.cov(scores, rowvar=False), 8).tolist()
    support = bootstrap_support(scores)
    summary["support"] = np.round(support, 8).tolist()
    factors, groups = _grid()
    for i, indices in enumerate(groups):
        joint = ll[:, indices].sum(axis=0)
        weights = np.exp(joint - logsumexp(joint))
        summary["factor_estimates"][i] = np.round(weights @ factors[indices], 5).tolist()
    # An absolute goodness-of-fit escape hatch: all candidate explanations can
    # be wrong. Do not normalize poor explanations into an apparent discovery.
    if np.median(fit) > 4:
        summary["status"] = "model_mismatch"
    else:
        sensitivities = [bootstrap_support(predictive_scores(np.array(drift_likelihoods)[:, i, :], contrasts), draws=256) for i in range(len(DRIFT))]
        winners = {int(np.argmax(s)) for s in sensitivities if s.max() > .65}
        summary["status"] = "drift_sensitive" if len(winners) > 1 else "exploratory"
    if not gateway_only:
        summary["status"] = "external_usage_uncontrolled"
    summary["eligible"] = summary["status"] == "exploratory"
    if not summary["eligible"]:
        # Local diagnostics can still inspect mean scores, but the UI/receiver
        # must not display a misleading percentage under a failed quality gate.
        summary["support"] = [0.0]*7
    return summary
