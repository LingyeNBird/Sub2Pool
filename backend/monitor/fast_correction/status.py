"""FAST correction completeness status derived from saved observations."""
from datetime import datetime, timedelta

from django.db.models import Q

from ..models import AppSettings, MonitoredAccount, Observation


def current_cycle_start(account: MonitoredAccount) -> datetime | None:
    latest = (
        Observation.objects.filter(
            account_id=account.external_account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is not None:
        return latest.attribution_started_at
    latest_raw = (
        Observation.objects.filter(account_id=account.external_account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest_raw is None:
        return None
    return latest_raw.upstream_resets_at - timedelta(
        seconds=latest_raw.window_seconds
    )


def _missing_for_account(account: MonitoredAccount) -> int:
    start = current_cycle_start(account)
    if start is None:
        return 0
    return (
        Observation.objects.filter(
            account_id=account.external_account_id,
            observed_at__gte=start,
        )
        .filter(
            Q(fast_correction_standard_cost__isnull=True)
            | Q(fast_correction_actual_cost__isnull=True)
        )
        .count()
    )


def missing_current_cycle_intervals(
    _config: AppSettings,
    account: MonitoredAccount | None = None,
) -> int:
    """Return one account's missing intervals, or the enabled-account total."""
    if account is not None:
        return _missing_for_account(account)
    return sum(
        _missing_for_account(item)
        for item in MonitoredAccount.objects.filter(enabled=True)
    )
