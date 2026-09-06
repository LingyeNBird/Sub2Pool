"""Targeted upstream backfill for all corrections, including legacy FAST-only intervals.

The historic module/function/route names remain compatibility aliases. Existing
primary captures are validated and read, never replaced by a new upstream query.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q

from ..billing_correction.observations import interval_corrections
from ..billing_correction.rules import corrections_enabled
from ..accounting.boundaries import official_start, same_official_reset
from ..accounting.replay import rebuild_observation_suffix
from ..history_state import LeaseGuard, fenced_fact_write
from ..integrations.sub2api import Sub2APIClient
from ..models import AppSettings, Observation, ObservationBillingCapture
from .persistence import apply_fast_interval
from .service import fetch_fast_interval


def _has_capture(observation: Observation) -> bool:
    # Query the authoritative one-to-one table, not the old FAST totals or a
    # possibly cached missing relation on a previously read Observation object.
    return ObservationBillingCapture.objects.filter(observation_id=observation.pk).exists()


def _interval_start(observation: Observation):
    # A legacy subtotal may cover a saved interval whose previous raw sample
    # is no longer present. Backfill that exact interval, not a larger window.
    if observation.fast_correction_started_at is not None:
        if observation.fast_correction_started_at > observation.observed_at:
            raise ValueError("已保存的修正区间起点晚于观测时间")
        return observation.fast_correction_started_at
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


def _result(observation: Observation, config: AppSettings) -> dict:
    observation.refresh_from_db()
    interval = interval_corrections(observation, config)
    return {
        "observation_id": observation.id,
        "fast_correction_usd": float(interval.amounts.fast),
        "fast_correction_calculated": interval.calculated,
        **interval.payload(),
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
    if _has_capture(locked):
        return _result(locked, config)

    if (
        locked.fast_correction_request_count is not None
        and interval.request_count != locked.fast_correction_request_count
    ):
        raise ValueError("上游请求数量与此区间已保存的数量不一致，可能已清理日志；未覆盖原数据")

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
    return _result(locked, config)


def calculate_missing_fast_correction(
    observation: Observation,
    config: AppSettings | None = None,
) -> dict[str, int | float | bool]:
    """Fetch one missing/legacy raw interval, then locally replay its suffix."""

    config = config or AppSettings.load()
    if observation.account_id < 0:
        raise ValueError("CPA 请求不使用 Sub2API 修正补算")
    if not corrections_enabled(config):
        raise ValueError("修正当前未启用")
    if _has_capture(observation):
        return _result(observation, config)

    with fenced_fact_write(
        [observation.account_id],
        ttl=timedelta(minutes=30),
    ) as guards:
        current = Observation.objects.get(pk=observation.pk)
        if _has_capture(current):
            return _result(current, config)
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
