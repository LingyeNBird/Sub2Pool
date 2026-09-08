"""Time-scoped CPA ownership, explicit claims, and account projections."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..fact_utils import canonical_digest
from ..history_state import fenced_fact_write
from ..models import (
    AccountParticipant,
    AppSettings,
    CPAAPIKey,
    CPAClaimPlan,
    CPAClaimEvent,
    CPAKeyBinding,
    CPAQuotaContract,
    CPAUsageEvent,
    MonitoredAccount,
    Observation,
    ParticipantSnapshot,
    ParticipantUsageSample,
)
from .usage import _api_key_identity, cpa_event_cost

ZERO = Decimal("0")


def ownership_filter(participant_ids):
    """Filter events by immutable ownership intervals, never by current owner."""
    result = Q(pk__in=[])
    for binding in CPAKeyBinding.objects.filter(
        participant_id__in=participant_ids
    ).select_related("key"):
        if binding.claim_id is not None:
            result |= Q(ownership_claim__plan_id=binding.claim_id)
            continue
        condition = Q(
            api_key_hash=binding.key.key_hash, occurred_at__gte=binding.started_at
        )
        if binding.ended_at is not None:
            condition &= Q(occurred_at__lt=binding.ended_at)
        result |= condition
    return result


def owner_index():
    result = defaultdict(list)
    claimed = defaultdict(set)
    for plan_id, event_id in CPAClaimEvent.objects.values_list("plan_id", "event_id"):
        claimed[plan_id].add(event_id)
    for binding in CPAKeyBinding.objects.select_related("key").order_by(
        "started_at", "id"
    ):
        binding.claimed_event_ids = (
            claimed[binding.claim_id] if binding.claim_id else None
        )
        result[binding.key.key_hash].append(binding)
    return result


def event_owner(event, bindings):
    for binding in reversed(bindings.get(event.api_key_hash, ())):
        if (
            binding.claimed_event_ids is not None
            and event.pk not in binding.claimed_event_ids
        ):
            continue
        if binding.started_at <= event.occurred_at and (
            binding.ended_at is None or event.occurred_at < binding.ended_at
        ):
            return binding.participant_id
    return None


def cpa_accounts():
    return MonitoredAccount.objects.filter(provider="cpa").order_by("id")


def register_key(*, raw_key="", observed_hash="", name=""):
    if raw_key:
        digest, hint = _api_key_identity(raw_key)
    else:
        existing = CPAAPIKey.objects.filter(key_hash=observed_hash).first()
        if existing is not None:
            if name and existing.name != name:
                existing.name = name
                existing.save(update_fields=["name"])
            return existing
        event = (
            CPAUsageEvent.objects.filter(api_key_hash=observed_hash)
            .exclude(api_key_hash="")
            .first()
        )
        if event is None:
            raise ValidationError("该 Key 尚未采集，请输入完整 Key 预先绑定")
        digest, hint = event.api_key_hash, event.api_key_hint
    if not digest:
        raise ValidationError("Key 不能为空")
    key, _ = CPAAPIKey.objects.get_or_create(
        key_hash=digest, defaults={"hint": hint, "name": name}
    )
    if name and key.name != name:
        key.name = name
        key.save(update_fields=["name"])
    return key


def assert_available(key, started_at, ended_at=None, claiming_participant_id=None):
    rows = key.bindings.filter(Q(ended_at__isnull=True) | Q(ended_at__gt=started_at))
    if ended_at is not None:
        rows = rows.filter(started_at__lt=ended_at)
    if claiming_participant_id is not None:
        rows = rows.exclude(claim__isnull=False, participant_id=claiming_participant_id)
    if rows.exists():
        raise ValidationError("此时间范围已有 Key 归属，请调整范围或先解绑当前归属")


def bind_key(*, participant, user, raw_key="", observed_hash="", name=""):
    with fenced_fact_write([a.fact_key for a in cpa_accounts()]):
        key = register_key(raw_key=raw_key, observed_hash=observed_hash, name=name)
        started_at = timezone.now()
        assert_available(key, started_at)
        return CPAKeyBinding.objects.create(
            key=key, participant=participant, started_at=started_at, created_by=user
        )


def unbind_key(binding_id):
    with fenced_fact_write([a.fact_key for a in cpa_accounts()]):
        binding = CPAKeyBinding.objects.select_for_update().get(pk=binding_id)
        if binding.ended_at is None:
            binding.ended_at = max(
                timezone.now(), binding.started_at + timedelta(microseconds=1)
            )
            binding.save(update_fields=["ended_at"])
        return binding


def coverage_data(account, started_at, ended_at):
    intervals = list(
        account.cpa_collection_intervals.filter(connected_at__lte=ended_at)
        .filter(Q(disconnected_at__isnull=True) | Q(disconnected_at__gte=started_at))
        .order_by("connected_at", "id")
    )
    cursor = started_at
    gaps = []
    for interval in intervals:
        if interval.connected_at > cursor:
            gaps.append(
                {
                    "started_at": cursor.isoformat(),
                    "ended_at": min(interval.connected_at, ended_at).isoformat(),
                }
            )
        cursor = max(cursor, min(interval.disconnected_at or ended_at, ended_at))
    if cursor < ended_at:
        gaps.append(
            {"started_at": cursor.isoformat(), "ended_at": ended_at.isoformat()}
        )
    uncertain_end = any(
        row.disconnected_at is not None
        and not row.end_reliable
        and started_at < row.disconnected_at <= ended_at
        for row in intervals
    )
    return {
        "complete": not gaps and not uncertain_end,
        "gaps": gaps,
        "uncertain_end": uncertain_end,
    }


def _claim_events(key, start, end):
    return CPAUsageEvent.objects.filter(
        api_key_hash=key.key_hash,
        occurred_at__gte=start,
        occurred_at__lt=end,
        ownership_claim__isnull=True,
    ).order_by("id")


def claim_digest(key, start, end, config):
    accounts = list(cpa_accounts())
    return canonical_digest(
        {
            "events": list(_claim_events(key, start, end).values()),
            "bindings": list(key.bindings.order_by("id").values()),
            "settings": AppSettings.objects.filter(pk=config.pk).values().get(),
            "accounts": [(a.id, a.pool_id, a.pool.contract_revision) for a in accounts],
            "contracts": list(CPAQuotaContract.objects.order_by("id").values()),
            "observations": list(
                Observation.objects.filter(
                    account_id__in=[a.fact_key for a in accounts]
                )
                .order_by("id")
                .values(
                    "id",
                    "observed_at",
                    "upstream_used_percent",
                    "upstream_resets_at",
                    "excluded_at",
                    "is_manual_start",
                    "manual_start_end_id",
                )
            ),
            "coverage": [
                list(a.cpa_collection_intervals.order_by("id").values())
                for a in accounts
            ],
        }
    )


def preview_claim(*, key, participant, started_at, ended_at, user):
    if started_at >= ended_at or ended_at > timezone.now():
        raise ValidationError("历史范围必须为过去的有效起止时间")
    with fenced_fact_write([a.fact_key for a in cpa_accounts()]):
        assert_available(key, started_at, ended_at, participant.pk)
        config = AppSettings.load()
        groups = {}
        for event in _claim_events(key, started_at, ended_at).select_related("account"):
            row = groups.setdefault(
                event.account_id,
                {
                    "account_id": event.account_id,
                    "account_name": event.account.name,
                    "request_count": 0,
                    "token_count": 0,
                    "usage_usd": ZERO,
                    "unpriced_request_count": 0,
                    "coverage": coverage_data(event.account, started_at, ended_at),
                },
            )
            cost, unknown = cpa_event_cost(event, config)
            row["request_count"] += 1
            row["token_count"] += event.total_tokens
            row["usage_usd"] += cost
            row["unpriced_request_count"] += int(unknown)
        if not groups:
            raise ValidationError("此范围没有已采集请求")
        for row in groups.values():
            row["usage_usd"] = float(row["usage_usd"])
        return CPAClaimPlan.objects.create(
            key=key,
            participant=participant,
            started_at=started_at,
            ended_at=ended_at,
            created_by=user,
            expires_at=timezone.now() + timedelta(minutes=15),
            source_digest=claim_digest(key, started_at, ended_at, config),
            preview={
                "accounts": list(groups.values()),
                "historical_contract_policy": "缺少历史份额时只认领请求与消耗，剩余权益保持未知",
            },
        )


def apply_claim(plan_id):
    from ..replay import rebuild_account

    with fenced_fact_write([a.fact_key for a in cpa_accounts()]) as guards:
        plan = CPAClaimPlan.objects.select_for_update().get(pk=plan_id)
        if plan.applied_at is not None:
            return plan
        config = AppSettings.load()
        if plan.expires_at <= timezone.now() or plan.source_digest != claim_digest(
            plan.key, plan.started_at, plan.ended_at, config
        ):
            raise ValidationError("认领预览已过期或数据已变化，请重新预览")
        assert_available(plan.key, plan.started_at, plan.ended_at, plan.participant_id)
        events = list(
            _claim_events(plan.key, plan.started_at, plan.ended_at).values_list(
                "id", "account_id"
            )
        )
        CPAKeyBinding.objects.create(
            key=plan.key,
            participant=plan.participant,
            started_at=plan.started_at,
            ended_at=plan.ended_at,
            created_by=plan.created_by,
            claim=plan,
        )
        CPAClaimEvent.objects.bulk_create(
            [CPAClaimEvent(plan=plan, event_id=event_id) for event_id, _ in events],
            batch_size=500,
        )
        account_ids = {account_id for _, account_id in events}
        for account in cpa_accounts().filter(pk__in=account_ids):
            rebuild_account(account.fact_key, config, guard=guards[account.fact_key])
        plan.applied_at = timezone.now()
        plan.save(update_fields=["applied_at"])
        return plan


def record_contract(account, effective_at):
    allocations = [
        {"participant_id": row.participant_id, "share_percent": str(row.share_percent)}
        for row in account.pool.allocations.order_by("participant_id")
    ]
    previous = account.cpa_contracts.order_by("-effective_at", "-id").first()
    if (
        previous
        and previous.pool_id_at_capture == account.pool_id
        and previous.revision == account.pool.contract_revision
        and previous.allocations == allocations
    ):
        return previous
    return CPAQuotaContract.objects.create(
        account=account,
        effective_at=effective_at,
        pool_id_at_capture=account.pool_id,
        pool_name=account.pool.name,
        revision=account.pool.contract_revision,
        allocations=allocations,
    )


def materialize_participants(account, config):
    """Rebuild participant inputs from events and frozen policy before model replay."""
    bindings = owner_index()
    prefixes = defaultdict(lambda: [[], [ZERO]])
    for event in (
        CPAUsageEvent.objects.filter(account=account)
        .order_by("occurred_at", "id")
        .iterator(chunk_size=1000)
    ):
        owner = event_owner(event, bindings)
        if owner is None:
            continue
        times, sums = prefixes[owner]
        times.append(event.occurred_at)
        sums.append(sums[-1] + cpa_event_cost(event, config)[0])
    contracts = list(account.cpa_contracts.order_by("effective_at", "id"))
    contract_times = [row.effective_at for row in contracts]
    observations = list(
        Observation.objects.filter(account_id=account.fact_key).order_by(
            "observed_at", "id"
        )
    )
    snapshots = {
        (row.observation_id, row.participant_id): row
        for row in ParticipantSnapshot.objects.filter(
            observation__account_id=account.fact_key
        )
    }
    samples = {
        (row.observed_at, row.participant_id): row
        for row in ParticipantUsageSample.objects.filter(account_id=account.fact_key)
    }
    new_snapshots, changed_snapshots, new_samples, changed_samples = [], [], [], []
    snapshot_fields = [
        "raw_selected_cost",
        "share_percent",
        "cpa_contract_known",
        "quota_pool_id",
        "quota_pool_name",
        "pool_contract_revision",
    ]
    member_ids = set()
    for observation in observations:
        index = bisect_right(contract_times, observation.observed_at) - 1
        contract = contracts[index] if index >= 0 else None
        shares = (
            {
                row["participant_id"]: Decimal(row["share_percent"])
                for row in contract.allocations
            }
            if contract
            else {}
        )
        start = observation.cost_window_started_at or (
            observation.upstream_resets_at
            - timedelta(seconds=observation.window_seconds)
        )
        for participant_id in set(prefixes) | set(shares):
            times, sums = prefixes[participant_id]
            if participant_id not in shares and (
                not times or times[0] > observation.observed_at
            ):
                continue
            member_ids.add(participant_id)
            cost = (
                sums[bisect_right(times, observation.observed_at)]
                - sums[bisect_left(times, start)]
            )
            values = {
                "raw_selected_cost": cost,
                "share_percent": shares.get(participant_id, ZERO),
                "cpa_contract_known": participant_id in shares,
                "quota_pool_id": contract.pool_id_at_capture if contract else None,
                "quota_pool_name": contract.pool_name if contract else "",
                "pool_contract_revision": contract.revision if contract else None,
            }
            snapshot = snapshots.get((observation.id, participant_id))
            if snapshot is None:
                new_snapshots.append(
                    ParticipantSnapshot(
                        observation=observation,
                        participant_id=participant_id,
                        selected_cost=cost,
                        **values,
                    )
                )
            elif any(
                getattr(snapshot, field) != value for field, value in values.items()
            ):
                for field, value in values.items():
                    setattr(snapshot, field, value)
                changed_snapshots.append(snapshot)
            sample = samples.get((observation.observed_at, participant_id))
            if sample is None:
                new_samples.append(
                    ParticipantUsageSample(
                        participant_id=participant_id,
                        account_id=account.fact_key,
                        observed_at=observation.observed_at,
                        sample_point_id=observation.sample_point_id,
                        raw_selected_cost=cost,
                        selected_cost=cost,
                        balance_usd=None,
                    )
                )
            elif sample.raw_selected_cost != cost:
                sample.raw_selected_cost = cost
                changed_samples.append(sample)
    ParticipantSnapshot.objects.bulk_create(new_snapshots, batch_size=500)
    ParticipantSnapshot.objects.bulk_update(
        changed_snapshots, snapshot_fields, batch_size=500
    )
    ParticipantUsageSample.objects.bulk_create(new_samples, batch_size=500)
    ParticipantUsageSample.objects.bulk_update(
        changed_samples, ["raw_selected_cost"], batch_size=500
    )
    existing = set(account.memberships.values_list("participant_id", flat=True))
    AccountParticipant.objects.bulk_create(
        [
            AccountParticipant(account=account, participant_id=pk)
            for pk in member_ids - existing
        ],
        ignore_conflicts=True,
    )


def has_cpa_contract_history(participant_id):
    return any(
        any(row["participant_id"] == participant_id for row in allocations)
        for allocations in CPAQuotaContract.objects.values_list(
            "allocations", flat=True
        ).iterator()
    )
