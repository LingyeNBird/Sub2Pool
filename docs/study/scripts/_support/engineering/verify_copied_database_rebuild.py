#!/usr/bin/env python3
"""在 SQLite 副本上执行生产重放并输出匿名验收摘要。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    os.environ.setdefault("DJANGO_DEBUG", "true")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pinche.settings")
    os.environ["PINCH_DATA_DIR"] = str(args.data_dir.resolve())
    sys.path.insert(0, str(args.backend.resolve()))

    import django

    from django.db.models import F
    django.setup()

    from monitor.accounting.replay import rebuild_account
    from monitor.models import AppSettings, Observation, ParticipantSnapshot

    config = AppSettings.load()
    result = rebuild_account(int(config.openai_account_id), config)
    observations = list(
        Observation.objects.filter(
            account_id=config.openai_account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        ).order_by("observed_at", "id")
    )
    latest = observations[-1]
    latest_snapshots = list(
        ParticipantSnapshot.objects.filter(observation=latest).order_by("id")
    )
    invalid_snapshot_intervals = ParticipantSnapshot.objects.filter(
        observation__account_id=config.openai_account_id,
        charged_percent_lower__gt=F("charged_cycle_percent"),
    ).count() + ParticipantSnapshot.objects.filter(
        observation__account_id=config.openai_account_id,
        charged_percent_upper__lt=F("charged_cycle_percent"),
    ).count()
    incompatible_observations = 0
    projected_observations = 0
    for observation in observations:
        diagnostics = observation.model_diagnostics
        lower, upper = diagnostics.get("progress_deterministic_bounds", [0.0, 100.0])
        estimated = float(observation.estimated_used_percent)
        if not (float(lower) - 1e-6 <= estimated <= float(upper) + 1e-6):
            incompatible_observations += 1
        if diagnostics.get("attribution_projection_applied"):
            projected_observations += 1

    payload = {
        "source": "production replay against copied SQLite; no network calls",
        "replay_result": result.as_dict(),
        "algorithm": latest.model_diagnostics.get("algorithm"),
        "observations": len(observations),
        "incompatible_observations": incompatible_observations,
        "projected_observations": projected_observations,
        "invalid_snapshot_intervals": invalid_snapshot_intervals,
        "latest": {
            "displayed_percent": float(latest.upstream_used_percent),
            "interval_used_percent": float(latest.interval_used_percent),
            "estimated_used_percent": float(latest.estimated_used_percent),
            "capacity_usd": float(latest.effective_usd_per_percent * 100),
            "capacity_interval_usd": [
                float(latest.capacity_lower_usd),
                float(latest.capacity_upper_usd),
            ],
            "charged_percent_by_subject": [
                float(item.charged_cycle_percent)
                for item in latest_snapshots
            ],
            "recommended_balance_by_subject": [
                float(item.recommended_balance_usd)
                for item in latest_snapshots
            ],
            "diagnostics": {
                key: latest.model_diagnostics.get(key)
                for key in (
                    "capacity_range_usd",
                    "particles",
                    "balance_interval_inflation",
                    "progress_probability_interval",
                    "progress_deterministic_bounds",
                    "attribution_projection_applied",
                    "projection_max_adjustment_pp",
                    "deterministic_repairs",
                )
            },
        },
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
