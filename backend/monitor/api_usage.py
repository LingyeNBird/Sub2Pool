"""Current-cycle API-key usage snapshots backed by read-only Sub2API queries."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from .fast_correction.constants import COST_PRECISION, FAST_EXTRA_FACTOR
from .integrations.sub2api import Sub2APIClient
from .models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantAPIUsageSnapshot,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")
PERCENT_PRECISION = Decimal("0.0001")
CACHE_INTERVAL = timedelta(hours=1)


def _percentage(numerator: Decimal, denominator: Decimal | None) -> Decimal:
    if denominator is None or denominator <= ZERO:
        return ZERO
    return (numerator * HUNDRED / denominator).quantize(
        PERCENT_PRECISION,
        rounding=ROUND_HALF_UP,
    )


def _selected_log_cost(item, config: AppSettings) -> Decimal:
    cost = item.selected(config.cost_basis)
    if config.fast_correction_enabled and item.service_tier == "priority":
        cost += cost * FAST_EXTRA_FACTOR
    return cost.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


def latest_cycle_observation(config: AppSettings) -> Observation | None:
    if not config.openai_account_id:
        return None
    return (
        Observation.objects.filter(
            account_id=config.openai_account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("-observed_at", "-id")
        .first()
    )


def _matching_snapshots(
    *,
    participant: Participant,
    observation: Observation,
    config: AppSettings,
):
    return ParticipantAPIUsageSnapshot.objects.filter(
        participant=participant,
        account_id=observation.account_id,
        attribution_started_at=observation.attribution_started_at,
        cost_basis=config.cost_basis,
        fast_correction_enabled=config.fast_correction_enabled,
    )


def fresh_snapshot(
    *,
    participant: Participant,
    observation: Observation,
    config: AppSettings,
    now,
) -> ParticipantAPIUsageSnapshot | None:
    return (
        _matching_snapshots(
            participant=participant,
            observation=observation,
            config=config,
        )
        .filter(observed_at__gte=now - CACHE_INTERVAL)
        .order_by("-observed_at", "-id")
        .first()
    )


def refresh_participant_api_usage(
    *,
    client,
    participant: Participant,
    observation: Observation,
    config: AppSettings,
    observed_to,
) -> ParticipantAPIUsageSnapshot:
    """读取一个参与者的当前周期日志，并只保存按 API 密钥汇总的结论。"""

    keys = client.list_user_api_keys(participant.sub2api_user_id)
    logs = client.usage_logs(
        account_id=observation.account_id,
        user_id=participant.sub2api_user_id,
        started_at=observation.attribution_started_at,
        ended_at=observed_to,
        timezone_name=config.timezone,
    )
    names = {
        int(item["id"]): str(item.get("name") or "").strip()
        for item in keys
    }
    statuses = {
        int(item["id"]): str(item.get("status") or "")
        for item in keys
    }
    costs: defaultdict[int, Decimal] = defaultdict(Decimal)
    for item in logs:
        costs[item.api_key_id] += _selected_log_cost(item, config)
        if item.api_key_id and item.api_key_name:
            names.setdefault(item.api_key_id, item.api_key_name)

    participant_total = sum(costs.values(), ZERO)
    weekly_total = (
        observation.selected_total_cost
        * HUNDRED
        / observation.interval_used_percent
        if observation.interval_used_percent > ZERO
        else None
    )
    key_ids = sorted(
        set(names) | set(costs),
        key=lambda key_id: (
            key_id == 0,
            names.get(key_id, "").casefold(),
            key_id,
        ),
    )
    api_keys = [
        {
            "api_key_id": key_id or None,
            "name": names.get(key_id)
            or (
                "未识别或已删除的 API 密钥"
                if key_id == 0
                else f"API 密钥 {key_id}"
            ),
            "status": statuses.get(key_id, ""),
            "usage_usd": float(costs[key_id]),
            "participant_usage_percent": float(
                _percentage(costs[key_id], participant_total)
            ),
            "weekly_quota_percent": float(
                _percentage(costs[key_id], weekly_total)
            ),
        }
        for key_id in key_ids
    ]
    return ParticipantAPIUsageSnapshot.objects.create(
        participant=participant,
        observation=observation,
        account_id=observation.account_id,
        attribution_started_at=observation.attribution_started_at,
        observed_at=observed_to,
        cost_basis=config.cost_basis,
        fast_correction_enabled=config.fast_correction_enabled,
        participant_total_usd=participant_total,
        weekly_total_estimate_usd=weekly_total,
        participant_weekly_percent=_percentage(participant_total, weekly_total),
        api_keys=api_keys,
    )


def get_participant_api_usage(
    *,
    participant: Participant,
    observation: Observation,
    config: AppSettings,
    now=None,
) -> ParticipantAPIUsageSnapshot:
    """优先返回一小时内结论；缓存过期时执行一次只读刷新。"""

    observed_to = now or timezone.now()
    cached = fresh_snapshot(
        participant=participant,
        observation=observation,
        config=config,
        now=observed_to,
    )
    if cached is not None:
        return cached
    with Sub2APIClient(config) as client:
        return refresh_participant_api_usage(
            client=client,
            participant=participant,
            observation=observation,
            config=config,
            observed_to=observed_to,
        )


def refresh_due_api_usage_snapshots(config: AppSettings) -> dict[str, int]:
    """后台每轮只检查缓存年龄；每名参与者至多每小时刷新一次。"""

    if not config.monitoring_enabled:
        return {"refreshed": 0, "skipped": 0}
    observation = latest_cycle_observation(config)
    if observation is None:
        return {"refreshed": 0, "skipped": 0}
    now = timezone.now()
    due: list[Participant] = []
    skipped = 0
    for participant in Participant.objects.filter(enabled=True):
        if fresh_snapshot(
            participant=participant,
            observation=observation,
            config=config,
            now=now,
        ) is None:
            due.append(participant)
        else:
            skipped += 1
    if not due:
        return {"refreshed": 0, "skipped": skipped}

    refreshed = 0
    with Sub2APIClient(config) as client:
        for participant in due:
            refresh_participant_api_usage(
                client=client,
                participant=participant,
                observation=observation,
                config=config,
                observed_to=now,
            )
            refreshed += 1
    return {"refreshed": refreshed, "skipped": skipped}


def api_usage_snapshot_data(snapshot: ParticipantAPIUsageSnapshot) -> dict:
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": snapshot.participant.name,
        "sub2api_user_id": snapshot.participant.sub2api_user_id,
        "starts_at": snapshot.attribution_started_at.isoformat(),
        "observed_to": snapshot.observed_at.isoformat(),
        "cost_basis": snapshot.cost_basis,
        "fast_correction_enabled": snapshot.fast_correction_enabled,
        "participant_total_usd": float(snapshot.participant_total_usd),
        "weekly_total_estimate_usd": (
            float(snapshot.weekly_total_estimate_usd)
            if snapshot.weekly_total_estimate_usd is not None
            else None
        ),
        "participant_weekly_percent": float(
            snapshot.participant_weekly_percent
        ),
        "api_keys": snapshot.api_keys,
    }
