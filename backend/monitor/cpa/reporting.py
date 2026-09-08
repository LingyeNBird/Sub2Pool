"""CPA pool summaries expose peers' totals, never their keys or requests."""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from ..access import visible_accounts_for, visible_participant_ids
from ..models import (
    AppSettings,
    CPAUsageEvent,
    Observation,
    Participant,
    PoolParticipant,
)
from ..reporting.recommendations import _capacity_values
from .account_owner import account_owner_data
from .collector_state import get_collector_status
from .participants import coverage_data, event_owner, owner_index
from .usage import cpa_event_cost

ZERO = Decimal("0")


def _totals():
    return {
        "usage_usd": ZERO,
        "request_count": 0,
        "token_count": 0,
        "unpriced_request_count": 0,
    }


def account_summary(account, config, now, bindings):
    observation = (
        Observation.objects.filter(
            account_id=account.fact_key,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .prefetch_related("participant_snapshots")
        .order_by("-observed_at", "-id")
        .first()
    )
    start = (
        observation.upstream_resets_at - timedelta(seconds=observation.window_seconds)
        if observation
        else account.created_at
    )
    summaries = defaultdict(_totals)
    total = _totals()
    latest_request_at = None
    for event in (
        CPAUsageEvent.objects.filter(
            account=account, occurred_at__gte=start, occurred_at__lte=now
        )
        .order_by("occurred_at", "id")
        .iterator(chunk_size=1000)
    ):
        latest_request_at = event.occurred_at
        cost, unknown = cpa_event_cost(event, config)
        for row in (summaries[event_owner(event, bindings)], total):
            row["usage_usd"] += cost
            row["request_count"] += 1
            row["token_count"] += event.total_tokens
            row["unpriced_request_count"] += int(unknown)
    coverage = coverage_data(
        account, start, observation.observed_at if observation else now
    )
    snapshots = (
        {row.participant_id: row for row in observation.participant_snapshots.all()}
        if observation
        else {}
    )
    return observation, start, summaries, total, coverage, snapshots, latest_request_at


def pool_summary(user, account, config=None):
    config = config or AppSettings.load()
    now = timezone.now()
    mine = visible_participant_ids(user)
    accounts = list(
        visible_accounts_for(user)
        .filter(provider="cpa", pool_id=account.pool_id, enabled=True)
        .order_by("id")
    )
    allocations = list(
        PoolParticipant.objects.filter(pool=account.pool)
        .select_related("participant")
        .order_by("participant_id")
    )
    shares = {row.participant_id: row.share_percent for row in allocations}
    members = {row.participant_id: row.participant for row in allocations}
    if mine is not None:
        for participant in Participant.objects.filter(
            pk__in=mine, cpa_bindings__isnull=False
        ).distinct():
            members.setdefault(participant.id, participant)
    bindings = owner_index()
    by_account = []
    rows = {
        pk: {
            "participant_id": pk,
            "participant_name": member.name,
            "is_owner": member.is_owner,
            "is_self": mine is not None and pk in mine,
            "share_percent": float(shares[pk]) if pk in shares else None,
            **_totals(),
            "expected_entitlement_usd": ZERO,
            "consumed_entitlement_usd": ZERO,
            "remaining_entitlement_usd": ZERO,
            "quota_available": bool(accounts),
            "is_overused": False,
            "_remaining_upper": ZERO,
            "account_breakdowns": [],
        }
        for pk, member in members.items()
    }
    unassigned = _totals()
    for selected in accounts:
        observation, start, totals, total, coverage, snapshots, latest_request_at = (
            account_summary(selected, config, now, bindings)
        )
        observed_at = observation.observed_at if observation else None
        unavailable_reasons = []
        if not observation:
            unavailable_reasons.append("尚无额度观测")
        else:
            if not observation.valid_sample:
                unavailable_reasons.append("额度观测尚不足以估算")
            if observation.upstream_resets_at <= now:
                unavailable_reasons.append("等待新周期的额度观测")
            if observation.interval_used_percent != observation.upstream_used_percent:
                unavailable_reasons.append("本周期用量覆盖不完整")
        if not coverage["complete"]:
            unavailable_reasons.append(
                f"本周期有 {len(coverage['gaps'])} 段采集缺口"
                if coverage["gaps"]
                else "本周期采集覆盖不完整"
            )
        if total["unpriced_request_count"]:
            unavailable_reasons.append(
                f"{total['unpriced_request_count']} 次请求缺少模型价格"
            )
        valid = not unavailable_reasons
        by_account.append(
            {
                "account_id": selected.id,
                "account_name": selected.name,
                "owner": account_owner_data(selected),
                "selected": selected.id == account.id,
                "quota_as_of": observed_at.isoformat() if observed_at else None,
                "requests_as_of": latest_request_at.isoformat()
                if latest_request_at
                else None,
                "cycle_started_at": start.isoformat(),
                "resets_at": observation.upstream_resets_at.isoformat()
                if observation
                else None,
                "coverage": coverage,
                "upstream_used_percent": float(observation.upstream_used_percent) if observation else None,
                "quota_available": valid,
                "quota_unavailable_reasons": unavailable_reasons,
                **{
                    k: float(v) if isinstance(v, Decimal) else v
                    for k, v in total.items()
                },
            }
        )
        for owner, values in totals.items():
            if owner not in rows:
                for key in unassigned:
                    unassigned[key] += values[key]
        for pk, row in rows.items():
            values = totals[pk]
            for key in values:
                row[key] += values[key]
            snapshot = snapshots.get(pk)
            member_reasons = list(unavailable_reasons)
            if pk not in shares:
                member_reasons.append("尚未分配 CPA 份额")
            if not snapshot or not snapshot.cpa_contract_known:
                member_reasons.append("缺少历史份额依据或对应额度观测")
            elif (
                snapshot.quota_pool_id != selected.pool_id
                or snapshot.pool_contract_revision != selected.pool.contract_revision
            ):
                member_reasons.append("份额调整后尚未生成匹配的额度结果")
            available = valid and not member_reasons
            breakdown = {
                "account_id": selected.id,
                "quota_available": available,
                "quota_unavailable_reasons": member_reasons,
                "quota_as_of": observed_at.isoformat() if observed_at else None,
                "charged_percent": None,
                "remaining_share_percent": None,
                "usage_usd": float(values["usage_usd"]),
                "estimated_capacity_usd": None,
                "expected_entitlement_usd": None,
                "consumed_entitlement_usd": None,
                "remaining_entitlement_usd": None,
            }
            if available:
                capacity, capacity_lo, capacity_hi, charged, charged_lo, charged_hi = (
                    _capacity_values(snapshot, config)
                )
                expected = shares[pk] * capacity / 100
                consumed = charged * capacity / 100
                remaining = expected - consumed
                breakdown.update(
                    charged_percent=float(charged),
                    remaining_share_percent=float(max(ZERO, shares[pk] - charged)),
                    estimated_capacity_usd=float(capacity),
                    expected_entitlement_usd=float(expected),
                    consumed_entitlement_usd=float(consumed),
                    remaining_entitlement_usd=float(remaining),
                )
                row["expected_entitlement_usd"] += expected
                row["consumed_entitlement_usd"] += consumed
                row["remaining_entitlement_usd"] += remaining
                row["_remaining_upper"] += max(
                    (shares[pk] - spent) * cap / 100
                    for spent in (charged_lo, charged_hi)
                    for cap in (capacity_lo, capacity_hi)
                )
            else:
                row["quota_available"] = False
            row["account_breakdowns"].append(breakdown)
    for row in rows.values():
        if not row["quota_available"]:
            for key in (
                "expected_entitlement_usd",
                "consumed_entitlement_usd",
                "remaining_entitlement_usd",
            ):
                row[key] = None
            row["is_overused"] = False
        else:
            # Mixed-pool credits offset deficits, as in existing pool accounting.
            row["is_overused"] = row["_remaining_upper"] < ZERO
        row.pop("_remaining_upper")
        for key, value in list(row.items()):
            if isinstance(value, Decimal):
                row[key] = float(value)
    from .billing import billing_summary, weekly_distribution

    return {
        "weekly_distribution": weekly_distribution(accounts, members, config, now, bindings),
        "billing_summary": billing_summary(user, account, config, now, bindings, members),
        "pool_id": account.pool_id,
        "pool_name": account.pool.name,
        "selected_account_id": account.id,
        "partial_scope": len(accounts)
        != account.pool.accounts.filter(enabled=True).count(),
        "accounts": by_account,
        "members": list(rows.values()),
        "unattributed": {
            k: float(v) if isinstance(v, Decimal) else v for k, v in unassigned.items()
        },
        "collector": get_collector_status(include_error=user.is_staff),
        "cost_estimate": True,
        "enforcement_enabled": False,
        "generated_at": now.isoformat(),
    }
