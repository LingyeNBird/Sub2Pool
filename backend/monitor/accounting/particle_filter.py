"""未知整数显示规则下的时变周限混合粒子滤波。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dynamic_contracts import DynamicModelInput, ParticleFilterOutput

V_MIN = 1400.0
V_MAX = 4000.0
V_MID = 2700.0
V_HALF = 1300.0
QUANTIZER_NAMES = ("floor", "nearest", "ceil")


@dataclass(frozen=True)
class ParticleFilterConfig:
    particles: int = 480
    latent_stationary_sd: float = 0.60
    speed_taus_hours: tuple[float, ...] = (6.0, 24.0, 72.0)
    timing_dirichlet_alpha: float = 0.8
    observation_soft_sigma_pp: float = 0.05
    max_substeps: int = 6
    resample_ess_fraction: float = 0.50
    credible_mass: float = 0.90
    balance_interval_inflation: float = 1.30
    capacity_min_usd: float = V_MIN
    capacity_max_usd: float = V_MAX
    initial_capacity_usd: float | None = None
    initial_capacity_sd_usd: float = 120.0


def _weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    probabilities: tuple[float, ...],
) -> np.ndarray:
    order = np.argsort(values)
    ordered_values = values[order]
    cumulative = np.cumsum(weights[order])
    if cumulative[-1] <= 0:
        return np.quantile(ordered_values, probabilities)
    cumulative /= cumulative[-1]
    return np.interp(np.asarray(probabilities), cumulative, ordered_values)


def _display_cells(
    displayed: int,
    quantizer_codes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    lower = np.empty(len(quantizer_codes), dtype=float)
    upper = np.empty(len(quantizer_codes), dtype=float)
    floor_mask = quantizer_codes == 0
    nearest_mask = quantizer_codes == 1
    ceil_mask = quantizer_codes == 2
    lower[floor_mask], upper[floor_mask] = displayed, displayed + 1.0
    lower[nearest_mask], upper[nearest_mask] = displayed - 0.5, displayed + 0.5
    lower[ceil_mask], upper[ceil_mask] = displayed - 1.0, displayed
    return np.maximum(lower, 0.0), np.minimum(upper, 100.0)


def _distance_to_display_cell(
    hidden_percent: np.ndarray,
    displayed: int,
    quantizer_codes: np.ndarray,
) -> np.ndarray:
    lower, upper = _display_cells(displayed, quantizer_codes)
    return np.maximum(
        np.maximum(lower - hidden_percent, hidden_percent - upper),
        0.0,
    )


def _systematic_resample(
    weights: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    positions = (rng.random() + np.arange(len(weights))) / len(weights)
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    return np.searchsorted(cumulative, positions, side="left")


def run_particle_filter(
    model_input: DynamicModelInput,
    *,
    seed: int,
    config: ParticleFilterConfig | None = None,
) -> ParticleFilterOutput:
    """按完整因果观测序列返回每个时刻的点估计和概率区间。"""

    model_input.validate()
    cfg = config or ParticleFilterConfig()
    if cfg.particles < 30:
        raise ValueError("粒子数量过少")
    if cfg.capacity_min_usd >= cfg.capacity_max_usd:
        raise ValueError("容量下界必须小于容量上界")
    capacity_mid = 0.5 * (
        cfg.capacity_min_usd + cfg.capacity_max_usd
    )
    capacity_half = 0.5 * (
        cfg.capacity_max_usd - cfg.capacity_min_usd
    )

    rng = np.random.default_rng(seed)
    observation_count, subject_count = model_input.costs_usd.shape
    particle_count = cfg.particles

    if cfg.initial_capacity_usd is None:
        initial_capacity = rng.uniform(
            cfg.capacity_min_usd + 0.5,
            cfg.capacity_max_usd - 0.5,
            size=particle_count,
        )
    else:
        initial_capacity = np.clip(
            rng.normal(
                cfg.initial_capacity_usd,
                cfg.initial_capacity_sd_usd,
                size=particle_count,
            ),
            cfg.capacity_min_usd + 0.5,
            cfg.capacity_max_usd - 0.5,
        )
    latent_capacity = np.arctanh(
        np.clip(
            (initial_capacity - capacity_mid) / capacity_half,
            -0.999999,
            0.999999,
        )
    )
    speed_options = np.asarray(cfg.speed_taus_hours, dtype=float)
    speed_codes = rng.integers(0, len(speed_options), size=particle_count)
    speed_taus = speed_options[speed_codes]
    quantizer_codes = rng.integers(0, 3, size=particle_count)

    if model_input.baseline_exact_zero:
        baseline_hidden = np.zeros(particle_count, dtype=float)
    else:
        lower, upper = _display_cells(
            int(round(model_input.baseline_display_percent)),
            quantizer_codes,
        )
        baseline_hidden = rng.uniform(lower, np.maximum(lower, upper))

    attributed_particles = np.zeros(
        (particle_count, subject_count),
        dtype=float,
    )
    weights = np.full(particle_count, 1.0 / particle_count)

    capacity_hat = np.zeros(observation_count)
    capacity_lower = np.zeros(observation_count)
    capacity_upper = np.zeros(observation_count)
    total_hat = np.zeros(observation_count)
    total_lower = np.zeros(observation_count)
    total_upper = np.zeros(observation_count)
    attributed_hat = np.zeros((observation_count, subject_count))
    attributed_lower = np.zeros_like(attributed_hat)
    attributed_upper = np.zeros_like(attributed_hat)
    balance_hat = np.zeros_like(attributed_hat)
    balance_lower = np.zeros_like(attributed_hat)
    balance_upper = np.zeros_like(attributed_hat)
    quantizer_probabilities = np.zeros((observation_count, 3))
    speed_probabilities = np.zeros(
        (observation_count, len(speed_options))
    )
    ess_fraction = np.zeros(observation_count)
    resampled = np.zeros(observation_count, dtype=bool)
    lower_boundary_mass = np.zeros(observation_count)
    upper_boundary_mass = np.zeros(observation_count)

    tail = (1.0 - cfg.credible_mass) / 2.0
    interval_probabilities = (tail, 0.5, 1.0 - tail)

    def record(
        index: int,
        *,
        diagnostic_ess_fraction: float | None = None,
    ) -> None:
        capacity_particles = (
            capacity_mid + capacity_half * np.tanh(latent_capacity)
        )
        boundary_band = 0.05 * (
            cfg.capacity_max_usd - cfg.capacity_min_usd
        )
        lower_boundary_mass[index] = weights[
            capacity_particles <= cfg.capacity_min_usd + boundary_band
        ].sum()
        upper_boundary_mass[index] = weights[
            capacity_particles >= cfg.capacity_max_usd - boundary_band
        ].sum()
        total_particles = attributed_particles.sum(axis=1)
        remaining_percent = np.maximum(
            model_input.rights_percent[None, :] - attributed_particles,
            0.0,
        )
        balance_particles = (
            remaining_percent * capacity_particles[:, None] / 100.0
        )

        capacity_quantiles = _weighted_quantile(
            capacity_particles,
            weights,
            interval_probabilities,
        )
        total_quantiles = _weighted_quantile(
            total_particles,
            weights,
            interval_probabilities,
        )
        (
            capacity_lower[index],
            capacity_hat[index],
            capacity_upper[index],
        ) = capacity_quantiles
        total_lower[index], total_hat[index], total_upper[index] = (
            total_quantiles
        )
        for subject in range(subject_count):
            progress_quantiles = _weighted_quantile(
                attributed_particles[:, subject],
                weights,
                interval_probabilities,
            )
            balance_quantiles = _weighted_quantile(
                balance_particles[:, subject],
                weights,
                interval_probabilities,
            )
            (
                attributed_lower[index, subject],
                attributed_hat[index, subject],
                attributed_upper[index, subject],
            ) = progress_quantiles
            (
                balance_lower[index, subject],
                balance_hat[index, subject],
                balance_upper[index, subject],
            ) = balance_quantiles
        ess_fraction[index] = (
            diagnostic_ess_fraction
            if diagnostic_ess_fraction is not None
            else 1.0 / np.sum(weights * weights) / particle_count
        )
        for code in range(3):
            quantizer_probabilities[index, code] = weights[
                quantizer_codes == code
            ].sum()
        for code in range(len(speed_options)):
            speed_probabilities[index, code] = weights[
                speed_codes == code
            ].sum()

    record(0)
    for index in range(1, observation_count):
        delta_hours = float(
            model_input.times_hours[index]
            - model_input.times_hours[index - 1]
        )
        substeps = max(
            2,
            min(cfg.max_substeps, int(np.ceil(delta_hours))),
        )
        substep_hours = delta_hours / substeps
        inverse_capacities = np.empty((particle_count, substeps))
        for substep in range(substeps):
            persistence = np.exp(-substep_hours / speed_taus)
            innovation_sd = cfg.latent_stationary_sd * np.sqrt(
                np.maximum(1.0 - persistence * persistence, 1e-12)
            )
            latent_capacity = (
                persistence * latent_capacity
                + innovation_sd * rng.normal(size=particle_count)
            )
            capacity_at_substep = (
                capacity_mid + capacity_half * np.tanh(latent_capacity)
            )
            inverse_capacities[:, substep] = 100.0 / capacity_at_substep

        cost_delta = np.maximum(
            model_input.costs_usd[index]
            - model_input.costs_usd[index - 1],
            0.0,
        )
        interval_increments = np.zeros(
            (particle_count, subject_count),
            dtype=float,
        )
        for subject, cost in enumerate(cost_delta):
            if cost <= 0:
                continue
            timing_draws = rng.gamma(
                cfg.timing_dirichlet_alpha,
                1.0,
                size=(particle_count, substeps),
            )
            timing_weights = timing_draws / np.maximum(
                timing_draws.sum(axis=1, keepdims=True),
                1e-300,
            )
            effective_inverse = np.sum(
                timing_weights * inverse_capacities,
                axis=1,
            )
            interval_increments[:, subject] = cost * effective_inverse
        current_total = attributed_particles.sum(axis=1)
        remaining_to_exhaustion = np.maximum(
            100.0 - baseline_hidden - current_total,
            0.0,
        )
        interval_total = interval_increments.sum(axis=1)
        exhaustion_scale = np.minimum(
            1.0,
            remaining_to_exhaustion
            / np.maximum(interval_total, 1e-300),
        )
        attributed_particles += interval_increments * exhaustion_scale[:, None]

        absolute_hidden_percent = (
            baseline_hidden + attributed_particles.sum(axis=1)
        )
        distance = _distance_to_display_cell(
            absolute_hidden_percent,
            int(round(model_input.displayed_percent[index])),
            quantizer_codes,
        )
        log_likelihood = -0.5 * (
            distance / cfg.observation_soft_sigma_pp
        ) ** 2
        log_likelihood -= log_likelihood.max()
        weights *= np.exp(log_likelihood) + 1e-300
        total_weight = weights.sum()
        if not np.isfinite(total_weight) or total_weight <= 0:
            weights.fill(1.0 / particle_count)
        else:
            weights /= total_weight

        current_ess = 1.0 / np.sum(weights * weights)
        if current_ess < cfg.resample_ess_fraction * particle_count:
            selected = _systematic_resample(weights, rng)
            latent_capacity = latent_capacity[selected]
            speed_codes = speed_codes[selected]
            speed_taus = speed_taus[selected]
            quantizer_codes = quantizer_codes[selected]
            baseline_hidden = baseline_hidden[selected]
            attributed_particles = attributed_particles[selected]
            weights.fill(1.0 / particle_count)
            resampled[index] = True
        record(
            index,
            diagnostic_ess_fraction=current_ess / particle_count,
        )

    if cfg.balance_interval_inflation != 1.0:
        balance_lower = np.maximum(
            balance_hat
            - cfg.balance_interval_inflation
            * (balance_hat - balance_lower),
            0.0,
        )
        balance_upper = (
            balance_hat
            + cfg.balance_interval_inflation
            * (balance_upper - balance_hat)
        )

    return ParticleFilterOutput(
        capacity_hat_usd=capacity_hat,
        capacity_lower_usd=capacity_lower,
        capacity_upper_usd=capacity_upper,
        total_percent_hat=total_hat,
        total_percent_lower=total_lower,
        total_percent_upper=total_upper,
        attributed_percent_hat=attributed_hat,
        attributed_percent_lower=attributed_lower,
        attributed_percent_upper=attributed_upper,
        balance_hat_usd=balance_hat,
        balance_lower_usd=balance_lower,
        balance_upper_usd=balance_upper,
        quantizer_probabilities=quantizer_probabilities,
        speed_probabilities=speed_probabilities,
        ess_fraction=ess_fraction,
        resampled=resampled,
        lower_boundary_mass=lower_boundary_mass,
        upper_boundary_mass=upper_boundary_mass,
    )
