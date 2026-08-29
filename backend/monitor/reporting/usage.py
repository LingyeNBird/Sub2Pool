"""参与者账号周期用量的时间序列投影。"""

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AbstractBaseUser

from ..cpa.usage import cpa_event_cost
from ..models import (
    AppSettings,
    CPAUsageEvent,
    MonitoredAccount,
    Participant,
    ParticipantUsageSample,
)


def participant_usage_series(
    *,
    account: MonitoredAccount,
    user: AbstractBaseUser,
    location: ZoneInfo,
    now: datetime,
    usage_days: int,
    usage_precision: str,
) -> list[dict]:
    """按调用者可见范围和请求粒度生成参与者用量序列。"""
    participants = Participant.objects.all()
    if not user.is_staff:
        participants = participants.filter(authorized_users=user)

    samples = (
        ParticipantUsageSample.objects.filter(
            observed_at__gte=now - timedelta(days=usage_days),
            account_id=account.fact_key,
            participant__in=participants,
        )
        .select_related("participant")
        .order_by("participant_id", "observed_at", "id")
    )
    usage_buckets: dict[int, dict[str, dict]] = defaultdict(dict)
    for sample in samples:
        local = sample.observed_at.astimezone(location)
        if usage_precision == "raw":
            bucket = sample.observed_at.isoformat()
            label = local.strftime("%m-%d %H:%M")
        elif usage_precision == "hour":
            bucket = local.replace(
                minute=0,
                second=0,
                microsecond=0,
            ).isoformat()
            label = local.strftime("%m-%d %H:00")
        else:
            bucket = local.date().isoformat()
            label = local.strftime("%m-%d")
        # 账号周期用量用于归属权益；用户余额是 Sub2API 的全局可用余额。
        usage_buckets[sample.participant_id][bucket] = {
            "observed_at": sample.observed_at.isoformat(),
            "label": label,
            "account_cycle_usage_usd": float(sample.selected_cost),
            "balance_usd": (
                float(sample.balance_usd)
                if sample.balance_usd is not None
                else None
            ),
        }

    return [
        {
            "participant_id": participant.id,
            "participant_name": participant.name,
            "account_id": account.id,
            "external_account_id": account.external_account_id,
            "sub2api_user_id": participant.sub2api_user_id,
            "points": list(usage_buckets[participant.id].values()),
        }
        for participant in participants
    ]

def cpa_api_key_usage_series(
    *,
    config: AppSettings,
    account: MonitoredAccount,
    location: ZoneInfo,
    now: datetime,
    usage_days: int,
    usage_precision: str,
) -> list[dict]:
    events = CPAUsageEvent.objects.filter(
        account=account,
        occurred_at__gte=now - timedelta(days=usage_days),
        occurred_at__lte=now,
    ).order_by("occurred_at", "id")
    series: dict[str, dict] = {}
    for event in events:
        key = event.api_key_hash or "unattributed"
        item = series.setdefault(
            key,
            {
                "api_key_id": key[:12],
                "api_key_name": (
                    f"API Key ····{event.api_key_hint}"
                    if event.api_key_hint
                    else "未提供 API Key"
                ),
                "total_usage_usd": 0.0,
                "request_count": 0,
                "token_count": 0,
                "unpriced_request_count": 0,
                "_buckets": {},
            },
        )
        local = event.occurred_at.astimezone(location)
        if usage_precision == "raw":
            bucket = f"{event.occurred_at.isoformat()}-{event.id}"
            label = local.strftime("%m-%d %H:%M:%S")
        elif usage_precision == "hour":
            bucket = local.replace(
                minute=0,
                second=0,
                microsecond=0,
            ).isoformat()
            label = local.strftime("%m-%d %H:00")
        else:
            bucket = local.date().isoformat()
            label = local.strftime("%m-%d")
        point = item["_buckets"].setdefault(
            bucket,
            {
                "observed_at": event.occurred_at.isoformat(),
                "label": label,
                "usage_usd": 0.0,
                "request_count": 0,
                "token_count": 0,
            },
        )
        estimated_cost, unknown_model = cpa_event_cost(event, config)
        cost = float(estimated_cost)
        point["usage_usd"] += cost
        point["request_count"] += 1
        point["token_count"] += event.total_tokens
        item["total_usage_usd"] += cost
        item["request_count"] += 1
        item["token_count"] += event.total_tokens
        if unknown_model:
            item["unpriced_request_count"] += 1
    result = []
    for item in series.values():
        buckets = item.pop("_buckets")
        item["points"] = list(buckets.values())
        result.append(item)
    return sorted(
        result,
        key=lambda item: (-item["total_usage_usd"], item["api_key_name"]),
    )
