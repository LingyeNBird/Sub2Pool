"""从 Sub2API 请求日志补全旧版缺失的全用户用量事实。"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction

from .integrations.sub2api import Sub2APIClient, Sub2APIUsageLog
from .models import AppSettings, Observation, Sub2APIUserUsageSample
from .replay import rebuild_account

ZERO = Decimal("0")
MIN_TOLERANCE_USD = Decimal("0.05")
RELATIVE_TOLERANCE = Decimal("0.001")


class HistoricalUsageBackfillError(ValueError):
    """历史日志不足以安全补建全部缺失事实。"""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class _CostPrefix:
    times: tuple[datetime, ...]
    standard: tuple[Decimal, ...]
    actual: tuple[Decimal, ...]

    @classmethod
    def from_logs(cls, logs: list[Sub2APIUsageLog]) -> "_CostPrefix":
        standard = [ZERO]
        actual = [ZERO]
        for row in logs:
            standard.append(standard[-1] + row.total_cost)
            actual.append(actual[-1] + row.actual_cost)
        return cls(
            times=tuple(row.created_at for row in logs),
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


@dataclass
class HistoricalUsagePlan:
    account_id: int
    observations: list[Observation]
    logs: list[Sub2APIUsageLog]
    rows_to_create: list[Sub2APIUserUsageSample]
    segment_count: int
    compatible_segments: int
    incompatible_segments: int
    skipped_observations: int
    existing_samples: int
    missing_samples: int
    max_standard_gap_usd: Decimal
    max_actual_gap_usd: Decimal

    def public_data(self) -> dict[str, int | float | str | bool | None]:
        return {
            "account_id": self.account_id,
            "observation_count": len(self.observations),
            "segment_count": self.segment_count,
            "compatible_segments": self.compatible_segments,
            "incompatible_segments": self.incompatible_segments,
            "skipped_observations": self.skipped_observations,
            "request_log_count": len(self.logs),
            "user_count": len({row.user_id for row in self.logs}),
            "existing_samples": self.existing_samples,
            "missing_samples": self.missing_samples,
            "fillable_samples": len(self.rows_to_create),
            "max_standard_gap_usd": float(self.max_standard_gap_usd),
            "max_actual_gap_usd": float(self.max_actual_gap_usd),
            "earliest_log_at": (
                self.logs[0].created_at.isoformat() if self.logs else None
            ),
            "latest_log_at": (
                self.logs[-1].created_at.isoformat() if self.logs else None
            ),
            "can_backfill": bool(
                self.rows_to_create and not self.incompatible_segments
            ),
        }


def _statistics_start(observation: Observation, location: ZoneInfo) -> datetime:
    """复现实时采样使用的自然日起点，再由日志收紧到观测时刻。"""

    window_start = observation.upstream_resets_at - timedelta(
        seconds=observation.window_seconds
    )
    local_date = window_start.astimezone(location).date()
    return datetime.combine(local_date, time.min, tzinfo=location).astimezone(
        timezone.utc
    )


def _segment_key(observation: Observation) -> datetime:
    return observation.attribution_started_at or (
        observation.upstream_resets_at
        - timedelta(seconds=observation.window_seconds)
    )


def _tolerance(total: Decimal) -> Decimal:
    return max(MIN_TOLERANCE_USD, abs(total) * RELATIVE_TOLERANCE)


def inspect_historical_user_usage(
    config: AppSettings,
) -> HistoricalUsagePlan:
    """只读历史请求日志，规划可以安全补建的全量用户事实。"""

    if not config.openai_account_id:
        raise HistoricalUsageBackfillError("尚未配置 OpenAI 上游账号")
    try:
        location = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HistoricalUsageBackfillError("统计时区无效") from exc

    account_id = config.openai_account_id
    observations = list(
        Observation.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    if not observations:
        return HistoricalUsagePlan(
            account_id=account_id,
            observations=[],
            logs=[],
            rows_to_create=[],
            segment_count=0,
            compatible_segments=0,
            incompatible_segments=0,
            skipped_observations=0,
            existing_samples=0,
            missing_samples=0,
            max_standard_gap_usd=ZERO,
            max_actual_gap_usd=ZERO,
        )

    with Sub2APIClient(config) as client:
        logs = client.usage_logs(
            account_id=account_id,
            started_at=None,
            ended_at=observations[-1].observed_at,
            timezone_name=config.timezone,
        )
        users = client.list_users()

    metadata = {
        int(row["id"]): (
            str(row.get("username") or ""),
            str(row.get("email") or ""),
        )
        for row in users
    }
    logs_by_user: dict[int, list[Sub2APIUsageLog]] = defaultdict(list)
    for row in logs:
        logs_by_user[row.user_id].append(row)
    prefixes = {
        user_id: _CostPrefix.from_logs(rows)
        for user_id, rows in logs_by_user.items()
    }

    observation_times = [row.observed_at for row in observations]
    existing_rows = list(
        Sub2APIUserUsageSample.objects.filter(
            account_id=account_id,
            observed_at__in=observation_times,
        ).order_by("observed_at", "sub2api_user_id", "id")
    )
    existing_by_time: dict[
        datetime,
        dict[int, Sub2APIUserUsageSample],
    ] = defaultdict(dict)
    for row in existing_rows:
        existing_by_time[row.observed_at][row.sub2api_user_id] = row

    candidates_by_segment: dict[
        datetime,
        list[Sub2APIUserUsageSample],
    ] = defaultdict(list)
    observations_by_segment: dict[datetime, list[Observation]] = defaultdict(
        list
    )
    compatible_by_segment: dict[datetime, bool] = defaultdict(lambda: True)
    max_standard_gap = ZERO
    max_actual_gap = ZERO
    missing_samples = 0
    log_user_ids = sorted(prefixes)

    for observation in observations:
        segment = _segment_key(observation)
        observations_by_segment[segment].append(observation)
        known = existing_by_time.get(observation.observed_at, {})
        totals_by_user: dict[int, tuple[Decimal, Decimal]] = {
            user_id: (row.total_standard_cost, row.total_actual_cost)
            for user_id, row in known.items()
        }
        stats_start = _statistics_start(observation, location)
        window_start = observation.upstream_resets_at - timedelta(
            seconds=observation.window_seconds
        )

        for user_id in log_user_ids:
            standard, actual = prefixes[user_id].between(
                stats_start,
                observation.observed_at,
            )
            if user_id in known:
                continue
            missing_samples += 1
            totals_by_user[user_id] = (standard, actual)
            username, email = metadata.get(user_id, ("", ""))
            candidates_by_segment[segment].append(
                Sub2APIUserUsageSample(
                    account_id=account_id,
                    sub2api_user_id=user_id,
                    username=username,
                    email=email,
                    observed_at=observation.observed_at,
                    window_started_at=window_start,
                    window_resets_at=observation.upstream_resets_at,
                    total_standard_cost=standard,
                    total_actual_cost=actual,
                )
            )

        standard_total = sum(
            (value[0] for value in totals_by_user.values()),
            ZERO,
        )
        actual_total = sum(
            (value[1] for value in totals_by_user.values()),
            ZERO,
        )
        standard_gap = abs(observation.total_standard_cost - standard_total)
        actual_gap = abs(observation.total_actual_cost - actual_total)
        max_standard_gap = max(max_standard_gap, standard_gap)
        max_actual_gap = max(max_actual_gap, actual_gap)
        if (
            standard_gap > _tolerance(observation.total_standard_cost)
            or actual_gap > _tolerance(observation.total_actual_cost)
        ):
            compatible_by_segment[segment] = False

    rows_to_create: list[Sub2APIUserUsageSample] = []
    incompatible_segments = 0
    skipped_observations = 0
    compatible_segments = 0
    for segment, segment_observations in observations_by_segment.items():
        if compatible_by_segment[segment]:
            compatible_segments += 1
            rows_to_create.extend(candidates_by_segment[segment])
        else:
            incompatible_segments += 1
            skipped_observations += len(segment_observations)

    return HistoricalUsagePlan(
        account_id=account_id,
        observations=observations,
        logs=logs,
        rows_to_create=rows_to_create,
        segment_count=len(observations_by_segment),
        compatible_segments=compatible_segments,
        incompatible_segments=incompatible_segments,
        skipped_observations=skipped_observations,
        existing_samples=len(existing_rows),
        missing_samples=missing_samples,
        max_standard_gap_usd=max_standard_gap,
        max_actual_gap_usd=max_actual_gap,
    )


def backfill_historical_user_usage(config: AppSettings) -> dict:
    """补全所有安全区间，并从第一条原始观测开始重放全部历史。"""

    plan = inspect_historical_user_usage(config)
    preview = plan.public_data()
    if plan.incompatible_segments:
        raise HistoricalUsageBackfillError(
            "部分历史区间的用户日志合计与原始总成本不一致，未写入任何数据",
            preview,
        )
    if not plan.rows_to_create:
        return {
            **preview,
            "inserted_samples": 0,
            "replayed_observations": 0,
        }

    with transaction.atomic():
        before = Sub2APIUserUsageSample.objects.filter(
            account_id=plan.account_id
        ).count()
        Sub2APIUserUsageSample.objects.bulk_create(
            plan.rows_to_create,
            ignore_conflicts=True,
            batch_size=500,
        )
        after = Sub2APIUserUsageSample.objects.filter(
            account_id=plan.account_id
        ).count()
        replay = rebuild_account(plan.account_id, config)

    return {
        **preview,
        "inserted_samples": max(0, after - before),
        "replayed_observations": replay.rebuilt_observations,
        "inferred_intervals": replay.inferred_intervals,
        "automatic_exclusions": replay.automatic_exclusions,
    }


def rebuild_all_particle_results(config: AppSettings) -> dict:
    """显式忽略版本标记，从账号第一条原始观测全量重放。"""

    if not config.openai_account_id:
        raise HistoricalUsageBackfillError("尚未配置 OpenAI 上游账号")
    replay = rebuild_account(config.openai_account_id, config)
    return replay.as_dict()
