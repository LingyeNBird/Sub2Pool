#!/usr/bin/env python3
"""只读回放 Sub2Pool 历史，比较容量范围与粒子数量。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def unknown_display_cell(displayed: float) -> tuple[float, float]:
    return max(0.0, displayed - 1.0), min(100.0, displayed + 1.0)


def main() -> None:
    args = parse_args()
    os.environ.setdefault("DJANGO_DEBUG", "true")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinche.settings")
    os.environ["PINCH_DATA_DIR"] = str(args.data_dir.resolve())
    sys.path.insert(0, str(args.backend.resolve()))

    import django

    django.setup()

    from monitor.accounting.boundaries import infer_segments
    from monitor.accounting.model_inputs import (
        build_dynamic_replay_input,
        stable_segment_seed,
    )
    from monitor.accounting.particle_filter import (
        ParticleFilterConfig,
        run_particle_filter,
    )
    from monitor.fast_correction.prefix import FastCorrectionPrefix
    from monitor.models import AppSettings, Observation

    selected = json.loads(args.selected.read_text(encoding="utf-8"))
    config = AppSettings.load()
    account_id = int(config.openai_account_id)
    observations = list(
        Observation.objects.filter(account_id=account_id)
        .prefetch_related("participant_snapshots__participant")
        .order_by("observed_at", "id")
    )
    candidates = [item for item in observations if item.exclusion_source != "manual"]
    segments, _ = infer_segments(candidates)
    correction_prefix = FastCorrectionPrefix(account_id, config.cost_basis)
    replay_inputs = [
        build_dynamic_replay_input(
            account_id=account_id,
            segment=segment,
            config=config,
            correction_prefix=correction_prefix,
        )
        for segment in segments
        if segment.observations
    ]

    variants = [
        {
            "name": "legacy_1400_2100_p320",
            "particles": 320,
            "capacity_min_usd": 1400.0,
            "capacity_max_usd": 2100.0,
            "latent_sd": 0.78,
            "sigma": 0.15,
            "alpha": 0.8,
            "seed_offset": 0,
            "speed_taus_hours": [6.0, 24.0, 72.0],
        },
        *[
            {
                "name": f"expanded_1000_3500_p{particles}",
                "particles": particles,
                "capacity_min_usd": 1000.0,
                "capacity_max_usd": 3500.0,
                "latent_sd": float(selected["pf_latent_stationary_sd"]),
                "sigma": float(selected["pf_observation_soft_sigma_pp"]),
                "alpha": float(selected["pf_timing_dirichlet_alpha"]),
                "seed_offset": 0,
                "speed_taus_hours": [6.0, 24.0, 72.0],
            }
            for particles in (320, 480, 960)
        ],
        *[
            {
                "name": f"expanded_1000_3500_p480_seed{seed_offset}",
                "particles": 480,
                "capacity_min_usd": 1000.0,
                "capacity_max_usd": 3500.0,
                "latent_sd": float(selected["pf_latent_stationary_sd"]),
                "sigma": float(selected["pf_observation_soft_sigma_pp"]),
                "alpha": float(selected["pf_timing_dirichlet_alpha"]),
                "seed_offset": seed_offset,
                "speed_taus_hours": [6.0, 24.0, 72.0],
            }
            for seed_offset in (1, 2, 3)
        ],
        {
            "name": "expanded_1000_3500_p480_long_taus",
            "particles": 480,
            "capacity_min_usd": 1000.0,
            "capacity_max_usd": 3500.0,
            "latent_sd": float(selected["pf_latent_stationary_sd"]),
            "sigma": float(selected["pf_observation_soft_sigma_pp"]),
            "alpha": float(selected["pf_timing_dirichlet_alpha"]),
            "seed_offset": 0,
            "speed_taus_hours": [6.0, 24.0, 72.0, 168.0, 336.0],
        },
    ]

    variant_rows = []
    for variant in variants:
        prior_capacity = None
        segment_rows = []
        for segment, replay_input in zip(segments, replay_inputs, strict=True):
            filter_config = ParticleFilterConfig(
                particles=int(variant["particles"]),
                latent_stationary_sd=float(variant["latent_sd"]),
                speed_taus_hours=tuple(variant["speed_taus_hours"]),
                timing_dirichlet_alpha=float(variant["alpha"]),
                observation_soft_sigma_pp=float(variant["sigma"]),
                capacity_min_usd=float(variant["capacity_min_usd"]),
                capacity_max_usd=float(variant["capacity_max_usd"]),
                initial_capacity_usd=prior_capacity,
                initial_capacity_sd_usd=120.0,
            )
            seed = (stable_segment_seed(account_id, segment) + int(variant["seed_offset"])) % (2**64)
            output = run_particle_filter(
                replay_input.model_input,
                seed=seed,
                config=filter_config,
            )
            row = replay_input.observation_row_indices[-1]
            prior_capacity = float(output.capacity_hat_usd[row])
            displayed = float(replay_input.model_input.displayed_percent[row])
            baseline = float(replay_input.model_input.baseline_display_percent)
            absolute_progress = baseline + float(output.total_percent_hat[row])
            display_lower, display_upper = unknown_display_cell(displayed)
            subject_count = len(replay_input.subject_user_ids)
            segment_rows.append(
                {
                    "observations": len(segment.observations),
                    "displayed_percent": displayed,
                    "baseline_display_percent": baseline,
                    "selected_total_cost_usd": float(replay_input.selected_totals[-1]),
                    "capacity_hat_usd": prior_capacity,
                    "capacity_interval_usd": [
                        float(output.capacity_lower_usd[row]),
                        float(output.capacity_upper_usd[row]),
                    ],
                    "absolute_progress_hat": absolute_progress,
                    "display_compatible": display_lower - 1e-9 <= absolute_progress <= display_upper + 1e-9,
                    "progress_interval": [
                        float(output.total_percent_lower[row]),
                        float(output.total_percent_upper[row]),
                    ],
                    "ess_fraction": float(output.ess_fraction[row]),
                    "resample_count": int(output.resampled.sum()),
                    "charged_percent_by_subject": [
                        float(output.attributed_percent_hat[row, subject])
                        for subject in range(subject_count)
                    ],
                    "balance_usd_by_subject": [
                        float(output.balance_hat_usd[row, subject])
                        for subject in range(subject_count)
                    ],
                }
            )
        variant_rows.append({"variant": variant, "segments": segment_rows})

    expanded = {
        row["variant"]["name"]: row["segments"][-1]
        for row in variant_rows
        if row["variant"]["name"].startswith("expanded_1000_3500_p480")
    }
    charged = np.asarray(
        [row["charged_percent_by_subject"] for row in expanded.values()],
        dtype=float,
    )
    capacities = np.asarray(
        [row["capacity_hat_usd"] for row in expanded.values()],
        dtype=float,
    )
    result = {
        "source": "anonymized read-only replay of a copied SQLite snapshot",
        "account_id_redacted": True,
        "segments": len(segments),
        "observations": len(observations),
        "variants": variant_rows,
        "expanded_p480_seed_sensitivity": {
            "capacity_range_usd": float(capacities.max() - capacities.min()),
            "max_subject_charged_range_pp": float(np.ptp(charged, axis=0).max()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result["expanded_p480_seed_sensitivity"], ensure_ascii=False))


if __name__ == "__main__":
    main()
