"""Normalize cumulative Sub2API snapshots through explicit usage intervals."""

from __future__ import annotations

from decimal import Decimal

from .boundaries import same_official_reset
from ..models import Observation, Sub2APIUserUsageSample

ZERO = Decimal("0")


def _same_known_window(left, right) -> bool:
    return (
        left is not None
        and right is not None
        and left == right
    )


def normalize_observation_costs(
    account_id: int,
    observations: list[Observation] | None = None,
) -> list[Observation]:
    """Rebuild one comparable cumulative-cost coordinate per official window.

    Raw totals remain untouched. Explicit interval facts take precedence; equal
    query windows may safely fall back to subtracting cumulative snapshots.
    Legacy rows whose query window is unknown retain their original absolute
    total until the history-repair operation supplies interval facts.
    """

    rows = observations or list(
        Observation.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    previous: Observation | None = None
    changed: list[Observation] = []
    for row in rows:
        same_window_epoch = bool(
            previous is not None
            and same_official_reset(
                previous.upstream_resets_at,
                row.upstream_resets_at,
            )
        )
        interval_standard = row.interval_standard_cost
        interval_actual = row.interval_actual_cost

        if same_window_epoch and previous is not None:
            if interval_standard is None or interval_actual is None:
                if _same_known_window(
                    previous.cost_window_started_at,
                    row.cost_window_started_at,
                ):
                    interval_standard = (
                        row.total_standard_cost - previous.total_standard_cost
                    )
                    interval_actual = (
                        row.total_actual_cost - previous.total_actual_cost
                    )
            if interval_standard is not None and interval_actual is not None:
                previous_standard = previous.normalized_standard_cost
                previous_actual = previous.normalized_actual_cost
                if previous_standard is None:
                    previous_standard = previous.total_standard_cost
                if previous_actual is None:
                    previous_actual = previous.total_actual_cost
                normalized_standard = max(
                    ZERO,
                    previous_standard + interval_standard,
                )
                normalized_actual = max(
                    ZERO,
                    previous_actual + interval_actual,
                )
            else:
                normalized_standard = row.total_standard_cost
                normalized_actual = row.total_actual_cost
        else:
            normalized_standard = row.total_standard_cost
            normalized_actual = row.total_actual_cost

        if (
            row.normalized_standard_cost != normalized_standard
            or row.normalized_actual_cost != normalized_actual
        ):
            row.normalized_standard_cost = normalized_standard
            row.normalized_actual_cost = normalized_actual
            changed.append(row)
        previous = row

    if changed:
        Observation.objects.bulk_update(
            changed,
            ["normalized_standard_cost", "normalized_actual_cost"],
        )
    return rows


def normalize_user_costs(account_id: int) -> None:
    """Rebuild comparable cumulative coordinates for every Sub2API user."""

    rows = list(
        Sub2APIUserUsageSample.objects.filter(account_id=account_id).order_by(
            "sub2api_user_id",
            "observed_at",
            "id",
        )
    )
    previous_by_user: dict[int, Sub2APIUserUsageSample] = {}
    changed: list[Sub2APIUserUsageSample] = []
    for row in rows:
        previous = previous_by_user.get(row.sub2api_user_id)
        same_window_epoch = bool(
            previous is not None
            and same_official_reset(
                previous.window_resets_at,
                row.window_resets_at,
            )
        )
        interval_standard = row.interval_standard_cost
        interval_actual = row.interval_actual_cost

        if same_window_epoch and previous is not None:
            if interval_standard is None or interval_actual is None:
                if _same_known_window(
                    previous.window_started_at,
                    row.window_started_at,
                ):
                    interval_standard = (
                        row.total_standard_cost - previous.total_standard_cost
                    )
                    interval_actual = (
                        row.total_actual_cost - previous.total_actual_cost
                    )
            if interval_standard is not None and interval_actual is not None:
                previous_standard = previous.normalized_standard_cost
                previous_actual = previous.normalized_actual_cost
                if previous_standard is None:
                    previous_standard = previous.total_standard_cost
                if previous_actual is None:
                    previous_actual = previous.total_actual_cost
                normalized_standard = max(
                    ZERO,
                    previous_standard + interval_standard,
                )
                normalized_actual = max(
                    ZERO,
                    previous_actual + interval_actual,
                )
            else:
                normalized_standard = row.total_standard_cost
                normalized_actual = row.total_actual_cost
        else:
            normalized_standard = row.total_standard_cost
            normalized_actual = row.total_actual_cost

        if (
            row.normalized_standard_cost != normalized_standard
            or row.normalized_actual_cost != normalized_actual
        ):
            row.normalized_standard_cost = normalized_standard
            row.normalized_actual_cost = normalized_actual
            changed.append(row)
        previous_by_user[row.sub2api_user_id] = row

    if changed:
        Sub2APIUserUsageSample.objects.bulk_update(
            changed,
            ["normalized_standard_cost", "normalized_actual_cost"],
            batch_size=500,
        )


def normalize_cost_history(
    account_id: int,
    observations: list[Observation] | None = None,
) -> list[Observation]:
    rows = normalize_observation_costs(account_id, observations)
    normalize_user_costs(account_id)
    return rows
