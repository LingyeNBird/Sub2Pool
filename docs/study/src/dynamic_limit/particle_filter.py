from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import AlgorithmOutput, Observations



@dataclass(frozen=True)
class ParticleFilterConfig:
    particles: int = 400
    latent_stationary_sd: float = 0.78
    speed_taus_hours: tuple[float, ...] = (6.0, 24.0, 72.0)
    timing_dirichlet_alpha: float = 0.8
    observation_soft_sigma_pp: float = 0.22
    max_substeps: int = 6
    resample_ess_fraction: float = 0.50
    credible_mass: float = 0.90
    known_quantizer: str | None = None
    capacity_min_usd: float = 1400.0
    capacity_max_usd: float = 2100.0


def _systematic_resample(weights: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    n = len(weights)
    positions = (rng.random() + np.arange(n)) / n
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    return np.searchsorted(cumulative, positions, side="left")


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, probs: tuple[float, ...]) -> np.ndarray:
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    c = np.cumsum(w)
    if c[-1] <= 0:
        return np.quantile(v, probs)
    c = c / c[-1]
    return np.interp(np.asarray(probs), c, v)


def _distance_to_display_cell(p: np.ndarray, z: int, rule_code: np.ndarray) -> np.ndarray:
    lower = np.empty_like(p)
    upper = np.empty_like(p)
    floor_mask = rule_code == 0
    near_mask = rule_code == 1
    ceil_mask = rule_code == 2
    lower[floor_mask] = z
    upper[floor_mask] = z + 1.0
    lower[near_mask] = z - 0.5
    upper[near_mask] = z + 0.5
    lower[ceil_mask] = z - 1.0
    upper[ceil_mask] = z
    lower = np.maximum(lower, 0.0)
    upper = np.minimum(upper, 100.0)
    return np.maximum(np.maximum(lower - p, p - upper), 0.0)


def particle_filter(
    obs: Observations,
    rights: np.ndarray,
    seed: int,
    config: ParticleFilterConfig | None = None,
) -> AlgorithmOutput:
    cfg = config or ParticleFilterConfig()
    if cfg.capacity_min_usd >= cfg.capacity_max_usd:
        raise ValueError("capacity_min_usd must be below capacity_max_usd")
    capacity_mid = 0.5 * (cfg.capacity_min_usd + cfg.capacity_max_usd)
    capacity_half = 0.5 * (cfg.capacity_max_usd - cfg.capacity_min_usd)
    rng = np.random.default_rng(seed)
    n_particles = cfg.particles
    n_obs, n_participants = obs.c_obs.shape

    # Broad endpoint prior. The exact bounds are open under tanh, but the
    # numerical epsilon is immaterial at dollar precision.
    v0 = rng.uniform(
        cfg.capacity_min_usd + 0.5,
        cfg.capacity_max_usd - 0.5,
        size=n_particles,
    )
    y = np.arctanh(
        np.clip((v0 - capacity_mid) / capacity_half, -0.999999, 0.999999)
    )
    tau_choices = np.asarray(cfg.speed_taus_hours, dtype=float)
    tau_idx = rng.integers(0, len(tau_choices), size=n_particles)
    tau = tau_choices[tau_idx]
    if cfg.known_quantizer is None:
        quantizer_code = rng.integers(0, 3, size=n_particles)
    else:
        code_map = {"floor": 0, "nearest": 1, "ceil": 2}
        if cfg.known_quantizer not in code_map:
            raise ValueError(f"Unknown known_quantizer: {cfg.known_quantizer}")
        quantizer_code = np.full(n_particles, code_map[cfg.known_quantizer], dtype=int)
    q_particles = np.zeros((n_particles, n_participants), dtype=float)
    weights = np.full(n_particles, 1.0 / n_particles)

    b_hat = np.zeros((n_obs, n_participants))
    b_lower = np.zeros_like(b_hat)
    b_upper = np.zeros_like(b_hat)
    q_hat = np.zeros_like(b_hat)
    v_hat = np.zeros(n_obs)
    ess = np.zeros(n_obs)
    resampled = np.zeros(n_obs, dtype=int)
    capacity_quantiles = np.zeros((n_obs, 3))
    lower_boundary_mass = np.zeros(n_obs)
    upper_boundary_mass = np.zeros(n_obs)
    rule_probs = np.zeros((n_obs, 3))
    speed_probs = np.zeros((n_obs, len(tau_choices)))
    h_pp = 100.0 * rights

    alpha_tail = (1.0 - cfg.credible_mass) / 2.0
    probs = (alpha_tail, 0.5, 1.0 - alpha_tail)

    def record(k: int) -> None:
        nonlocal weights
        v_now = capacity_mid + capacity_half * np.tanh(y)
        remaining = np.maximum(h_pp[None, :] - q_particles, 0.0)
        b_particles = remaining * v_now[:, None] / 100.0
        capacity_quantiles[k] = _weighted_quantile(v_now, weights, probs)
        boundary_band = 0.05 * (
            cfg.capacity_max_usd - cfg.capacity_min_usd
        )
        lower_boundary_mass[k] = float(
            weights[v_now <= cfg.capacity_min_usd + boundary_band].sum()
        )
        upper_boundary_mass[k] = float(
            weights[v_now >= cfg.capacity_max_usd - boundary_band].sum()
        )
        v_hat[k] = _weighted_quantile(v_now, weights, (0.5,))[0]
        for i in range(n_participants):
            bq = _weighted_quantile(b_particles[:, i], weights, probs)
            qq = _weighted_quantile(q_particles[:, i], weights, (0.5,))
            b_lower[k, i], b_hat[k, i], b_upper[k, i] = bq
            q_hat[k, i] = qq[0]
        ess[k] = 1.0 / np.sum(weights * weights)
        for code in range(3):
            rule_probs[k, code] = float(weights[quantizer_code == code].sum())
        for j in range(len(tau_choices)):
            speed_probs[k, j] = float(weights[tau_idx == j].sum())

    record(0)

    for k in range(1, n_obs):
        delta_t = float(obs.times[k] - obs.times[k - 1])
        substeps = max(2, min(cfg.max_substeps, int(np.ceil(delta_t))))
        sub_dt = delta_t / substeps
        x_sub = np.empty((n_particles, substeps), dtype=float)
        for s in range(substeps):
            phi = np.exp(-sub_dt / tau)
            innovation_sd = cfg.latent_stationary_sd * np.sqrt(np.maximum(1.0 - phi * phi, 1e-12))
            y = phi * y + innovation_sd * rng.normal(size=n_particles)
            v_s = capacity_mid + capacity_half * np.tanh(y)
            x_sub[:, s] = 100.0 / v_s

        dc = np.maximum(obs.c_obs[k] - obs.c_obs[k - 1], 0.0)
        for i in range(n_participants):
            if dc[i] <= 0:
                continue
            gamma = rng.gamma(cfg.timing_dirichlet_alpha, 1.0, size=(n_particles, substeps))
            gamma_sum = gamma.sum(axis=1, keepdims=True)
            timing_weights = gamma / np.maximum(gamma_sum, 1e-300)
            x_eff = np.sum(timing_weights * x_sub, axis=1)
            q_particles[:, i] += dc[i] * x_eff

        p_particles = q_particles.sum(axis=1)
        distance = _distance_to_display_cell(p_particles, int(obs.z[k]), quantizer_code)
        log_like = -0.5 * (distance / cfg.observation_soft_sigma_pp) ** 2
        log_like -= log_like.max()
        weights *= np.exp(log_like) + 1e-300
        total = weights.sum()
        if not np.isfinite(total) or total <= 0:
            weights.fill(1.0 / n_particles)
        else:
            weights /= total

        current_ess = 1.0 / np.sum(weights * weights)
        if current_ess < cfg.resample_ess_fraction * n_particles:
            idx = _systematic_resample(weights, rng)
            y = y[idx]
            tau_idx = tau_idx[idx]
            tau = tau[idx]
            quantizer_code = quantizer_code[idx]
            q_particles = q_particles[idx]
            weights.fill(1.0 / n_particles)
            resampled[k] = 1
        record(k)

    l_hat = obs.c_obs + b_hat
    l_lower = obs.c_obs + b_lower
    l_upper = obs.c_obs + b_upper
    return AlgorithmOutput(
        algorithm="particle_filter_mixture",
        times=obs.times,
        b_hat=b_hat,
        l_hat=l_hat,
        b_lower=b_lower,
        b_upper=b_upper,
        l_lower=l_lower,
        l_upper=l_upper,
        q_hat=q_hat,
        v_hat=v_hat,
        diagnostics={
            "ess": ess,
            "resampled": resampled,
            "quantizer_probabilities": rule_probs,
            "speed_probabilities": speed_probs,
            "capacity_quantiles": capacity_quantiles,
            "lower_boundary_mass": lower_boundary_mass,
            "upper_boundary_mass": upper_boundary_mass,
            "config": cfg,
        },
    )


def guarded_particle_filter(
    pf: AlgorithmOutput,
    deterministic_set: AlgorithmOutput,
    obs: Observations,
    inertia: float = 0.20,
) -> AlgorithmOutput:
    """PF point estimate with deterministic-set clipping and mild causal inertia."""
    if deterministic_set.b_lower is None or deterministic_set.b_upper is None:
        raise ValueError("A deterministic interval is required")
    lower = deterministic_set.b_lower
    upper = deterministic_set.b_upper
    b = np.clip(pf.b_hat, lower, upper)
    out = np.empty_like(b)
    out[0] = b[0]
    for k in range(1, len(b)):
        candidate = (1.0 - inertia) * b[k] + inertia * out[k - 1]
        out[k] = np.clip(candidate, lower[k], upper[k])
    # Credible interval is intersected with the deterministic outer set. If the
    # model interval misses it entirely, fall back to the deterministic interval.
    if pf.b_lower is not None and pf.b_upper is not None:
        ci_l = np.maximum(pf.b_lower, lower)
        ci_u = np.minimum(pf.b_upper, upper)
        bad = ci_l > ci_u
        ci_l[bad] = lower[bad]
        ci_u[bad] = upper[bad]
    else:
        ci_l, ci_u = lower.copy(), upper.copy()
    return AlgorithmOutput(
        algorithm="pf_guarded",
        times=pf.times,
        b_hat=out,
        l_hat=obs.c_obs + out,
        b_lower=ci_l,
        b_upper=ci_u,
        l_lower=obs.c_obs + ci_l,
        l_upper=obs.c_obs + ci_u,
        q_hat=pf.q_hat,
        v_hat=pf.v_hat,
        diagnostics={"inertia": inertia, "base": pf.algorithm},
    )


def calibrated_particle_interval(
    pf: AlgorithmOutput,
    obs: Observations,
    deterministic_set: AlgorithmOutput | None = None,
    inflation: float = 1.8,
) -> AlgorithmOutput:
    """Inflate PF posterior intervals by a tuning-set calibration factor."""
    if pf.b_lower is None or pf.b_upper is None:
        raise ValueError("PF intervals are required")
    lower = pf.b_hat - inflation * (pf.b_hat - pf.b_lower)
    upper = pf.b_hat + inflation * (pf.b_upper - pf.b_hat)
    lower = np.maximum(lower, 0.0)
    if deterministic_set is not None and deterministic_set.b_lower is not None:
        lower = np.maximum(lower, deterministic_set.b_lower)
        upper = np.minimum(upper, deterministic_set.b_upper)
        bad = lower > upper
        lower[bad] = deterministic_set.b_lower[bad]
        upper[bad] = deterministic_set.b_upper[bad]
    return AlgorithmOutput(
        algorithm="particle_filter_calibrated",
        times=pf.times,
        b_hat=pf.b_hat.copy(),
        l_hat=pf.l_hat.copy(),
        b_lower=lower,
        b_upper=upper,
        l_lower=obs.c_obs + lower,
        l_upper=obs.c_obs + upper,
        q_hat=pf.q_hat,
        v_hat=pf.v_hat,
        diagnostics={"inflation": inflation, "base": pf.algorithm},
    )
