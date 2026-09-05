"""Current-cycle API-key usage snapshots backed by read-only Sub2API queries."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone
from django.db import transaction

from .billing_correction.domain import BillingCorrectionRules, CorrectionAmounts
from .billing_correction.facts import validate_interval_logs
from .billing_correction.persistence import persist_api_usage_facts
from .billing_correction.rules import corrections_digest, corrections_enabled

from .fast_correction.constants import COST_PRECISION
from .integrations.sub2api import Sub2APIClient
from .models import (
    AppSettings,
    APIUsageRequestFact,
    MonitoredAccount,
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


def latest_cycle_observation(
    account: MonitoredAccount,
) -> Observation | None:
    return (
        Observation.objects.filter(
            account_id=account.fact_key,
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
        fast_correction_rules_hash=corrections_digest(config),
    )


def fresh_snapshot(*, participant, observation, config, now):
    # Age applies to upstream facts, not pricing policy. Policy changes alone
    # must not trigger another current-week request-log download.
    cached = ParticipantAPIUsageSnapshot.objects.filter(
        participant=participant, account_id=observation.account_id,
        attribution_started_at=observation.attribution_started_at,
        raw_facts_available=True, raw_user_id=participant.sub2api_user_id,
        observed_at__gte=now - CACHE_INTERVAL,
    ).order_by("-observed_at", "-id").first()
    if cached is not None:
        logs = list(APIUsageRequestFact.objects.filter(
            account_id=cached.account_id, user_id=cached.raw_user_id,
            created_at__gte=cached.attribution_started_at, created_at__lt=cached.observed_at,
        ))
        if len(logs) == cached.raw_request_count:
            project_snapshot(cached, logs=logs, keys=cached.raw_api_keys, observation=observation, config=config)
            return cached
        raise ValueError("API 用量缓存的原始事实数量不一致；未使用残缺数据重算")
    return _matching_snapshots(participant=participant, observation=observation, config=config).filter(observed_at__gte=now - CACHE_INTERVAL).order_by("-observed_at", "-id").first()


def project_snapshot(snapshot, *, logs, keys, observation, config):
    names = {int(item["id"]): str(item.get("name") or "").strip() for item in keys}
    statuses = {int(item["id"]): str(item.get("status") or "") for item in keys}
    rules = BillingCorrectionRules(config)
    costs = defaultdict(Decimal)
    corrections = {}
    total = CorrectionAmounts()
    unknown = 0
    for item in logs:
        result = rules.calculate(item, config.cost_basis)
        costs[item.api_key_id] += result.corrected_cost.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)
        corrections[item.api_key_id] = corrections.get(item.api_key_id, CorrectionAmounts()) + result.amounts
        total += result.amounts
        unknown += int(result.long_context_unknown)
        if item.api_key_id and item.api_key_name:
            names.setdefault(item.api_key_id, item.api_key_name)
    participant_total = sum(costs.values(), ZERO)
    weekly_total = observation.selected_total_cost * HUNDRED / observation.interval_used_percent if observation.interval_used_percent > ZERO else None
    key_ids = sorted(set(names) | set(costs), key=lambda key: (key == 0, names.get(key, "").casefold(), key))
    snapshot.cost_basis = config.cost_basis
    snapshot.fast_correction_enabled = config.fast_correction_enabled
    snapshot.fast_correction_rules_hash = corrections_digest(config)
    snapshot.participant_total_usd = participant_total
    snapshot.weekly_total_estimate_usd = weekly_total
    snapshot.participant_weekly_percent = _percentage(participant_total, weekly_total)
    snapshot.api_keys = [{
        "api_key_id": key or None,
        "name": names.get(key) or ("未识别或已删除的 API 密钥" if key == 0 else f"API 密钥 {key}"),
        "status": statuses.get(key, ""), "usage_usd": float(costs[key]),
        "participant_usage_percent": float(_percentage(costs[key], participant_total)),
        "weekly_quota_percent": float(_percentage(costs[key], weekly_total)),
        **corrections.get(key, CorrectionAmounts()).payload(),
    } for key in key_ids]
    snapshot._correction_payload = {**total.payload(), "correction_facts_complete": True, "unknown_long_context_request_count": unknown, "corrections_enabled": corrections_enabled(config)}


def refresh_participant_api_usage(
    *,
    client,
    participant: Participant,
    observation: Observation,
    config: AppSettings,
    observed_to,
) -> ParticipantAPIUsageSnapshot:
    """Capture immutable requests once; save only a replaceable UI projection."""
    keys = client.list_user_api_keys(participant.sub2api_user_id)
    logs = client.usage_logs(
        account_id=observation.account_id, user_id=participant.sub2api_user_id,
        started_at=observation.attribution_started_at, ended_at=observed_to,
        timezone_name=config.timezone,
    )
    validate_interval_logs(logs, account_id=observation.account_id, user_id=participant.sub2api_user_id, started_at=observation.attribution_started_at, ended_at=observed_to)
    # Never persist the raw API key/token, even if a future client returns it.
    keys = [{"id": int(row["id"]), "name": str(row.get("name") or ""), "status": str(row.get("status") or "")} for row in keys]
    snapshot = ParticipantAPIUsageSnapshot(
        participant=participant, observation=observation,
        account_id=observation.account_id,
        attribution_started_at=observation.attribution_started_at,
        observed_at=observed_to, raw_facts_available=True,
        raw_request_count=len(logs), raw_user_id=participant.sub2api_user_id,
        raw_api_keys=keys,
    )
    project_snapshot(snapshot, logs=logs, keys=keys, observation=observation, config=config)
    with transaction.atomic():
        persist_api_usage_facts(observation.account_id, logs)
        projected_keys = snapshot.api_keys
        correction_keys = set(CorrectionAmounts().payload())
        snapshot.api_keys = [
            {key: value for key, value in row.items() if key not in correction_keys}
            for row in projected_keys
        ]
        snapshot.save()
        snapshot.api_keys = projected_keys
    return snapshot


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
    """Refresh due user/account API-key summaries across enabled accounts."""

    if not config.monitoring_enabled:
        return {"refreshed": 0, "skipped": 0}
    now = timezone.now()
    due: list[tuple[Participant, Observation]] = []
    skipped = 0
    for account in MonitoredAccount.objects.filter(
        enabled=True,
        provider="sub2api",
    ):
        observation = latest_cycle_observation(account)
        if observation is None:
            continue
        participants = Participant.objects.filter(enabled=True)
        for participant in participants:
            if fresh_snapshot(
                participant=participant,
                observation=observation,
                config=config,
                now=now,
            ) is None:
                due.append((participant, observation))
            else:
                skipped += 1
    if not due:
        return {"refreshed": 0, "skipped": skipped}

    refreshed = 0
    with Sub2APIClient(config) as client:
        for participant, observation in due:
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
        **getattr(snapshot, "_correction_payload", {}),
    }
