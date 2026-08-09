"""参与者账号周期用量的时间序列投影。"""

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AbstractBaseUser

from ..models import Participant, ParticipantUsageSample


def participant_usage_series(
    *,
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
            "sub2api_user_id": participant.sub2api_user_id,
            "points": list(usage_buckets[participant.id].values()),
        }
        for participant in participants
    ]
