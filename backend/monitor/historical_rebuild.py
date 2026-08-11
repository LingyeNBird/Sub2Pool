"""Reacquire replayable history from passive Sub2API request logs.

The upstream integer percentage and its observation boundary are irreplaceable
historical evidence. Usage costs, per-user splits, FAST corrections and every
model result are recoverable and are therefore rebuilt from request logs rather
than compared with legacy snapshots.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .accounting.boundaries import official_start, same_official_reset
from .fast_correction.domain import FastCorrectionInterval, aggregate_fast_logs
from .fast_correction.persistence import detail_rows
from .integrations.sub2api import Sub2APIClient, Sub2APIUsageLog
from .models import (
    AppSettings,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantAPIUsageSnapshot,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from .replay import rebuild_account

ZERO = Decimal("0")
DEFAULT_WINDOW_SECONDS = 7 * 24 * 60 * 60
HISTORICAL_REBUILD_LEASE = timedelta(hours=1)
PRESERVED_RAW_WINDOW_FIELDS = {
    "slot",
    "window_seconds",
    "reset_after_seconds",
    "reset_at",
    "query_mode",
    "sampled_at",
}


class HistoricalRebuildError(ValueError):
    """Historical request facts could not be rebuilt safely."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class _CostPrefix:
    logs: tuple[Sub2APIUsageLog, ...]
    times: tuple[datetime, ...]
    standard: tuple[Decimal, ...]
    actual: tuple[Decimal, ...]

    @classmethod
    def from_logs(cls, logs: list[Sub2APIUsageLog]) -> "_CostPrefix":
        ordered = tuple(sorted(logs, key=lambda row: (row.created_at, row.id)))
        standard = [ZERO]
        actual = [ZERO]
        for row in ordered:
            standard.append(standard[-1] + row.total_cost)
            actual.append(actual[-1] + row.actual_cost)
        return cls(
            logs=ordered,
            times=tuple(row.created_at for row in ordered),
            standard=tuple(standard),
            actual=tuple(actual),
        )

    def between(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[Decimal, Decimal]:
        start = bisect_left(self.times, started_at)
        end = bisect_left(self.times, ended_at)
        return (
            self.standard[end] - self.standard[start],
            self.actual[end] - self.actual[start],
        )

    def logs_between(
        self,
        started_at: datetime,
        ended_at: datetime,
    ) -> list[Sub2APIUsageLog]:
        start = bisect_left(self.times, started_at)
        end = bisect_left(self.times, ended_at)
        return list(self.logs[start:end])


@dataclass(frozen=True)
class _HistoryPoint:
    observed_at: datetime
    window_resets_at: datetime
    window_seconds: int

    @property
    def window_started_at(self) -> datetime:
        return self.window_resets_at - timedelta(seconds=self.window_seconds)


@dataclass(frozen=True)
class _ObservationFacts:
    window_started_at: datetime
    total_standard_cost: Decimal
    total_actual_cost: Decimal
    interval_started_at: datetime
    interval_standard_cost: Decimal
    interval_actual_cost: Decimal
    fast_interval: FastCorrectionInterval


@dataclass
class HistoricalRebuildPlan:
    account_id: int
    observations: list[Observation]
    points: list[_HistoryPoint]
    logs: list[Sub2APIUsageLog]
    users: dict[int, tuple[str, str]]
    observation_facts: dict[int, _ObservationFacts]
    user_rows: list[Sub2APIUserUsageSample]
    config_fingerprint: tuple[object, ...]
    participant_fingerprint: tuple[tuple[object, ...], ...]
    sample_fingerprint: tuple[
        int,
        int,
        datetime | None,
        int,
        int,
        datetime | None,
    ]
    existing_user_samples: int
    existing_participant_samples: int
    participant_snapshot_count: int
    participant_sample_count: int
    preserved_balance_facts: int
    invalidated_api_usage_snapshots: int
    nonzero_percent_without_cost: int

    @property
    def can_rebuild(self) -> bool:
        return bool(self.observations)

    def public_data(self) -> dict[str, int | bool | str | None]:
        return {
            "account_id": self.account_id,
            "observation_count": len(self.observations),
            "sample_point_count": len(self.points),
            "request_log_count": len(self.logs),
            "user_count": len(self.users),
            "existing_user_samples": self.existing_user_samples,
            "rebuilt_user_samples": len(self.user_rows),
            "participant_snapshot_count": self.participant_snapshot_count,
            "existing_participant_samples": self.existing_participant_samples,
            "rebuilt_participant_samples": self.participant_sample_count,
            "fast_interval_count": len(self.observation_facts),
            "preserved_balance_facts": self.preserved_balance_facts,
            "invalidated_api_usage_snapshots": (
                self.invalidated_api_usage_snapshots
            ),
            "nonzero_percent_without_cost": self.nonzero_percent_without_cost,
            "earliest_log_at": (
                self.logs[0].created_at.isoformat() if self.logs else None
            ),
            "latest_log_at": (
                self.logs[-1].created_at.isoformat() if self.logs else None
            ),
            "can_rebuild": self.can_rebuild,
        }


def _closest_observation(
    observations: list[Observation],
    observed_at: datetime,
    reset_at: datetime | None = None,
) -> Observation:
    candidates = observations
    if reset_at is not None:
        same_reset = [
            row
            for row in observations
            if same_official_reset(row.upstream_resets_at, reset_at)
        ]
        if same_reset:
            candidates = same_reset
    return min(
        candidates,
        key=lambda row: (
            abs((row.observed_at - observed_at).total_seconds()),
            row.id,
        ),
    )


def _history_points(
    observations: list[Observation],
    user_samples: list[Sub2APIUserUsageSample],
    participant_samples: list[ParticipantUsageSample],
) -> list[_HistoryPoint]:
    observations_by_time: dict[datetime, Observation] = {}
    for row in observations:
        observations_by_time.setdefault(row.observed_at, row)

    samples_by_time: dict[datetime, Sub2APIUserUsageSample] = {}
    for row in user_samples:
        samples_by_time.setdefault(row.observed_at, row)

    all_times = set(observations_by_time) | set(samples_by_time)
    all_times.update(row.observed_at for row in participant_samples)
    points: list[_HistoryPoint] = []
    for observed_at in sorted(all_times):
        observation = observations_by_time.get(observed_at)
        if observation is not None:
            points.append(
                _HistoryPoint(
                    observed_at=observed_at,
                    window_resets_at=observation.upstream_resets_at,
                    window_seconds=observation.window_seconds,
                )
            )
            continue

        sample = samples_by_time.get(observed_at)
        reset_at = sample.window_resets_at if sample is not None else None
        closest = _closest_observation(observations, observed_at, reset_at)
        points.append(
            _HistoryPoint(
                observed_at=observed_at,
                window_resets_at=reset_at or closest.upstream_resets_at,
                window_seconds=closest.window_seconds or DEFAULT_WINDOW_SECONDS,
            )
        )
    return points


def _user_metadata(
    remote_users: list[dict],
    existing_samples: list[Sub2APIUserUsageSample],
    logs: list[Sub2APIUsageLog],
    participants: list[Participant],
) -> dict[int, tuple[str, str]]:
    metadata: dict[int, tuple[str, str]] = {
        row.sub2api_user_id: (row.username, row.email)
        for row in existing_samples
    }
    for participant in participants:
        metadata.setdefault(
            participant.sub2api_user_id,
            (participant.sub2api_username, participant.sub2api_email),
        )
    for row in logs:
        metadata.setdefault(row.user_id, ("", ""))

    for row in remote_users:
        try:
            user_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if user_id not in metadata:
            continue
        metadata[user_id] = (
            str(row.get("username") or ""),
            str(row.get("email") or ""),
        )
    return dict(sorted(metadata.items()))


def _build_observation_facts(
    observations: list[Observation],
    prefix: _CostPrefix,
) -> tuple[dict[int, _ObservationFacts], int]:
    facts: dict[int, _ObservationFacts] = {}
    previous: Observation | None = None
    nonzero_without_cost = 0
    for observation in observations:
        window_start = official_start(observation)
        total_standard, total_actual = prefix.between(
            window_start,
            observation.observed_at,
        )
        interval_start = window_start
        if previous is not None and same_official_reset(
            previous.upstream_resets_at,
            observation.upstream_resets_at,
        ):
            interval_start = previous.observed_at
        interval_logs = prefix.logs_between(
            interval_start,
            observation.observed_at,
        )
        interval_standard, interval_actual = prefix.between(
            interval_start,
            observation.observed_at,
        )
        facts[observation.id] = _ObservationFacts(
            window_started_at=window_start,
            total_standard_cost=total_standard,
            total_actual_cost=total_actual,
            interval_started_at=interval_start,
            interval_standard_cost=interval_standard,
            interval_actual_cost=interval_actual,
            fast_interval=aggregate_fast_logs(
                interval_logs,
                started_at=interval_start,
                ended_at=observation.observed_at,
            ),
        )
        if (
            observation.upstream_used_percent > ZERO
            and total_standard == ZERO
            and total_actual == ZERO
        ):
            nonzero_without_cost += 1
        previous = observation
    return facts, nonzero_without_cost


def _build_user_rows(
    account_id: int,
    points: list[_HistoryPoint],
    users: dict[int, tuple[str, str]],
    logs: list[Sub2APIUsageLog],
) -> list[Sub2APIUserUsageSample]:
    logs_by_user: dict[int, list[Sub2APIUsageLog]] = defaultdict(list)
    for row in logs:
        logs_by_user[row.user_id].append(row)
    prefixes = {
        user_id: _CostPrefix.from_logs(rows)
        for user_id, rows in logs_by_user.items()
    }
    empty_prefix = _CostPrefix.from_logs([])

    rows: list[Sub2APIUserUsageSample] = []
    previous_point: _HistoryPoint | None = None
    for point in points:
        interval_start = point.window_started_at
        if previous_point is not None and same_official_reset(
            previous_point.window_resets_at,
            point.window_resets_at,
        ):
            interval_start = previous_point.observed_at
        for user_id, (username, email) in users.items():
            prefix = prefixes.get(user_id, empty_prefix)
            total_standard, total_actual = prefix.between(
                point.window_started_at,
                point.observed_at,
            )
            interval_standard, interval_actual = prefix.between(
                interval_start,
                point.observed_at,
            )
            rows.append(
                Sub2APIUserUsageSample(
                    account_id=account_id,
                    sub2api_user_id=user_id,
                    username=username,
                    email=email,
                    observed_at=point.observed_at,
                    window_started_at=point.window_started_at,
                    window_ended_at=point.observed_at,
                    window_resets_at=point.window_resets_at,
                    total_standard_cost=total_standard,
                    total_actual_cost=total_actual,
                    interval_started_at=interval_start,
                    interval_standard_cost=interval_standard,
                    interval_actual_cost=interval_actual,
                    interval_source="historical_logs",
                    normalized_standard_cost=None,
                    normalized_actual_cost=None,
                )
            )
        previous_point = point
    return rows


def inspect_historical_rebuild(config: AppSettings) -> HistoricalRebuildPlan:
    """Read request logs and build a complete replacement plan without writes."""

    if not config.openai_account_id:
        raise HistoricalRebuildError("尚未配置 OpenAI 上游账号")
    account_id = config.openai_account_id
    observations = list(
        Observation.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    existing_user_samples = list(
        Sub2APIUserUsageSample.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "sub2api_user_id",
            "id",
        )
    )
    existing_participant_samples = list(
        ParticipantUsageSample.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "participant_id",
            "id",
        )
    )
    participants = list(Participant.objects.order_by("id"))
    if not observations:
        return HistoricalRebuildPlan(
            account_id=account_id,
            observations=[],
            points=[],
            logs=[],
            users={},
            observation_facts={},
            user_rows=[],
            config_fingerprint=_config_fingerprint(config),
            participant_fingerprint=_participant_fingerprint(participants),
            sample_fingerprint=_sample_fingerprint(
                existing_user_samples,
                existing_participant_samples,
            ),
            existing_user_samples=len(existing_user_samples),
            existing_participant_samples=len(existing_participant_samples),
            participant_snapshot_count=0,
            participant_sample_count=0,
            preserved_balance_facts=0,
            invalidated_api_usage_snapshots=ParticipantAPIUsageSnapshot.objects.filter(
                account_id=account_id
            ).count(),
            nonzero_percent_without_cost=0,
        )

    points = _history_points(
        observations,
        existing_user_samples,
        existing_participant_samples,
    )
    earliest_start = min(point.window_started_at for point in points)
    latest_end = max(point.observed_at for point in points)
    with Sub2APIClient(config) as client:
        logs = client.usage_logs(
            account_id=account_id,
            started_at=earliest_start,
            ended_at=latest_end,
            timezone_name=config.timezone,
        )
        remote_users = client.list_users()

    users = _user_metadata(
        remote_users,
        existing_user_samples,
        logs,
        participants,
    )
    prefix = _CostPrefix.from_logs(logs)
    observation_facts, nonzero_without_cost = _build_observation_facts(
        observations,
        prefix,
    )
    user_rows = _build_user_rows(account_id, points, users, logs)

    existing_snapshot_members = defaultdict(set)
    for observation_id, participant_id in ParticipantSnapshot.objects.filter(
        observation__account_id=account_id
    ).values_list("observation_id", "participant_id"):
        existing_snapshot_members[observation_id].add(participant_id)
    enabled_participant_ids = {
        participant.id for participant in participants if participant.enabled
    }
    participant_snapshot_count = sum(
        len(existing_snapshot_members[row.id] | enabled_participant_ids)
        for row in observations
    )
    historical_participant_ids = {
        row.participant_id for row in existing_participant_samples
    } | enabled_participant_ids
    participant_sample_count = len(historical_participant_ids) * len(points)
    preserved_balance_facts = sum(
        row.balance_usd is not None for row in existing_participant_samples
    ) + ParticipantSnapshot.objects.filter(
        observation__account_id=account_id,
        current_balance_usd__isnull=False,
    ).count()

    return HistoricalRebuildPlan(
        account_id=account_id,
        observations=observations,
        points=points,
        logs=logs,
        users=users,
        observation_facts=observation_facts,
        user_rows=user_rows,
        config_fingerprint=_config_fingerprint(config),
        participant_fingerprint=_participant_fingerprint(participants),
        sample_fingerprint=_sample_fingerprint(
            existing_user_samples,
            existing_participant_samples,
        ),
        existing_user_samples=len(existing_user_samples),
        existing_participant_samples=len(existing_participant_samples),
        participant_snapshot_count=participant_snapshot_count,
        participant_sample_count=participant_sample_count,
        preserved_balance_facts=preserved_balance_facts,
        invalidated_api_usage_snapshots=ParticipantAPIUsageSnapshot.objects.filter(
            account_id=account_id
        ).count(),
        nonzero_percent_without_cost=nonzero_without_cost,
    )


def _config_fingerprint(config: AppSettings) -> tuple[object, ...]:
    """Stable settings that define the fetched facts or rebuilt derivations."""

    return (
        config.sub2api_base_url,
        config.sub2api_admin_token_encrypted,
        config.openai_account_id,
        config.request_timeout_seconds,
        config.verify_tls,
        config.timezone,
        config.cost_basis,
        config.weekly_quota_model,
        config.fast_correction_enabled,
        config.initial_usd_per_percent,
        config.safety_factor,
        config.daily_estimate_min_percent_span,
        config.limit_warning_usd,
        config.recommendation_change_usd,
    )


def _participant_fingerprint(
    participants: list[Participant],
) -> tuple[tuple[object, ...], ...]:
    """Membership facts used to map request logs into participant ledgers."""

    return tuple(
        (
            row.id,
            row.sub2api_user_id,
            row.sub2api_username,
            row.sub2api_email,
            row.share_percent,
            row.enabled,
        )
        for row in participants
    )


def _observation_fingerprint(
    observations: list[Observation],
) -> tuple[tuple[object, ...], ...]:
    """Irreplaceable upstream and administrator-authored observation facts."""

    return tuple(
        (
            row.id,
            row.observed_at,
            row.upstream_resets_at,
            row.window_seconds,
            row.upstream_used_percent,
            row.is_manual_start,
            row.manual_start_set_at,
            row.excluded_at,
            row.exclusion_source,
        )
        for row in observations
    )


def _sample_fingerprint(
    user_samples: list[Sub2APIUserUsageSample],
    participant_samples: list[ParticipantUsageSample],
) -> tuple[int, int, datetime | None, int, int, datetime | None]:
    return (
        len(user_samples),
        max((row.id for row in user_samples), default=0),
        max((row.observed_at for row in user_samples), default=None),
        len(participant_samples),
        max((row.id for row in participant_samples), default=0),
        max((row.observed_at for row in participant_samples), default=None),
    )


def _replace_observations(
    plan: HistoricalRebuildPlan,
    config: AppSettings,
) -> None:
    fast_rows: list[ObservationFastCorrection] = []
    for observation in plan.observations:
        facts = plan.observation_facts[observation.id]
        observation.total_standard_cost = facts.total_standard_cost
        observation.total_actual_cost = facts.total_actual_cost
        observation.raw_selected_total_cost = (
            facts.total_actual_cost
            if config.cost_basis == "actual"
            else facts.total_standard_cost
        )
        observation.selected_total_cost = observation.raw_selected_total_cost
        observation.cost_window_started_at = facts.window_started_at
        observation.cost_window_ended_at = observation.observed_at
        observation.interval_cost_started_at = facts.interval_started_at
        observation.interval_standard_cost = facts.interval_standard_cost
        observation.interval_actual_cost = facts.interval_actual_cost
        observation.interval_cost_source = "historical_logs"
        observation.normalized_standard_cost = None
        observation.normalized_actual_cost = None
        observation.fast_correction_started_at = facts.interval_started_at
        observation.fast_correction_request_count = (
            facts.fast_interval.request_count
        )
        observation.fast_correction_standard_cost = (
            facts.fast_interval.standard_correction_cost
        )
        observation.fast_correction_actual_cost = (
            facts.fast_interval.actual_correction_cost
        )
        observation.attribution_started_at = None
        observation.interval_used_percent = ZERO
        observation.delta_percent = None
        observation.delta_cost = None
        observation.sample_usd_per_percent = None
        observation.effective_usd_per_percent = (
            config.initial_usd_per_percent
        )
        observation.estimated_used_percent = ZERO
        observation.capacity_lower_usd = None
        observation.capacity_upper_usd = None
        observation.model_diagnostics = {}
        observation.valid_sample = False
        observation.sample_note = "等待历史重建派生"
        raw_window = {
            key: value
            for key, value in observation.raw_window.items()
            if key in PRESERVED_RAW_WINDOW_FIELDS
        }
        raw_window.update(
            {
                "cost_window_started_at": facts.window_started_at.isoformat(),
                "cost_window_ended_at": observation.observed_at.isoformat(),
                "interval_cost_source": "historical_logs",
                "history_rebuild_source": "sub2api_request_logs",
            }
        )
        observation.raw_window = raw_window
        fast_rows.extend(detail_rows(observation, facts.fast_interval))

    Observation.objects.bulk_update(
        plan.observations,
        [
            "total_standard_cost",
            "total_actual_cost",
            "raw_selected_total_cost",
            "selected_total_cost",
            "cost_window_started_at",
            "cost_window_ended_at",
            "interval_cost_started_at",
            "interval_standard_cost",
            "interval_actual_cost",
            "interval_cost_source",
            "normalized_standard_cost",
            "normalized_actual_cost",
            "fast_correction_started_at",
            "fast_correction_request_count",
            "fast_correction_standard_cost",
            "fast_correction_actual_cost",
            "attribution_started_at",
            "interval_used_percent",
            "delta_percent",
            "delta_cost",
            "sample_usd_per_percent",
            "effective_usd_per_percent",
            "estimated_used_percent",
            "capacity_lower_usd",
            "capacity_upper_usd",
            "model_diagnostics",
            "valid_sample",
            "sample_note",
            "raw_window",
        ],
        batch_size=500,
    )
    ObservationFastCorrection.objects.filter(
        observation__account_id=plan.account_id
    ).delete()
    if fast_rows:
        ObservationFastCorrection.objects.bulk_create(
            fast_rows,
            batch_size=500,
        )


def _replace_participant_facts(
    plan: HistoricalRebuildPlan,
    config: AppSettings,
) -> None:
    participants = {
        row.id: row for row in Participant.objects.order_by("id")
    }
    raw_by_time_user = {
        (row.observed_at, row.sub2api_user_id): row.selected_cost(
            config.cost_basis
        )
        for row in plan.user_rows
    }

    old_snapshots = list(
        ParticipantSnapshot.objects.filter(
            observation__account_id=plan.account_id
        ).order_by("observation_id", "participant_id")
    )
    snapshot_state = {
        (row.observation_id, row.participant_id): (
            row.current_balance_usd,
            row.recommendation_applied,
            row.recommended_balance_usd,
        )
        for row in old_snapshots
    }
    members_by_observation: dict[int, set[int]] = defaultdict(set)
    for row in old_snapshots:
        members_by_observation[row.observation_id].add(row.participant_id)
    enabled_ids = {
        participant.id
        for participant in participants.values()
        if participant.enabled
    }

    ParticipantSnapshot.objects.filter(
        observation__account_id=plan.account_id
    ).delete()
    rebuilt_snapshots: list[ParticipantSnapshot] = []
    for observation in plan.observations:
        member_ids = members_by_observation[observation.id] | enabled_ids
        for participant_id in sorted(member_ids):
            participant = participants.get(participant_id)
            if participant is None:
                continue
            raw_cost = raw_by_time_user.get(
                (observation.observed_at, participant.sub2api_user_id),
                ZERO,
            )
            balance, applied, previous_recommendation = snapshot_state.get(
                (observation.id, participant_id),
                (None, False, None),
            )
            if (
                balance is None
                and participant.last_checked_at == observation.observed_at
            ):
                balance = participant.latest_balance_usd
            rebuilt_snapshots.append(
                ParticipantSnapshot(
                    observation=observation,
                    participant=participant,
                    raw_selected_cost=raw_cost,
                    selected_cost=raw_cost,
                    current_balance_usd=balance,
                    remaining_share_percent=participant.share_percent,
                    recommended_balance_usd=previous_recommendation,
                    recommendation_applied=applied,
                )
            )
    if rebuilt_snapshots:
        ParticipantSnapshot.objects.bulk_create(
            rebuilt_snapshots,
            batch_size=500,
        )

    old_usage = list(
        ParticipantUsageSample.objects.filter(account_id=plan.account_id)
    )
    balances = {
        (row.participant_id, row.observed_at): row.balance_usd
        for row in old_usage
    }
    historical_ids = {row.participant_id for row in old_usage} | enabled_ids
    ParticipantUsageSample.objects.filter(account_id=plan.account_id).delete()
    rebuilt_usage: list[ParticipantUsageSample] = []
    for participant_id in sorted(historical_ids):
        participant = participants.get(participant_id)
        if participant is None:
            continue
        for point in plan.points:
            raw_cost = raw_by_time_user.get(
                (point.observed_at, participant.sub2api_user_id),
                ZERO,
            )
            balance = balances.get((participant_id, point.observed_at))
            if (
                balance is None
                and participant.last_checked_at == point.observed_at
            ):
                balance = participant.latest_balance_usd
            rebuilt_usage.append(
                ParticipantUsageSample(
                    participant=participant,
                    account_id=plan.account_id,
                    attribution_started_at=None,
                    observed_at=point.observed_at,
                    balance_usd=balance,
                    selected_cost=raw_cost,
                    raw_selected_cost=raw_cost,
                )
            )
    if rebuilt_usage:
        ParticipantUsageSample.objects.bulk_create(
            rebuilt_usage,
            batch_size=500,
        )


def _restore_participant_latest_facts(
    account_id: int,
    preserved_balances: dict[int, tuple[Decimal | None, datetime | None]],
) -> None:
    """Keep captured balances while selecting rebuilt usage for the same time."""

    participants = list(Participant.objects.order_by("id"))
    samples = list(
        ParticipantUsageSample.objects.filter(account_id=account_id).order_by(
            "participant_id",
            "observed_at",
            "id",
        )
    )
    sample_by_key = {
        (row.participant_id, row.observed_at): row for row in samples
    }
    latest_by_participant = {
        row.participant_id: row for row in samples
    }
    changed: list[Participant] = []
    for participant in participants:
        balance, checked_at = preserved_balances.get(
            participant.id,
            (participant.latest_balance_usd, participant.last_checked_at),
        )
        selected_sample = (
            sample_by_key.get((participant.id, checked_at))
            if checked_at is not None
            else None
        )
        if selected_sample is None:
            selected_sample = latest_by_participant.get(participant.id)
        participant.latest_balance_usd = balance
        participant.last_checked_at = checked_at
        if selected_sample is not None:
            participant.latest_selected_cost = selected_sample.selected_cost
        changed.append(participant)
    if changed:
        Participant.objects.bulk_update(
            changed,
            [
                "latest_balance_usd",
                "latest_selected_cost",
                "last_checked_at",
            ],
        )


def _rebuild_historical_data_locked(config: AppSettings) -> dict:
    """Replace every recoverable history fact, then replay all derivations."""

    plan = inspect_historical_rebuild(config)
    preview = plan.public_data()
    if not plan.can_rebuild:
        raise HistoricalRebuildError("尚无可重建的原始百分比观测", preview)

    expected = _observation_fingerprint(plan.observations)
    expected_config = plan.config_fingerprint
    with transaction.atomic():
        locked_config = AppSettings.objects.select_for_update().get(pk=config.pk)
        if _config_fingerprint(locked_config) != expected_config:
            raise HistoricalRebuildError(
                "重建期间系统设置发生变化，请重新检查后再试",
                preview,
            )
        config = locked_config
        locked = list(
            Observation.objects.select_for_update()
            .filter(account_id=plan.account_id)
            .order_by("observed_at", "id")
        )
        if _observation_fingerprint(locked) != expected:
            raise HistoricalRebuildError(
                "重建期间观测记录发生变化，请重新检查后再试",
                preview,
            )
        locked_user_samples = list(
            Sub2APIUserUsageSample.objects.select_for_update().filter(
                account_id=plan.account_id
            )
        )
        locked_participant_samples = list(
            ParticipantUsageSample.objects.select_for_update().filter(
                account_id=plan.account_id
            )
        )
        if (
            _sample_fingerprint(
                locked_user_samples,
                locked_participant_samples,
            )
            != plan.sample_fingerprint
        ):
            raise HistoricalRebuildError(
                "重建期间本地用量记录发生变化，请重新检查后再试",
                preview,
            )
        locked_participants = list(
            Participant.objects.select_for_update().order_by("id")
        )
        if (
            _participant_fingerprint(locked_participants)
            != plan.participant_fingerprint
        ):
            raise HistoricalRebuildError(
                "重建期间参与者配置发生变化，请重新检查后再试",
                preview,
            )
        plan.observations = locked
        preserved_balances = {
            row.id: (row.latest_balance_usd, row.last_checked_at)
            for row in locked_participants
        }
        _replace_observations(plan, config)

        Sub2APIUserUsageSample.objects.filter(
            account_id=plan.account_id
        ).delete()
        if plan.user_rows:
            Sub2APIUserUsageSample.objects.bulk_create(
                plan.user_rows,
                batch_size=500,
            )
        _replace_participant_facts(plan, config)
        ParticipantAPIUsageSnapshot.objects.filter(
            account_id=plan.account_id
        ).delete()
        replay = rebuild_account(plan.account_id, config)
        _restore_participant_latest_facts(
            plan.account_id,
            preserved_balances,
        )

    return {
        **preview,
        "replayed_observations": replay.rebuilt_observations,
        "inferred_intervals": replay.inferred_intervals,
        "automatic_exclusions": replay.automatic_exclusions,
    }


def rebuild_historical_data(config: AppSettings) -> dict:
    """Serialize destructive rebuilding against sampling and other rebuilds."""

    now = timezone.now()
    lease_until = now + HISTORICAL_REBUILD_LEASE
    acquired = (
        AppSettings.objects.filter(pk=config.pk)
        .filter(Q(run_lease_until__isnull=True) | Q(run_lease_until__lt=now))
        .update(run_lease_until=lease_until)
    )
    if not acquired:
        raise HistoricalRebuildError(
            "已有采集或历史维护任务正在执行，请稍后再试"
        )
    try:
        return _rebuild_historical_data_locked(config)
    finally:
        AppSettings.objects.filter(
            pk=config.pk,
            run_lease_until=lease_until,
        ).update(run_lease_until=None)
