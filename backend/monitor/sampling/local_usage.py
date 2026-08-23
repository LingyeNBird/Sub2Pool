"""采样所需的 Sub2API 本地用量读取与趋势点持久化。"""

from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from .types import LocalBundle, LocalParticipantData, WindowReference
from ..fact_utils import expected_user_digest
from ..accounting.boundaries import same_official_reset
from ..accounting.cost_ledger import normalize_user_sample
from ..integrations.sub2api import (
    Sub2APIReader,
    Sub2APIUsageLog,
    Sub2APIUserUsage,
)
from ..models import (
    AccountParticipant,
    PoolParticipant,
    AppSettings,
    Observation,
    Participant,
    ParticipantBalanceSample,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)

ZERO = Decimal("0")


def fetch_local(
    client: Sub2APIReader,
    config: AppSettings,
    reference: WindowReference,
    memberships: list[AccountParticipant],
    allocations_by_participant_id: dict[int, PoolParticipant],
    now: datetime,
) -> LocalBundle:
    """只读取 Sub2API；数据库写入在最终确认查询窗口后统一执行一次。"""

    location = ZoneInfo(config.timezone)
    start_date = (
        reference.reset_at - timedelta(seconds=reference.window_seconds)
    ).astimezone(location).date()
    end_date = now.astimezone(location).date()
    cost_window_started_at = datetime.combine(
        start_date,
        time.min,
        tzinfo=location,
    ).astimezone(dt_timezone.utc)
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
    for membership in memberships:
        participant = membership.participant
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
                membership=membership,
                allocation=allocations_by_participant_id[participant.id],
                stats=user_usage.stats,
                balance=client.user_balance(participant.sub2api_user_id),
            )
        )
    return LocalBundle(
        total=total,
        participants=rows,
        users=all_users,
        checked_at=now,
        cost_window_started_at=cost_window_started_at,
        cost_window_ended_at=now,
    )


def _same_query_window(left: datetime | None, right: datetime) -> bool:
    return left is not None and left == right


def _log_costs(
    logs: list[Sub2APIUsageLog],
    *,
    started_at: datetime,
    user_id: int | None = None,
) -> tuple[Decimal, Decimal]:
    selected = (
        row
        for row in logs
        if row.created_at >= started_at
        and (user_id is None or row.user_id == user_id)
    )
    standard = ZERO
    actual = ZERO
    for row in selected:
        standard += row.total_cost
        actual += row.actual_cost
    return standard, actual


def fetch_interval_bridge_logs(
    client: Sub2APIReader,
    config: AppSettings,
    reference: WindowReference,
    local: LocalBundle,
    latest_observation: Observation | None,
) -> list[Sub2APIUsageLog] | None:
    """Only query exact logs when cumulative snapshots changed coordinates."""

    starts: list[datetime] = []
    if (
        latest_observation is not None
        and same_official_reset(
            latest_observation.upstream_resets_at,
            reference.reset_at,
        )
        and not _same_query_window(
            latest_observation.cost_window_started_at,
            local.cost_window_started_at,
        )
    ):
        starts.append(latest_observation.observed_at)

    latest_user_sample = (
        Sub2APIUserUsageSample.objects.filter(account_id=reference.account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if (
        latest_user_sample is not None
        and same_official_reset(
            latest_user_sample.window_resets_at,
            reference.reset_at,
        )
        and not _same_query_window(
            latest_user_sample.window_started_at,
            local.cost_window_started_at,
        )
    ):
        starts.append(latest_user_sample.observed_at)

    if not starts:
        return None
    fetch_logs = getattr(client, "usage_logs", None)
    if not callable(fetch_logs):
        return None
    return fetch_logs(
        account_id=reference.account_id,
        started_at=min(starts),
        ended_at=local.cost_window_ended_at,
        timezone_name=config.timezone,
    )


def observation_interval_costs(
    latest: Observation | None,
    reference: WindowReference,
    local: LocalBundle,
    logs: list[Sub2APIUsageLog] | None,
) -> tuple[datetime, Decimal | None, Decimal | None, str]:
    if latest is None or not same_official_reset(
        latest.upstream_resets_at,
        reference.reset_at,
    ):
        return (
            local.cost_window_started_at,
            local.total.total_cost,
            local.total.total_actual_cost,
            "window_total",
        )
    if _same_query_window(
        latest.cost_window_started_at,
        local.cost_window_started_at,
    ):
        return (
            latest.observed_at,
            local.total.total_cost - latest.total_standard_cost,
            local.total.total_actual_cost - latest.total_actual_cost,
            "snapshot_delta",
        )
    if logs is not None:
        standard, actual = _log_costs(
            logs,
            started_at=latest.observed_at,
        )
        return latest.observed_at, standard, actual, "request_logs"
    return latest.observed_at, None, None, "unresolved"


def _latest_user_samples(account_id: int) -> dict[int, Sub2APIUserUsageSample]:
    latest = (
        Sub2APIUserUsageSample.objects.filter(account_id=account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return {}
    return {
        row.sub2api_user_id: row
        for row in Sub2APIUserUsageSample.objects.filter(
            account_id=account_id,
            observed_at=latest.observed_at,
        ).order_by("sub2api_user_id", "id")
    }


def _user_interval(
    user: Sub2APIUserUsage,
    previous: Sub2APIUserUsageSample | None,
    reference: WindowReference,
    local: LocalBundle,
    logs: list[Sub2APIUsageLog] | None,
) -> tuple[datetime, Decimal | None, Decimal | None, str]:
    if previous is None or not same_official_reset(
        previous.window_resets_at,
        reference.reset_at,
    ):
        return (
            local.cost_window_started_at,
            user.stats.total_cost,
            user.stats.total_actual_cost,
            "window_total",
        )
    if _same_query_window(
        previous.window_started_at,
        local.cost_window_started_at,
    ):
        return (
            previous.observed_at,
            user.stats.total_cost - previous.total_standard_cost,
            user.stats.total_actual_cost - previous.total_actual_cost,
            "snapshot_delta",
        )
    if logs is not None:
        standard, actual = _log_costs(
            logs,
            started_at=previous.observed_at,
            user_id=user.user_id,
        )
        return previous.observed_at, standard, actual, "request_logs"
    return previous.observed_at, None, None, "unresolved"


def _participant_baselines(
    latest: Observation | None,
    cost_basis: str,
) -> dict[int, Decimal]:
    if latest is None:
        return {}
    normalized_by_user = {
        row.sub2api_user_id: row.normalized_cost(cost_basis)
        for row in Sub2APIUserUsageSample.objects.filter(
            account_id=latest.account_id,
            observed_at=latest.observed_at,
        )
    }
    return {
        snapshot.participant_id: max(
            ZERO,
            normalized_by_user.get(
                snapshot.participant.sub2api_user_id,
                snapshot.raw_selected_cost,
            )
            - snapshot.selected_cost,
        )
        for snapshot in latest.participant_snapshots.all()
    }


@transaction.atomic
def save_local_bundle(
    config: AppSettings,
    reference: WindowReference,
    local: LocalBundle,
    latest: Observation | None,
    *,
    latest_raw: Observation | None = None,
    interval_logs: list[Sub2APIUsageLog] | None = None,
    capture_started_at: datetime | None = None,
    capture_finished_at: datetime | None = None,
) -> UsageSamplePoint:
    """Commit one complete local fact group and return its canonical point."""

    user_ids = [int(user.user_id) for user in local.users]
    if len(user_ids) != len(set(user_ids)):
        raise ValueError("Sub2API 全量用户快照包含重复用户")
    sum_standard = sum(
        (user.stats.total_cost for user in local.users),
        ZERO,
    )
    sum_actual = sum(
        (user.stats.total_actual_cost for user in local.users),
        ZERO,
    )
    residual_standard = local.total.total_cost - sum_standard
    residual_actual = local.total.total_actual_cost - sum_actual
    (
        account_interval_start,
        account_interval_standard,
        account_interval_actual,
        _account_interval_source,
    ) = observation_interval_costs(
        latest_raw,
        reference,
        local,
        interval_logs,
    )
    point = UsageSamplePoint.objects.create(
        account_id=reference.account_id,
        observed_at=local.checked_at,
        window_started_at=local.cost_window_started_at,
        window_ended_at=local.cost_window_ended_at,
        window_resets_at=reference.reset_at,
        capture_started_at=capture_started_at,
        capture_finished_at=capture_finished_at,
        account_standard_cost=local.total.total_cost,
        account_actual_cost=local.total.total_actual_cost,
        interval_started_at=account_interval_start,
        interval_standard_cost=account_interval_standard,
        interval_actual_cost=account_interval_actual,
        residual_standard_cost=residual_standard,
        residual_actual_cost=residual_actual,
        expected_user_count=len(user_ids),
        expected_user_digest=expected_user_digest(user_ids),
        write_status="complete",
        reconciliation_status=(
            "conflict"
            if residual_standard < ZERO or residual_actual < ZERO
            else (
                "reconciled"
                if residual_standard == ZERO and residual_actual == ZERO
                else "residual"
            )
        ),
        provenance={
            "source": "monitor_capture",
            "multi_request_snapshot": True,
        },
    )

    previous_by_user = _latest_user_samples(reference.account_id)
    user_samples: list[Sub2APIUserUsageSample] = []
    for user in sorted(local.users, key=lambda item: item.user_id):
        previous = previous_by_user.get(user.user_id)
        interval_start, interval_standard, interval_actual, source = (
            _user_interval(
                user,
                previous,
                reference,
                local,
                interval_logs,
            )
        )
        sample = Sub2APIUserUsageSample(
            sample_point=point,
            account_id=reference.account_id,
            sub2api_user_id=user.user_id,
            username=user.username,
            email=user.email,
            observed_at=local.checked_at,
            window_started_at=local.cost_window_started_at,
            window_ended_at=local.cost_window_ended_at,
            window_resets_at=reference.reset_at,
            total_standard_cost=user.stats.total_cost,
            total_actual_cost=user.stats.total_actual_cost,
            interval_started_at=interval_start,
            interval_standard_cost=interval_standard,
            interval_actual_cost=interval_actual,
            interval_source=source,
        )
        normalize_user_sample(sample, previous)
        user_samples.append(sample)
    Sub2APIUserUsageSample.objects.bulk_create(user_samples)

    normalized_by_user = {
        sample.sub2api_user_id: sample.normalized_cost(config.cost_basis)
        for sample in user_samples
    }
    baselines = _participant_baselines(latest, config.cost_basis)
    changed_participants: list[Participant] = []
    changed_memberships: list[AccountParticipant] = []
    usage_samples: list[ParticipantUsageSample] = []
    balance_samples: list[ParticipantBalanceSample] = []
    for row in local.participants:
        raw_cost = row.selected_cost(config.cost_basis)
        normalized_cost = normalized_by_user.get(
            row.participant.sub2api_user_id,
            raw_cost,
        )
        selected_cost = max(
            ZERO,
            normalized_cost - baselines.get(row.participant.pk, ZERO),
        )
        row.participant.latest_balance_usd = row.balance.balance
        row.participant.last_checked_at = local.checked_at
        row.membership.latest_selected_cost = selected_cost
        row.membership.last_checked_at = local.checked_at
        changed_participants.append(row.participant)
        changed_memberships.append(row.membership)
        usage_samples.append(
            ParticipantUsageSample(
                sample_point=point,
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
        balance_samples.append(
            ParticipantBalanceSample(
                point=point,
                participant=row.participant,
                balance_usd=row.balance.balance,
                captured_at=local.checked_at,
                provenance="captured_local",
            )
        )
    if changed_participants:
        Participant.objects.bulk_update(
            changed_participants,
            ["latest_balance_usd", "last_checked_at"],
        )
        AccountParticipant.objects.bulk_update(
            changed_memberships,
            ["latest_selected_cost", "last_checked_at"],
        )
        ParticipantUsageSample.objects.bulk_create(usage_samples)
        ParticipantBalanceSample.objects.bulk_create(balance_samples)
    return point
