"""FAST correction completeness status derived from saved observations."""
from datetime import datetime, timedelta

from django.db.models import Q

from ..models import AppSettings, Observation


def current_cycle_start(config: AppSettings) -> datetime | None:
    if not config.openai_account_id:
        return None
    latest = (
        Observation.objects.filter(
            account_id=config.openai_account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is not None:
        return latest.attribution_started_at
    latest_raw = (
        Observation.objects.filter(account_id=config.openai_account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest_raw is None:
        return None
    return latest_raw.upstream_resets_at - timedelta(
        seconds=latest_raw.window_seconds
    )


def missing_current_cycle_intervals(config: AppSettings) -> int:
    start = current_cycle_start(config)
    if start is None or not config.openai_account_id:
        return 0
    return (
        Observation.objects.filter(
            account_id=config.openai_account_id,
            observed_at__gte=start,
        )
        .filter(
            Q(fast_correction_standard_cost__isnull=True)
            | Q(fast_correction_actual_cost__isnull=True)
        )
        .count()
    )
