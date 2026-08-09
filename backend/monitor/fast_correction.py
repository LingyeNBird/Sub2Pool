"""FAST 请求的等效成本修正、持久化与按需重建。

Sub2API 当前把 OpenAI ``service_tier=priority`` 按 2 倍计费，而套餐周限按
2.5 倍消耗。模块只读取 Admin API 请求日志，保存额外的 0.5 倍等效成本；
原始用量、余额和请求日志均不会被修改。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q

from .models import (
    AppSettings,
    Observation,
    ObservationFastCorrection,
)
from .integrations.sub2api import Sub2APIClient, Sub2APIUsageLog

ZERO = Decimal("0")
COST_PRECISION = Decimal("0.000001")
SUB2API_FAST_MULTIPLIER = Decimal("2")
UPSTREAM_FAST_MULTIPLIER = Decimal("2.5")
FAST_EXTRA_FACTOR = (
    UPSTREAM_FAST_MULTIPLIER / SUB2API_FAST_MULTIPLIER - Decimal("1")
)
MAX_KEY_ID = 2**63 - 1


def _money(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class UserFastCorrection:
    user_id: int
    request_count: int
    fast_request_count: int
    fast_standard_cost: Decimal
    fast_actual_cost: Decimal
    standard_correction_cost: Decimal
    actual_correction_cost: Decimal


@dataclass(frozen=True)
class FastCorrectionInterval:
    started_at: datetime
    ended_at: datetime
    request_count: int
    fast_request_count: int
    standard_correction_cost: Decimal
    actual_correction_cost: Decimal
    users: tuple[UserFastCorrection, ...]

    def selected_correction(self, basis: str) -> Decimal:
        return (
            self.actual_correction_cost
            if basis == "actual"
            else self.standard_correction_cost
        )


def aggregate_fast_logs(
    logs: list[Sub2APIUsageLog],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> FastCorrectionInterval:
    """把一个采样区间的 FAST 请求按原始 Sub2API 用户 ID 汇总。"""

    totals: dict[int, dict[str, Decimal | int]] = {}
    fast_count = 0
    for log in logs:
        row = totals.setdefault(
            log.user_id,
            {
                "request_count": 0,
                "count": 0,
                "standard": ZERO,
                "actual": ZERO,
            },
        )
        row["request_count"] = int(row["request_count"]) + 1
        if log.service_tier != "priority":
            continue
        fast_count += 1
        row["count"] = int(row["count"]) + 1
        row["standard"] = Decimal(row["standard"]) + log.total_cost
        row["actual"] = Decimal(row["actual"]) + log.actual_cost
    users: list[UserFastCorrection] = []
    for user_id in sorted(totals):
        row = totals[user_id]
        standard = _money(Decimal(row["standard"]))
        actual = _money(Decimal(row["actual"]))
        users.append(
            UserFastCorrection(
                user_id=user_id,
                request_count=int(row["request_count"]),
                fast_request_count=int(row["count"]),
                fast_standard_cost=standard,
                fast_actual_cost=actual,
                standard_correction_cost=_money(
                    standard * FAST_EXTRA_FACTOR
                ),
                actual_correction_cost=_money(actual * FAST_EXTRA_FACTOR),
            )
        )

    return FastCorrectionInterval(
        started_at=started_at,
        ended_at=ended_at,
        request_count=len(logs),
        fast_request_count=fast_count,
        standard_correction_cost=_money(
            sum((row.standard_correction_cost for row in users), ZERO)
        ),
        actual_correction_cost=_money(
            sum((row.actual_correction_cost for row in users), ZERO)
        ),
        users=tuple(users),
    )


def fetch_fast_interval(
    client: Sub2APIClient,
    *,
    account_id: int,
    started_at: datetime,
    ended_at: datetime,
    timezone_name: str,
) -> FastCorrectionInterval:
    logs = client.usage_logs(
        account_id=account_id,
        started_at=started_at,
        ended_at=ended_at,
        timezone_name=timezone_name,
    )
    return aggregate_fast_logs(
        logs,
        started_at=started_at,
        ended_at=ended_at,
    )


def _detail_rows(
    observation: Observation,
    interval: FastCorrectionInterval,
) -> list[ObservationFastCorrection]:
    return [
        ObservationFastCorrection(
            observation=observation,
            sub2api_user_id=row.user_id,
            request_count=row.request_count,
            fast_request_count=row.fast_request_count,
            fast_standard_cost=row.fast_standard_cost,
            fast_actual_cost=row.fast_actual_cost,
            standard_correction_cost=row.standard_correction_cost,
            actual_correction_cost=row.actual_correction_cost,
        )
        for row in interval.users
    ]


def apply_fast_interval(
    observation: Observation,
    interval: FastCorrectionInterval,
) -> None:
    """在调用者的事务中覆盖一个观测区间的可重建 FAST 修正事实。"""

    observation.fast_correction_started_at = interval.started_at
    observation.fast_correction_request_count = interval.request_count
    observation.fast_correction_standard_cost = (
        interval.standard_correction_cost
    )
    observation.fast_correction_actual_cost = interval.actual_correction_cost
    observation.fast_corrections.all().delete()
    rows = _detail_rows(observation, interval)
    if rows:
        ObservationFastCorrection.objects.bulk_create(rows, batch_size=500)


class FastCorrectionPrefix:
    """一次加载账号全部修正，提供任意归属边界到观测点的前缀差。"""

    def __init__(self, account_id: int, basis: str):
        total_field = (
            "fast_correction_actual_cost"
            if basis == "actual"
            else "fast_correction_standard_cost"
        )
        user_field = (
            "actual_correction_cost"
            if basis == "actual"
            else "standard_correction_cost"
        )
        observations = list(
            Observation.objects.filter(account_id=account_id)
            .prefetch_related("fast_corrections")
            .order_by("observed_at", "id")
        )

        self.total_keys: list[tuple[datetime, int]] = []
        self.total_values: list[Decimal] = []
        self.user_keys: dict[int, list[tuple[datetime, int]]] = {}
        self.user_values: dict[int, list[Decimal]] = {}
        total_running = ZERO
        user_running: dict[int, Decimal] = {}
        for observation in observations:
            key = (observation.observed_at, observation.id)
            total_running += getattr(observation, total_field) or ZERO
            self.total_keys.append(key)
            self.total_values.append(total_running)
            for row in observation.fast_corrections.all():
                user_id = row.sub2api_user_id
                user_running[user_id] = (
                    user_running.get(user_id, ZERO) + getattr(row, user_field)
                )
                self.user_keys.setdefault(user_id, []).append(key)
                self.user_values.setdefault(user_id, []).append(
                    user_running[user_id]
                )

    @staticmethod
    def _prefix_at(
        keys: list[tuple[datetime, int]],
        values: list[Decimal],
        key: tuple[datetime, int],
    ) -> Decimal:
        index = bisect_right(keys, key) - 1
        return values[index] if index >= 0 else ZERO

    def total_between(
        self,
        started_at: datetime,
        observation: Observation,
    ) -> Decimal:
        end = self._prefix_at(
            self.total_keys,
            self.total_values,
            (observation.observed_at, observation.id),
        )
        start = self._prefix_at(
            self.total_keys,
            self.total_values,
            (started_at, MAX_KEY_ID),
        )
        return max(ZERO, end - start)

    def user_between(
        self,
        user_id: int,
        started_at: datetime,
        ended_at: datetime,
        *,
        observation_id: int = MAX_KEY_ID,
    ) -> Decimal:
        keys = self.user_keys.get(user_id, [])
        values = self.user_values.get(user_id, [])
        end = self._prefix_at(keys, values, (ended_at, observation_id))
        start = self._prefix_at(
            keys,
            values,
            (started_at, MAX_KEY_ID),
        )
        return max(ZERO, end - start)


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


def _bucket_intervals(
    observations: list[Observation],
    logs: list[Sub2APIUsageLog],
    rebuild_start: datetime,
) -> list[tuple[Observation, FastCorrectionInterval]]:
    result: list[tuple[Observation, FastCorrectionInterval]] = []
    log_index = 0
    interval_start = rebuild_start
    while log_index < len(logs) and logs[log_index].created_at < rebuild_start:
        log_index += 1
    for observation in observations:
        interval_logs: list[Sub2APIUsageLog] = []
        while (
            log_index < len(logs)
            and logs[log_index].created_at < observation.observed_at
        ):
            if logs[log_index].created_at >= interval_start:
                interval_logs.append(logs[log_index])
            log_index += 1
        result.append(
            (
                observation,
                aggregate_fast_logs(
                    interval_logs,
                    started_at=interval_start,
                    ended_at=observation.observed_at,
                ),
            )
        )
        interval_start = observation.observed_at
    return result


def rebuild_fast_corrections(
    config: AppSettings,
    scope: str,
) -> dict[str, int | float | str | None]:
    """从当前归属区间或 Sub2API 最早日志重建 FAST 修正。"""

    if scope not in {"cycle", "all"}:
        raise ValueError("FAST 修正重建范围无效")
    if not config.openai_account_id:
        raise ValueError("尚未配置 OpenAI 上游账号")

    account_id = config.openai_account_id
    all_observations = list(
        Observation.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    if not all_observations:
        return {
            "scope": scope,
            "rebuilt_observations": 0,
            "request_count": 0,
            "fast_request_count": 0,
            "correction_usd": 0.0,
            "replay_started_at": None,
        }

    if scope == "cycle":
        requested_start = current_cycle_start(config)
        if requested_start is None:
            raise ValueError("尚无法确定当前周期起点")
    else:
        requested_start = None
    ended_at = all_observations[-1].observed_at

    with Sub2APIClient(config) as client:
        logs = client.usage_logs(
            account_id=account_id,
            started_at=requested_start,
            ended_at=ended_at,
            timezone_name=config.timezone,
        )

    if requested_start is None:
        rebuild_start = (
            logs[0].created_at
            if logs
            else all_observations[0].upstream_resets_at
            - timedelta(seconds=all_observations[0].window_seconds)
        )
    else:
        rebuild_start = requested_start
    observations = [
        row for row in all_observations if row.observed_at >= rebuild_start
    ]
    intervals = _bucket_intervals(observations, logs, rebuild_start)

    with transaction.atomic():
        observation_ids = [row.id for row in observations]
        ObservationFastCorrection.objects.filter(
            observation_id__in=observation_ids
        ).delete()
        details: list[ObservationFastCorrection] = []
        for observation, interval in intervals:
            observation.fast_correction_started_at = interval.started_at
            observation.fast_correction_request_count = interval.request_count
            observation.fast_correction_standard_cost = (
                interval.standard_correction_cost
            )
            observation.fast_correction_actual_cost = (
                interval.actual_correction_cost
            )
            details.extend(_detail_rows(observation, interval))
        if observations:
            Observation.objects.bulk_update(
                observations,
                [
                    "fast_correction_started_at",
                    "fast_correction_request_count",
                    "fast_correction_standard_cost",
                    "fast_correction_actual_cost",
                ],
                batch_size=500,
            )
        if details:
            ObservationFastCorrection.objects.bulk_create(
                details,
                batch_size=500,
            )

    # 延迟导入避免 replay 在加载前缀类时形成模块循环。
    from .replay import rebuild_account

    replay = rebuild_account(account_id, config, replay_from=rebuild_start)
    correction = sum(
        (interval.selected_correction(config.cost_basis) for _row, interval in intervals),
        ZERO,
    )
    return {
        "scope": scope,
        "rebuilt_observations": len(observations),
        "request_count": len(logs),
        "fast_request_count": sum(
            interval.fast_request_count for _row, interval in intervals
        ),
        "correction_usd": float(_money(correction)),
        "replay_started_at": rebuild_start.isoformat(),
        "replayed_observations": replay.rebuilt_observations,
    }
