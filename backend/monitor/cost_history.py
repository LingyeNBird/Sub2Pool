"""Repair historical cost intervals from passive Sub2API request logs."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction

from .accounting.boundaries import official_start, same_official_reset
from .integrations.sub2api import Sub2APIClient, Sub2APIUsageLog
from .models import AppSettings, Observation, Sub2APIUserUsageSample
from .replay import rebuild_account

ZERO = Decimal("0")
MIN_TOLERANCE_USD = Decimal("0.05")
RELATIVE_TOLERANCE = Decimal("0.001")


class CostHistoryRepairError(ValueError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class _Prefix:
    times: tuple[datetime, ...]
    standard: tuple[Decimal, ...]
    actual: tuple[Decimal, ...]

    @classmethod
    def from_logs(cls, logs: list[Sub2APIUsageLog]) -> "_Prefix":
        standard = [ZERO]
        actual = [ZERO]
        for row in logs:
            standard.append(standard[-1] + row.total_cost)
            actual.append(actual[-1] + row.actual_cost)
        return cls(
            tuple(row.created_at for row in logs),
            tuple(standard),
            tuple(actual),
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


@dataclass
class CostHistoryRepairPlan:
    account_id: int
    observations: list[Observation]
    user_samples: list[Sub2APIUserUsageSample]
    logs: list[Sub2APIUsageLog]
    observation_intervals: dict[int, tuple[datetime, Decimal, Decimal, str]]
    user_intervals: dict[int, tuple[datetime, Decimal, Decimal, str]]
    coordinate_changes: int
    snapshot_conflicts: int
    max_standard_gap_usd: Decimal
    max_actual_gap_usd: Decimal

    @property
    def can_repair(self) -> bool:
        return bool(
            self.observations
            and self.logs
            and not self.snapshot_conflicts
        )

    def public_data(self) -> dict[str, int | float | bool | None]:
        return {
            "account_id": self.account_id,
            "observation_count": len(self.observations),
            "user_sample_count": len(self.user_samples),
            "request_log_count": len(self.logs),
            "observation_interval_count": len(self.observation_intervals),
            "user_interval_count": len(self.user_intervals),
            "coordinate_changes": self.coordinate_changes,
            "snapshot_conflicts": self.snapshot_conflicts,
            "max_standard_gap_usd": float(self.max_standard_gap_usd),
            "max_actual_gap_usd": float(self.max_actual_gap_usd),
            "earliest_log_at": (
                self.logs[0].created_at.isoformat() if self.logs else None
            ),
            "latest_log_at": (
                self.logs[-1].created_at.isoformat() if self.logs else None
            ),
            "can_repair": self.can_repair,
        }


def _tolerance(total: Decimal) -> Decimal:
    return max(MIN_TOLERANCE_USD, abs(total) * RELATIVE_TOLERANCE)


def _compare_snapshot_delta(
    previous_standard: Decimal,
    previous_actual: Decimal,
    current_standard: Decimal,
    current_actual: Decimal,
    interval_standard: Decimal,
    interval_actual: Decimal,
) -> tuple[bool, Decimal, Decimal, bool]:
    raw_standard = current_standard - previous_standard
    raw_actual = current_actual - previous_actual
    coordinate_changed = raw_standard < ZERO or raw_actual < ZERO
    if coordinate_changed:
        return False, ZERO, ZERO, True
    standard_gap = abs(raw_standard - interval_standard)
    actual_gap = abs(raw_actual - interval_actual)
    conflict = (
        standard_gap > _tolerance(current_standard)
        or actual_gap > _tolerance(current_actual)
    )
    return conflict, standard_gap, actual_gap, False


def _compare_coordinate_snapshot(
    observation: Observation,
    prefix: _Prefix,
) -> tuple[bool, Decimal, Decimal]:
    """Verify a changed cumulative coordinate against its recorded query window."""

    if observation.cost_window_started_at is None:
        return (
            True,
            abs(observation.total_standard_cost),
            abs(observation.total_actual_cost),
        )
    window_standard, window_actual = prefix.between(
        observation.cost_window_started_at,
        observation.cost_window_ended_at or observation.observed_at,
    )
    standard_gap = abs(observation.total_standard_cost - window_standard)
    actual_gap = abs(observation.total_actual_cost - window_actual)
    conflict = (
        standard_gap > _tolerance(observation.total_standard_cost)
        or actual_gap > _tolerance(observation.total_actual_cost)
    )
    return conflict, standard_gap, actual_gap


def inspect_cost_history(config: AppSettings) -> CostHistoryRepairPlan:
    """Read logs and prepare interval facts without changing the database."""

    if not config.openai_account_id:
        raise CostHistoryRepairError("尚未配置 OpenAI 上游账号")
    account_id = config.openai_account_id
    observations = list(
        Observation.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    user_samples = list(
        Sub2APIUserUsageSample.objects.filter(account_id=account_id).order_by(
            "sub2api_user_id",
            "observed_at",
            "id",
        )
    )
    end_candidates = [row.observed_at for row in observations]
    end_candidates.extend(row.observed_at for row in user_samples)
    if not end_candidates:
        return CostHistoryRepairPlan(
            account_id,
            [],
            [],
            [],
            {},
            {},
            0,
            0,
            ZERO,
            ZERO,
        )

    ended_at = max(end_candidates)
    with Sub2APIClient(config) as client:
        logs = client.usage_logs(
            account_id=account_id,
            started_at=None,
            ended_at=ended_at,
            timezone_name=config.timezone,
        )

    total_prefix = _Prefix.from_logs(logs)
    logs_by_user: dict[int, list[Sub2APIUsageLog]] = defaultdict(list)
    for log in logs:
        logs_by_user[log.user_id].append(log)
    user_prefixes = {
        user_id: _Prefix.from_logs(rows)
        for user_id, rows in logs_by_user.items()
    }

    observation_intervals: dict[
        int,
        tuple[datetime, Decimal, Decimal, str],
    ] = {}
    previous_observation: Observation | None = None
    coordinate_changes = 0
    conflicts = 0
    max_standard_gap = ZERO
    max_actual_gap = ZERO
    for observation in observations:
        same_epoch = bool(
            previous_observation is not None
            and same_official_reset(
                previous_observation.upstream_resets_at,
                observation.upstream_resets_at,
            )
        )
        if not same_epoch or previous_observation is None:
            interval = (
                observation.cost_window_started_at or official_start(observation),
                observation.total_standard_cost,
                observation.total_actual_cost,
                "historical_anchor",
            )
        else:
            standard, actual = total_prefix.between(
                previous_observation.observed_at,
                observation.observed_at,
            )
            interval = (
                previous_observation.observed_at,
                standard,
                actual,
                "historical_logs",
            )
            conflict, standard_gap, actual_gap, changed = (
                _compare_snapshot_delta(
                    previous_observation.total_standard_cost,
                    previous_observation.total_actual_cost,
                    observation.total_standard_cost,
                    observation.total_actual_cost,
                    standard,
                    actual,
                )
            )
            if changed:
                conflict, standard_gap, actual_gap = (
                    _compare_coordinate_snapshot(
                        observation,
                        total_prefix,
                    )
                )
            conflicts += int(conflict)
            coordinate_changes += int(changed)
            max_standard_gap = max(max_standard_gap, standard_gap)
            max_actual_gap = max(max_actual_gap, actual_gap)
        observation_intervals[observation.pk] = interval
        previous_observation = observation

    official_starts_by_time = {
        observation.observed_at: official_start(observation)
        for observation in observations
    }
    user_intervals: dict[int, tuple[datetime, Decimal, Decimal, str]] = {}
    previous_by_user: dict[int, Sub2APIUserUsageSample] = {}
    for sample in user_samples:
        previous = previous_by_user.get(sample.sub2api_user_id)
        same_epoch = bool(
            previous is not None
            and same_official_reset(
                previous.window_resets_at,
                sample.window_resets_at,
            )
        )
        if not same_epoch or previous is None:
            interval = (
                sample.window_started_at
                or official_starts_by_time.get(
                    sample.observed_at,
                    sample.window_resets_at - timedelta(days=7),
                ),
                sample.total_standard_cost,
                sample.total_actual_cost,
                "historical_anchor",
            )
        else:
            prefix = user_prefixes.get(sample.sub2api_user_id)
            standard, actual = (
                prefix.between(previous.observed_at, sample.observed_at)
                if prefix is not None
                else (ZERO, ZERO)
            )
            interval = (
                previous.observed_at,
                standard,
                actual,
                "historical_logs",
            )
        user_intervals[sample.pk] = interval
        previous_by_user[sample.sub2api_user_id] = sample

    return CostHistoryRepairPlan(
        account_id,
        observations,
        user_samples,
        logs,
        observation_intervals,
        user_intervals,
        coordinate_changes,
        conflicts,
        max_standard_gap,
        max_actual_gap,
    )


def repair_cost_history(config: AppSettings) -> dict:
    # 请求日志可能很多，网络读取不能占用数据库事务。先只读形成计划，再在
    # 一个短事务内写入区间事实并重放所有派生结果。
    plan = inspect_cost_history(config)
    preview = plan.public_data()
    if not plan.can_repair:
        message = (
            "请求日志与同一查询窗口的累计快照不一致，已拒绝写入"
            if plan.snapshot_conflicts
            else "没有可用于重建历史成本区间的请求日志"
        )
        raise CostHistoryRepairError(message, preview)

    with transaction.atomic():
        for observation in plan.observations:
            started_at, standard, actual, source = (
                plan.observation_intervals[observation.pk]
            )
            observation.interval_cost_started_at = started_at
            observation.interval_standard_cost = standard
            observation.interval_actual_cost = actual
            observation.interval_cost_source = source
        Observation.objects.bulk_update(
            plan.observations,
            [
                "interval_cost_started_at",
                "interval_standard_cost",
                "interval_actual_cost",
                "interval_cost_source",
            ],
            batch_size=500,
        )

        for sample in plan.user_samples:
            started_at, standard, actual, source = plan.user_intervals[
                sample.pk
            ]
            sample.interval_started_at = started_at
            sample.interval_standard_cost = standard
            sample.interval_actual_cost = actual
            sample.interval_source = source
        if plan.user_samples:
            Sub2APIUserUsageSample.objects.bulk_update(
                plan.user_samples,
                [
                    "interval_started_at",
                    "interval_standard_cost",
                    "interval_actual_cost",
                    "interval_source",
                ],
                batch_size=500,
            )

        replay = rebuild_account(plan.account_id, config)
    return {
        **preview,
        "replayed_observations": replay.rebuilt_observations,
        "inferred_intervals": replay.inferred_intervals,
        "automatic_exclusions": replay.automatic_exclusions,
    }
