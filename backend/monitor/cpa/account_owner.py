"""Account-scoped fallback ownership and explicit claims of collected requests."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..fact_utils import canonical_digest
from ..history_state import fenced_fact_write
from ..models import (
    AppSettings,
    CPAAccountOwnerBinding,
    CPAClaimEvent,
    CPAClaimPlan,
    CPAKeyBinding,
    CPAQuotaContract,
    CPAUsageEvent,
    Observation,
    Participant,
)
from .participants import cpa_accounts, coverage_data, ownership_filter
from .usage import cpa_event_cost


def sync_account_owner(account, effective_at=None):
    """Called under a fact-write fence when roles or pool membership change."""
    owners = list(
        account.pool.allocations.filter(
            participant__is_owner=True, participant__enabled=True
        ).values_list("participant_id", flat=True)
    )
    participant_id = owners[0] if len(owners) == 1 else None
    previous = account.cpa_owner_bindings.filter(ended_at__isnull=True).first()
    if previous and previous.participant_id == participant_id:
        return previous
    now = effective_at or timezone.now()
    if previous:
        now = max(now, previous.started_at + timedelta(microseconds=1))
        previous.ended_at = now
        previous.save(update_fields=["ended_at"])
    if participant_id is not None:
        return CPAAccountOwnerBinding.objects.create(
            account=account, participant_id=participant_id, started_at=now
        )
    return None


def account_owner_data(account):
    binding = (
        account.cpa_owner_bindings.filter(ended_at__isnull=True)
        .select_related("participant")
        .first()
    )
    if binding:
        return {
            "participant_id": binding.participant_id,
            "participant_name": binding.participant.name,
            "started_at": binding.started_at.isoformat(),
            "status": "active",
        }
    count = account.pool.allocations.filter(
        participant__is_owner=True, participant__enabled=True
    ).count()
    return {
        "participant_id": None,
        "participant_name": None,
        "started_at": None,
        "status": "ambiguous" if count > 1 else "missing",
    }


def unassigned_events(account, start, end):
    return (
        CPAUsageEvent.objects.filter(
            account=account, occurred_at__gte=start, occurred_at__lt=end
        )
        .exclude(ownership_filter(Participant.objects.values_list("pk", flat=True)))
        .order_by("id")
    )


def source_digest(account, start, end, config):
    # Include raw facts and all ownership evidence, even if a concurrent change
    # leaves the aggregate request count unchanged.
    return canonical_digest(
        {
            "events": list(
                CPAUsageEvent.objects.filter(
                    account=account, occurred_at__gte=start, occurred_at__lt=end
                )
                .order_by("id")
                .values()
            ),
            "keys": list(CPAKeyBinding.objects.order_by("id").values()),
            "owners": list(CPAAccountOwnerBinding.objects.order_by("id").values()),
            "claims": list(CPAClaimEvent.objects.order_by("id").values()),
            "settings": AppSettings.objects.filter(pk=config.pk).values().get(),
            "contracts": list(
                CPAQuotaContract.objects.filter(account=account).order_by("id").values()
            ),
            "pool": [account.pool_id, account.pool.contract_revision],
            "observations": list(
                Observation.objects.filter(account_id=account.fact_key)
                .order_by("id")
                .values()
            ),
            "coverage": list(account.cpa_collection_intervals.order_by("id").values()),
        }
    )


def preview_unassigned_claim(
    account, participant, user, started_at=None, ended_at=None
):
    with fenced_fact_write([a.fact_key for a in cpa_accounts()]):
        now = timezone.now()
        earliest = (
            CPAUsageEvent.objects.filter(account=account)
            .order_by("occurred_at")
            .values_list("occurred_at", flat=True)
            .first()
        )
        start = started_at or earliest or account.created_at
        end = ended_at or now
        if start >= end or end > now:
            raise ValidationError("历史范围必须为过去的有效起止时间")
        current = account.cpa_owner_bindings.filter(ended_at__isnull=True).first()
        if (
            current is None
            or current.participant_id != participant.pk
            or not participant.enabled
            or not account.pool.allocations.filter(participant=participant).exists()
        ):
            raise ValidationError("请选择此 CPA 池内已分配份额的参与者")
        config = AppSettings.load()
        events = list(unassigned_events(account, start, end))
        if not events:
            raise ValidationError("此范围没有未归属的已采集请求")
        total = Decimal("0")
        unpriced = 0
        for event in events:
            cost, unknown = cpa_event_cost(event, config)
            total += cost
            unpriced += int(unknown)
        return CPAClaimPlan.objects.create(
            account=account,
            participant=participant,
            started_at=start,
            ended_at=end,
            created_by=user,
            expires_at=now + timedelta(minutes=15),
            source_digest=source_digest(account, start, end, config),
            preview={
                "accounts": [
                    {
                        "account_id": account.pk,
                        "account_name": account.name,
                        "request_count": len(events),
                        "token_count": sum(e.total_tokens for e in events),
                        "usage_usd": float(total),
                        "unpriced_request_count": unpriced,
                        "coverage": coverage_data(account, start, end),
                    }
                ],
                "historical_contract_policy": "仅认领已采集且尚无归属的请求，不补造断线数据或历史份额",
            },
        )


def apply_unassigned_claim(plan_id):
    from ..replay import rebuild_account

    with fenced_fact_write([a.fact_key for a in cpa_accounts()]) as guards:
        plan = (
            CPAClaimPlan.objects.select_for_update()
            .select_related("account__pool")
            .get(pk=plan_id)
        )
        if plan.applied_at is not None:
            return plan
        config = AppSettings.load()
        if plan.expires_at <= timezone.now() or plan.source_digest != source_digest(
            plan.account, plan.started_at, plan.ended_at, config
        ):
            raise ValidationError("认领预览已过期或数据已变化，请重新预览")
        events = list(unassigned_events(plan.account, plan.started_at, plan.ended_at))
        CPAClaimEvent.objects.bulk_create(
            [CPAClaimEvent(plan=plan, event=e) for e in events], batch_size=500
        )
        rebuild_account(
            plan.account.fact_key, config, guard=guards[plan.account.fact_key]
        )
        plan.applied_at = timezone.now()
        plan.save(update_fields=["applied_at"])
        return plan
