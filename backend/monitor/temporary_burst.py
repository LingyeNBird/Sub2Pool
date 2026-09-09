"""Global temporary balance override with zero-sum, per-account cycle credits."""

from datetime import timedelta
from decimal import Decimal, ROUND_DOWN

from django.db import transaction
from django.utils import timezone

from .accounting.boundaries import (
    RESET_TIME_TOLERANCE,
    official_reset_advanced,
)
from .history_state import LeaseGuard
from .models import (
    AppSettings,
    MonitoredAccount,
    Observation,
    ParticipantSnapshot,
    PoolParticipant,
)
from .models.temporary_burst import TemporaryBurstCycle, TemporaryBurstSession

ZERO = Decimal("0")
PRECISION = Decimal("0.00001")
BURST_BALANCE = Decimal("9999.00")


def _apportion(total, weights):
    result = {key: ZERO for key in weights}
    denominator = sum(weights.values(), ZERO)
    if total <= ZERO or denominator <= ZERO:
        return result
    active = [(key, value) for key, value in sorted(weights.items()) if value > ZERO]
    remainder = total
    for index, (key, value) in enumerate(active):
        amount = (
            remainder
            if index == len(active) - 1
            else (total * value / denominator).quantize(PRECISION, rounding=ROUND_DOWN)
        )
        result[key] = amount
        remainder -= amount
    return result


def settle_percentages(entitlements, usage):
    """Transfer only borrowed rights; unused, unborrowed quota expires."""
    unused = {key: max(ZERO, value - usage[key]) for key, value in entitlements.items()}
    excess = {key: max(ZERO, usage[key] - value) for key, value in entitlements.items()}
    borrowed = min(sum(unused.values(), ZERO), sum(excess.values(), ZERO)).quantize(
        PRECISION, rounding=ROUND_DOWN
    )
    credits = _apportion(borrowed, unused)
    debts = _apportion(borrowed, excess)
    return {key: credits[key] - debts[key] for key in entitlements}


def active_session(config=None):
    config = config or AppSettings.load()
    return (
        TemporaryBurstSession.objects.filter(
            ended_at__isnull=True,
            expires_at__gt=timezone.now(),
            base_url=config.sub2api_base_url.rstrip("/"),
        )
        .order_by("-id")
        .first()
    )


def cycle_for(account, observation):
    return (
        TemporaryBurstCycle.objects.filter(
            account=account,
            resets_at__gte=observation.upstream_resets_at - RESET_TIME_TOLERANCE,
            resets_at__lte=observation.upstream_resets_at + RESET_TIME_TOLERANCE,
        )
        .order_by("-id")
        .first()
    )


def adjustment_for(cycle, participant):
    if cycle is None:
        return ZERO
    member = next(
        (
            row
            for row in cycle.members
            if row["participant_id"] == participant.id
            and row["user_id"] == participant.sub2api_user_id
        ),
        None,
    )
    return Decimal(member["opening_adjustment"]) if member else ZERO


def _members(account, credits=None):
    credits = credits or {}
    return [
        {
            "participant_id": allocation.participant_id,
            "user_id": allocation.participant.sub2api_user_id,
            "name": allocation.participant.name,
            "base_share": str(allocation.share_percent),
            "opening_adjustment": str(credits.get(allocation.participant_id, ZERO)),
            "pool_id": allocation.pool_id,
            "contract_revision": allocation.pool.contract_revision,
        }
        for allocation in PoolParticipant.objects.select_related("participant", "pool")
        .filter(
            pool_id=account.pool_id,
            participant__enabled=True,
            share_percent__gt=ZERO,
        )
        .order_by("participant_id")
    ]


def _latest(account):
    return (
        Observation.objects.filter(
            account_id=account.fact_key,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("-observed_at", "-id")
        .first()
    )


def _cycle_snapshots(cycle):
    identities = {row["participant_id"]: row["user_id"] for row in cycle.members}
    latest_by_segment = {}
    rows = (
        ParticipantSnapshot.objects.select_related("observation")
        .filter(
            observation__account_id=cycle.account.fact_key,
            observation__excluded_at__isnull=True,
            observation__attribution_started_at__isnull=False,
            observation__upstream_resets_at__gte=cycle.resets_at - RESET_TIME_TOLERANCE,
            observation__upstream_resets_at__lte=cycle.resets_at + RESET_TIME_TOLERANCE,
            participant_id__in=identities,
        )
        .order_by("observation__observed_at", "id")
    )
    for snapshot in rows:
        if snapshot.source_sub2api_user_id == identities[snapshot.participant_id]:
            latest_by_segment[
                (snapshot.participant_id, snapshot.observation.attribution_started_at)
            ] = snapshot
    return latest_by_segment


def _charged(snapshot, quota_model):
    from .reporting.recommendations import _constant_average_charged

    return max(
        ZERO,
        _constant_average_charged(snapshot)
        if quota_model == "constant_average"
        else snapshot.charged_cycle_percent,
    )


def prior_cycle_usage(cycle, participant, current_segment):
    if cycle is None:
        return ZERO
    return sum(
        (
            _charged(snapshot, cycle.quota_model)
            for (participant_id, segment), snapshot in _cycle_snapshots(cycle).items()
            if participant_id == participant.id
            and segment != current_segment
            and snapshot.source_sub2api_user_id == participant.sub2api_user_id
        ),
        ZERO,
    )


def _cycle_usage(cycle):
    identities = {row["participant_id"]: row["user_id"] for row in cycle.members}
    latest_by_segment = _cycle_snapshots(cycle)
    if set(key[0] for key in latest_by_segment) != set(identities):
        raise ValueError("旧周期缺少参与者归属证据，保留待结算状态，不猜测消耗")
    usage = {key: ZERO for key in identities}
    for (participant_id, _segment), snapshot in latest_by_segment.items():
        usage[participant_id] += _charged(snapshot, cycle.quota_model)
    return usage, max(row.observation.observed_at for row in latest_by_segment.values())


@transaction.atomic
def reconcile_account(account, observation, config):
    """Called under the existing account/global lease after live replay, never from a GET."""
    if observation is None or observation.excluded_at is not None:
        return
    pending = list(
        TemporaryBurstCycle.objects.select_for_update()
        .select_related("session", "account")
        .filter(account=account, settled_at__isnull=True)
        .order_by("resets_at", "id")
    )
    for cycle in pending:
        if not official_reset_advanced(observation.upstream_resets_at, cycle.resets_at):
            continue
        if cycle.is_burst_cycle:
            TemporaryBurstSession.objects.filter(
                pk=cycle.session_id, ended_at__isnull=True
            ).update(ended_at=timezone.now())
        if cycle.session.base_url != config.sub2api_base_url.rstrip("/"):
            cycle.error = "Sub2API 连接已变化，不能跨服务结算旧权益"
            cycle.save(update_fields=["error"])
            continue
        try:
            usage, evidence_at = _cycle_usage(cycle)
        except ValueError as exc:
            cycle.error = str(exc)
            cycle.save(update_fields=["error"])
            continue
        rights = {
            row["participant_id"]: Decimal(row["base_share"])
            + Decimal(row["opening_adjustment"])
            for row in cycle.members
        }
        credits = settle_percentages(rights, usage)
        next_members = _members(account, credits)
        next_users = {row["participant_id"]: row["user_id"] for row in next_members}
        if any(
            credits[row["participant_id"]]
            and next_users.get(row["participant_id"]) != row["user_id"]
            for row in cycle.members
        ):
            cycle.error = "有未结清权益的参与者已停用、移除或更换绑定，保留待结算账目"
            cycle.save(update_fields=["error"])
            continue
        if (
            observation.upstream_resets_at - cycle.resets_at
            > timedelta(seconds=observation.window_seconds) + RESET_TIME_TOLERANCE
        ):
            cycle.error = "缺少紧邻的下一周期观测，不能把过期结转自动挪到更晚周期"
            cycle.save(update_fields=["error"])
            continue
        cycle.settlement = [
            {
                **row,
                "effective_share": str(rights[row["participant_id"]]),
                "used_percent": str(usage[row["participant_id"]]),
                "next_adjustment": str(credits[row["participant_id"]]),
            }
            for row in cycle.members
        ]
        cycle.settled_at = timezone.now()
        cycle.evidence_at = evidence_at
        cycle.error = ""
        cycle.save(update_fields=["settlement", "settled_at", "evidence_at", "error"])
        if any(credits.values()):
            # The identity check above prevents dropping one side of the transfer.
            TemporaryBurstCycle.objects.get_or_create(
                account=account,
                resets_at=observation.upstream_resets_at,
                defaults={
                    "session": cycle.session,
                    "quota_model": config.weekly_quota_model,
                    "members": next_members,
                },
            )


def start_session():
    guard = LeaseGuard.acquire(0)
    try:
        with transaction.atomic():
            guard.assert_owned()
            config = AppSettings.load()
            if not config.sub2api_admin_token_encrypted:
                raise ValueError("请先配置 Sub2API 管理连接")
            if active_session(config):
                raise ValueError("临时爽蹬已开启")
            accounts = list(
                MonitoredAccount.objects.filter(enabled=True, provider="sub2api")
                .select_related("pool")
                .order_by("id")
            )
            if not accounts:
                raise ValueError("没有启用的 Sub2API 账号")
            captured = []
            participant_users = {}
            now = timezone.now()
            for account in accounts:
                observation = _latest(account)
                if (
                    observation is None
                    or observation.upstream_resets_at <= now
                    or now - observation.observed_at
                    > timedelta(hours=config.stale_warning_hours)
                ):
                    raise ValueError(f"{account.name} 缺少当前周期的新鲜测算，请先采样")
                reconcile_account(account, observation, config)
                members = _members(account)
                if not members:
                    raise ValueError(f"{account.name} 所在额度池没有有效参与者")
                if observation.effective_usd_per_percent <= ZERO:
                    raise ValueError(f"{account.name} 尚无可用的额度换算依据")
                snapshots = {
                    row.participant_id: row.source_sub2api_user_id
                    for row in observation.participant_snapshots.all()
                }
                if any(
                    snapshots.get(row["participant_id"]) != row["user_id"]
                    for row in members
                ):
                    raise ValueError(f"{account.name} 的参与者归属尚未采样完整")
                participant_users.update(
                    {str(row["participant_id"]): row["user_id"] for row in members}
                )
                existing = cycle_for(account, observation)
                if existing and existing.is_burst_cycle:
                    raise ValueError(f"{account.name} 本周期已经开启过临时爽蹬")
                captured.append((account, observation, members, existing))
            if TemporaryBurstCycle.objects.filter(
                is_burst_cycle=True, settled_at__isnull=True
            ).exists():
                raise ValueError("上一轮仍有账号等待周期结束结算，不能重复开启")
            session = TemporaryBurstSession.objects.create(
                started_at=now,
                expires_at=min(row[1].upstream_resets_at for row in captured),
                participant_users=participant_users,
                base_url=config.sub2api_base_url.rstrip("/"),
            )
            for account, observation, members, existing in captured:
                if existing:
                    for row in members:
                        previous = next(
                            (
                                item
                                for item in existing.members
                                if item["participant_id"] == row["participant_id"]
                                and item["user_id"] == row["user_id"]
                            ),
                            None,
                        )
                        row["opening_adjustment"] = (
                            previous["opening_adjustment"] if previous else "0"
                        )
                    existing.session = session
                    existing.members = members
                    existing.is_burst_cycle = True
                    existing.save(
                        update_fields=["session", "members", "is_burst_cycle"]
                    )
                else:
                    TemporaryBurstCycle.objects.create(
                        session=session,
                        account=account,
                        resets_at=observation.upstream_resets_at,
                        quota_model=config.weekly_quota_model,
                        members=members,
                        is_burst_cycle=True,
                    )
            return session
    finally:
        guard.release()


def burst_payload():
    config = AppSettings.load()
    session = TemporaryBurstSession.objects.order_by("-id").first()
    active = active_session(config)
    cycles = (
        list(
            session.cycles.select_related("account").order_by("account_id", "resets_at")
        )
        if session
        else []
    )
    pending = TemporaryBurstCycle.objects.filter(
        is_burst_cycle=True, settled_at__isnull=True
    ).exists()
    return {
        "active": active is not None,
        "session_id": session.pk if session else None,
        "started_at": session.started_at if session else None,
        "expires_at": session.expires_at if session else None,
        "ended_at": (
            session.ended_at
            or (session.expires_at if session.expires_at <= timezone.now() else None)
        )
        if session
        else None,
        "auto_apply": config.auto_apply_recommendations,
        "monitoring_enabled": config.monitoring_enabled,
        "recommended_balance_usd": float(BURST_BALANCE),
        "can_start": active is None and not pending,
        "cycles": [
            {
                "account_id": row.account_id,
                "account_name": row.account.name,
                "resets_at": row.resets_at,
                "is_burst_cycle": row.is_burst_cycle,
                "settled_at": row.settled_at,
                "evidence_at": row.evidence_at,
                "error": row.error,
                "members": row.members,
                "settlement": row.settlement,
            }
            for row in cycles
        ],
    }
