"""采样所需的 Sub2API 本地用量读取与趋势点持久化。"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .types import LocalBundle, LocalParticipantData, WindowReference
from ..integrations.sub2api import Sub2APIReader, Sub2APIUserUsage
from ..models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)

ZERO = Decimal("0")


def fetch_local(
    client: Sub2APIReader,
    config: AppSettings,
    reference: WindowReference,
    participants: list[Participant],
    now: datetime,
) -> LocalBundle:
    """只读取 Sub2API；数据库写入在最终确认查询窗口后统一执行一次。"""

    location = ZoneInfo(config.timezone)
    start_date = (
        reference.reset_at - timedelta(seconds=reference.window_seconds)
    ).astimezone(location).date()
    end_date = now.astimezone(location).date()
    query = {
        "account_id": reference.account_id,
        "start_date": start_date,
        "end_date": end_date,
        "timezone_name": config.timezone,
    }
    total = client.usage_stats(**query)

    # 正式客户端会一次性读取全部用户；保留回退分支，兼容只实现旧版
    # usage_stats 接口的替代客户端，不改变正式采样的全量行为。
    fetch_all = getattr(client, "all_user_usage_stats", None)
    all_users = fetch_all(**query) if callable(fetch_all) else []
    usage_by_user = {item.user_id: item for item in all_users}

    rows: list[LocalParticipantData] = []
    for participant in participants:
        user_usage = usage_by_user.get(participant.sub2api_user_id)
        if user_usage is None:
            stats = client.usage_stats(
                **query,
                user_id=participant.sub2api_user_id,
            )
            user_usage = Sub2APIUserUsage(
                user_id=participant.sub2api_user_id,
                email=participant.sub2api_email,
                username=participant.sub2api_username,
                stats=stats,
            )
            usage_by_user[user_usage.user_id] = user_usage
            all_users.append(user_usage)
        rows.append(
            LocalParticipantData(
                participant=participant,
                stats=user_usage.stats,
                balance=client.user_balance(participant.sub2api_user_id),
            )
        )
    return LocalBundle(
        total=total,
        participants=rows,
        users=all_users,
        checked_at=now,
    )


def _participant_baselines(
    latest: Observation | None,
) -> dict[int, Decimal]:
    if latest is None:
        return {}
    return {
        snapshot.participant_id: max(
            ZERO,
            snapshot.raw_selected_cost - snapshot.selected_cost,
        )
        for snapshot in latest.participant_snapshots.all()
    }


def save_local_bundle(
    config: AppSettings,
    reference: WindowReference,
    local: LocalBundle,
    latest: Observation | None,
) -> None:
    """保存一次本地趋势点；raw 字段永远保留 Sub2API 返回的累计值。"""

    window_started_at = reference.reset_at - timedelta(
        seconds=reference.window_seconds
    )
    Sub2APIUserUsageSample.objects.bulk_create(
        [
            Sub2APIUserUsageSample(
                account_id=reference.account_id,
                sub2api_user_id=user.user_id,
                username=user.username,
                email=user.email,
                observed_at=local.checked_at,
                window_started_at=window_started_at,
                window_resets_at=reference.reset_at,
                total_standard_cost=user.stats.total_cost,
                total_actual_cost=user.stats.total_actual_cost,
            )
            for user in local.users
        ],
        ignore_conflicts=True,
    )

    baselines = _participant_baselines(latest)
    changed_participants: list[Participant] = []
    usage_samples: list[ParticipantUsageSample] = []
    for row in local.participants:
        raw_cost = row.selected_cost(config.cost_basis)
        selected_cost = max(
            ZERO,
            raw_cost - baselines.get(row.participant.pk, ZERO),
        )
        row.participant.latest_balance_usd = row.balance.balance
        row.participant.latest_selected_cost = selected_cost
        row.participant.last_checked_at = local.checked_at
        changed_participants.append(row.participant)
        usage_samples.append(
            ParticipantUsageSample(
                participant=row.participant,
                account_id=reference.account_id,
                attribution_started_at=(
                    latest.attribution_started_at if latest is not None else None
                ),
                observed_at=local.checked_at,
                balance_usd=row.balance.balance,
                selected_cost=selected_cost,
                raw_selected_cost=raw_cost,
            )
        )
    if changed_participants:
        Participant.objects.bulk_update(
            changed_participants,
            ["latest_balance_usd", "latest_selected_cost", "last_checked_at"],
        )
        ParticipantUsageSample.objects.bulk_create(
            usage_samples,
            ignore_conflicts=True,
        )
