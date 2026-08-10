from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .algorithms import (
    adaptive_phase_extension,
    amount_proportion,
    multiphase_window,
    multiscale_window,
    single_window,
    static_allocation,
)
from .generators import simulate_truth
from .metrics import evaluate_output, phase_diagnostic_rows, trajectory_frame
from .models import AlgorithmOutput, SimulationSpec
from .observation import make_observations
from .particle_filter import (
    ParticleFilterConfig,
    calibrated_particle_interval,
    guarded_particle_filter,
    particle_filter,
)
from .set_identification import recursive_outer_set


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def stable_seed(base: int, text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int((base + int.from_bytes(digest[:8], "little")) % (2**32 - 1))


def make_tuning_specs(config: dict[str, Any]) -> list[SimulationSpec]:
    rng = np.random.default_rng(int(config["base_seed"]))
    all_combos = list(
        itertools.product(
            config["speeds"],
            config["participants"],
            config["rights_profiles"],
            config["scenarios"],
            config["sample_hours"],
            config["quantizers"],
        )
    )
    count = int(config["num_cases"])
    chosen = rng.choice(len(all_combos), size=count, replace=False)
    specs = []
    for pos, idx in enumerate(chosen):
        speed, n, rights, scenario, sample, quantizer = all_combos[int(idx)]
        case_id = f"tune_{pos:04d}_{speed}_n{n}_{scenario}_{sample:g}h_{quantizer}"
        specs.append(
            SimulationSpec(
                case_id=case_id,
                seed=stable_seed(int(config["base_seed"]), case_id),
                speed=str(speed),
                n_participants=int(n),
                rights_profile=str(rights),
                scenario=str(scenario),
                sample_hours=float(sample),
                quantizer=str(quantizer),
                horizon_hours=float(config["horizon_hours"]),
                dt_hours=float(config["dt_hours"]),
                target_progress_low=float(config["target_progress"][0]),
                target_progress_high=float(config["target_progress"][1]),
                capacity_min_usd=float(config.get("capacity_range", [1400.0, 2100.0])[0]),
                capacity_max_usd=float(config.get("capacity_range", [1400.0, 2100.0])[1]),
            )
        )
    return specs


def make_final_specs(config: dict[str, Any]) -> tuple[list[SimulationSpec], list[SimulationSpec]]:
    rights_profiles = tuple(config["rights_profiles"])
    specs: list[SimulationSpec] = []
    counter = 0
    for speed, n, scenario, sample, quantizer in itertools.product(
        config["speeds"],
        config["participants"],
        config["scenarios"],
        config["sample_hours"],
        config["quantizers"],
    ):
        reps = 2 if scenario in set(config.get("challenging_second_replicate", [])) else 1
        for rep in range(reps):
            rights = rights_profiles[(counter + rep) % len(rights_profiles)]
            case_id = f"test_{counter:04d}_r{rep}_{speed}_n{n}_{scenario}_{sample:g}h_{quantizer}"
            specs.append(
                SimulationSpec(
                    case_id=case_id,
                    seed=stable_seed(int(config["base_seed"]), case_id),
                    speed=str(speed),
                    n_participants=int(n),
                    rights_profile=rights,
                    scenario=str(scenario),
                    sample_hours=float(sample),
                    quantizer=str(quantizer),
                    horizon_hours=float(config["horizon_hours"]),
                    dt_hours=float(config["dt_hours"]),
                    target_progress_low=float(config["target_progress"][0]),
                    target_progress_high=float(config["target_progress"][1]),
                    capacity_min_usd=float(config.get("capacity_range", [1400.0, 2100.0])[0]),
                    capacity_max_usd=float(config.get("capacity_range", [1400.0, 2100.0])[1]),
                )
            )
            counter += 1

    stress: list[SimulationSpec] = []
    counter = 0
    for speed, n, scenario, sample, quantizer in itertools.product(
        config["stress_speeds"],
        config["participants"],
        config["stress_scenarios"],
        config["sample_hours"],
        config["quantizers"],
    ):
        rights = rights_profiles[counter % len(rights_profiles)]
        case_id = f"stress_{counter:04d}_{speed}_n{n}_{scenario}_{sample:g}h_{quantizer}"
        stress.append(
            SimulationSpec(
                case_id=case_id,
                seed=stable_seed(int(config["base_seed"]) + 909090, case_id),
                speed=str(speed),
                n_participants=int(n),
                rights_profile=rights,
                scenario=str(scenario),
                sample_hours=float(sample),
                quantizer=str(quantizer),
                horizon_hours=float(config["horizon_hours"]),
                dt_hours=float(config["dt_hours"]),
                target_progress_low=float(config["target_progress"][0]),
                target_progress_high=float(config["target_progress"][1]),
                capacity_min_usd=float(config.get("capacity_range", [1400.0, 2100.0])[0]),
                capacity_max_usd=float(config.get("capacity_range", [1400.0, 2100.0])[1]),
            )
        )
        counter += 1
    return specs, stress


def default_selected_parameters() -> dict[str, Any]:
    return {
        "single_width_pp": 6,
        "multiscale_widths_pp": [3, 6, 12],
        "multiphase_width_pp": 6,
        "adaptive_widths_pp": [3, 6, 9, 12, 18],
        "adaptive_spread_threshold": 0.08,
        "window_shrink_pp": 1.5,
        "pf_observation_soft_sigma_pp": 0.25,
        "pf_timing_dirichlet_alpha": 0.8,
        "pf_interval_inflation": 2.0,
        "pf_guarded_inertia": 0.1,
    }


def run_standard_case(
    spec: SimulationSpec,
    selected: dict[str, Any],
    particle_count: int = 320,
    save_trajectory: bool = False,
) -> tuple[list[dict[str, Any]], pd.DataFrame, pd.DataFrame | None, dict[str, Any]]:
    truth = simulate_truth(spec)
    obs = make_observations(truth)

    set_output = recursive_outer_set(
        obs,
        truth.rights,
        capacity_min_usd=spec.capacity_min_usd,
        capacity_max_usd=spec.capacity_max_usd,
    )
    pf_cfg = ParticleFilterConfig(
        particles=int(particle_count),
        latent_stationary_sd=float(
            selected.get("pf_latent_stationary_sd", 0.78)
        ),
        observation_soft_sigma_pp=float(selected["pf_observation_soft_sigma_pp"]),
        timing_dirichlet_alpha=float(selected["pf_timing_dirichlet_alpha"]),
        capacity_min_usd=spec.capacity_min_usd,
        capacity_max_usd=spec.capacity_max_usd,
    )
    pf = particle_filter(obs, truth.rights, seed=(spec.seed + 7919) % (2**32 - 1), config=pf_cfg)
    pf_cal = calibrated_particle_interval(
        pf,
        obs,
        deterministic_set=set_output,
        inflation=float(selected["pf_interval_inflation"]),
    )
    pf_guard = guarded_particle_filter(
        pf_cal,
        set_output,
        obs,
        inertia=float(selected["pf_guarded_inertia"]),
    )

    outputs: list[AlgorithmOutput] = [
        static_allocation(obs, truth.rights),
        amount_proportion(obs, truth.rights),
        single_window(
            obs,
            truth.rights,
            width_pp=float(selected["single_width_pp"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        multiscale_window(
            obs,
            truth.rights,
            widths_pp=selected["multiscale_widths_pp"],
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        multiphase_window(
            obs,
            truth.rights,
            width_pp=int(selected["multiphase_width_pp"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        adaptive_phase_extension(
            obs,
            truth.rights,
            widths_pp=selected["adaptive_widths_pp"],
            spread_threshold=float(selected["adaptive_spread_threshold"]),
            shrink_pp=float(selected["window_shrink_pp"]),
        ),
        set_output,
        pf,
        pf_cal,
        pf_guard,
    ]
    metrics = []
    true_q_code = {"floor": 0, "nearest": 1, "ceil": 2}[obs.quantizer]
    for out in outputs:
        met = evaluate_output(truth, obs, out)
        if out.algorithm == "particle_filter_mixture":
            probs = out.diagnostics["quantizer_probabilities"]
            met["pf_final_true_quantizer_probability"] = float(probs[-1, true_q_code])
            met["pf_mean_ess_fraction"] = float(np.mean(out.diagnostics["ess"]) / pf_cfg.particles)
            met["pf_resample_count"] = int(np.sum(out.diagnostics["resampled"]))
        else:
            met["pf_final_true_quantizer_probability"] = np.nan
            met["pf_mean_ess_fraction"] = np.nan
            met["pf_resample_count"] = np.nan
        metrics.append(met)
    phase_frames = [phase_diagnostic_rows(truth, obs, out) for out in outputs]
    phase_frames = [df for df in phase_frames if not df.empty]
    phase_df = pd.concat(phase_frames, ignore_index=True) if phase_frames else pd.DataFrame()
    trajectory = trajectory_frame(truth, obs, outputs) if save_trajectory else None
    metadata = {
        "case_id": spec.case_id,
        "rights": truth.rights.tolist(),
        **truth.metadata,
        "num_observations": int(len(obs.times)),
        "true_quantizer": obs.quantizer,
    }
    return metrics, phase_df, trajectory, metadata


def save_json(path: str | Path, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
