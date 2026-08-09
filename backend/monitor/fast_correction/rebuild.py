"""FAST 修正缺口检测与按需历史重建。"""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q

from .constants import ZERO
from .domain import FastCorrectionInterval, aggregate_fast_logs, money
from .persistence import detail_rows
from ..integrations.sub2api import Sub2APIClient, Sub2APIUsageLog
from ..models import AppSettings, Observation, ObservationFastCorrection


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
            details.extend(detail_rows(observation, interval))
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
    from ..replay import rebuild_account

    replay = rebuild_account(account_id, config, replay_from=rebuild_start)
    correction = sum(
        (
            interval.selected_correction(config.cost_basis)
            for _row, interval in intervals
        ),
        ZERO,
    )
    return {
        "scope": scope,
        "rebuilt_observations": len(observations),
        "request_count": len(logs),
        "fast_request_count": sum(
            interval.fast_request_count for _row, interval in intervals
        ),
        "correction_usd": float(money(correction)),
        "replay_started_at": rebuild_start.isoformat(),
        "replayed_observations": replay.rebuilt_observations,
    }
