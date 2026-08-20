"""Targeted repair of one missing FAST correction interval."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from ..accounting.boundaries import official_start, same_official_reset
from ..accounting.replay import rebuild_observation_suffix
from ..history_state import LeaseGuard, fenced_fact_write
from ..integrations.sub2api import Sub2APIClient
from ..models import AppSettings, Observation
from .persistence import apply_fast_interval
from .service import fetch_fast_interval


def _calculated(observation: Observation) -> bool:
    return (
        observation.fast_correction_standard_cost is not None
        and observation.fast_correction_actual_cost is not None
    )


def _selected_correction(observation: Observation, cost_basis: str) -> Decimal:
    value = (
        observation.fast_correction_actual_cost
        if cost_basis == "actual"
        else observation.fast_correction_standard_cost
    )
    return value or Decimal("0")


def _interval_start(observation: Observation):
    previous = (
        Observation.objects.filter(account_id=observation.account_id)
        .filter(
            Q(observed_at__lt=observation.observed_at)
            | Q(
                observed_at=observation.observed_at,
                id__lt=observation.id,
            )
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    if previous is not None and same_official_reset(
        previous.upstream_resets_at,
        observation.upstream_resets_at,
    ):
        return previous.observed_at
    return official_start(observation)


def _result(observation: Observation, cost_basis: str) -> dict[str, int | float | bool]:
    return {
        "observation_id": observation.id,
        "fast_correction_usd": float(
            _selected_correction(observation, cost_basis)
        ),
        "fast_correction_calculated": _calculated(observation),
    }


@transaction.atomic
def _persist_interval(
    observation: Observation,
    interval,
    config: AppSettings,
    guard: LeaseGuard,
) -> dict[str, int | float | bool]:
    guard.assert_owned()
    locked = Observation.objects.select_for_update().get(pk=observation.pk)
    if _calculated(locked):
        return _result(locked, config.cost_basis)

    apply_fast_interval(locked, interval)
    locked.save(
        update_fields=[
            "fast_correction_started_at",
            "fast_correction_standard_cost",
            "fast_correction_actual_cost",
            "fast_correction_request_count",
        ]
    )
    rebuild_observation_suffix(locked, config, guard=guard)
    locked.refresh_from_db()
    return _result(locked, config.cost_basis)


def calculate_missing_fast_correction(
    observation: Observation,
    config: AppSettings | None = None,
) -> dict[str, int | float | bool]:
    """Fetch and persist one exact raw-observation interval, then replay its suffix."""

    config = config or AppSettings.load()
    if not config.fast_correction_enabled:
        raise ValueError("FAST 修正当前未启用")
    if _calculated(observation):
        return _result(observation, config.cost_basis)

    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        current = Observation.objects.get(pk=observation.pk)
        if _calculated(current):
            return _result(current, config.cost_basis)
        started_at = min(_interval_start(current), current.observed_at)
        with Sub2APIClient(config) as client:
            interval = fetch_fast_interval(
                client,
                account_id=current.account_id,
                started_at=started_at,
                ended_at=current.observed_at,
                timezone_name=config.timezone,
                correction_rules=config.fast_correction_rules,
            )
        return _persist_interval(
            current,
            interval,
            config,
            guards[current.account_id],
        )
