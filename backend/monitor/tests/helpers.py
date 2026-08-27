import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from monitor.models import (
    AccountParticipant,
    MonitoredAccount,
    Observation,
    Participant,
    ParticipantSnapshot,
    PoolParticipant,
    QuotaPool,
)


def jwt_login(
    client: Client,
    username: str = "owner",
    password: str = "very-strong-password",
    **extra,
) -> tuple[dict[str, str], object]:
    response = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": username,
                "password": password,
                **extra,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    access = response.json()["data"]["access"]
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}, response


def create_monitored_account(
    external_account_id: int = 7,
    *,
    name: str | None = None,
    quota_query_mode: str = "passive",
    enabled: bool = True,
    quota_profile: str = "auto",
    detected_plan_type: str = "",
    capacity_min_usd_override: Decimal | int | str | None = None,
    capacity_max_usd_override: Decimal | int | str | None = None,
    pool: QuotaPool | None = None,
) -> MonitoredAccount:
    account = MonitoredAccount.objects.filter(
        external_account_id=external_account_id
    ).first()
    values = {
        "name": name or f"OpenAI 账号 {external_account_id}",
        "quota_query_mode": quota_query_mode,
        "enabled": enabled,
        "quota_profile": quota_profile,
        "detected_plan_type": detected_plan_type,
        "capacity_min_usd_override": capacity_min_usd_override,
        "capacity_max_usd_override": capacity_max_usd_override,
    }
    if account is None:
        account = MonitoredAccount.objects.create(
            external_account_id=external_account_id,
            pool=pool
            or QuotaPool.objects.create(
                name=f"{values['name']} 独立池",
            ),
            **values,
        )
    else:
        for field, value in values.items():
            setattr(account, field, value)
        if pool is not None:
            account.pool = pool
        account.save()
    return account


def create_participant(
    *,
    share_percent: Decimal | int | str | None = None,
    is_owner: bool = False,
    account: MonitoredAccount | None = None,
    account_id: int = 7,
    **participant_fields,
) -> Participant:
    participant = Participant.objects.create(
        is_owner=is_owner,
        **participant_fields,
    )
    if share_percent is not None or is_owner or account is not None:
        membership_account = account or create_monitored_account(account_id)
        AccountParticipant.objects.get_or_create(
            account=membership_account,
            participant=participant,
        )
        PoolParticipant.objects.update_or_create(
            pool=membership_account.pool,
            participant=participant,
            defaults={
                "share_percent": (
                    Decimal(str(share_percent))
                    if share_percent is not None
                    else Decimal("0")
                )
            },
        )
    return participant


def account_membership(
    participant: Participant,
    external_account_id: int = 7,
) -> AccountParticipant:
    return participant.account_memberships.get(
        account__external_account_id=external_account_id
    )


def participant_snapshot(
    *,
    observation: Observation,
    participant: Participant,
    share_percent: Decimal | int | str | None = None,
    is_owner: bool | None = None,
    **snapshot_fields,
) -> ParticipantSnapshot:
    membership = (
        AccountParticipant.objects.select_related("account__pool")
        .filter(
            account__external_account_id=observation.account_id,
            participant=participant,
        )
        .first()
    )
    if membership is None:
        raise AssertionError("snapshot fixture requires an account usage row")
    allocation = PoolParticipant.objects.filter(
        pool=membership.account.pool,
        participant=participant,
    ).first()
    if share_percent is None:
        if allocation is None:
            raise AssertionError("snapshot fixture requires a pool allocation")
        share_percent = allocation.share_percent
    if is_owner is None:
        is_owner = participant.is_owner
    recommended = snapshot_fields.get("recommended_balance_usd")
    if recommended is not None:
        snapshot_fields.setdefault("recommended_balance_min_usd", recommended)
        snapshot_fields.setdefault("recommended_balance_max_usd", recommended)
    return ParticipantSnapshot(
        observation=observation,
        participant=participant,
        source_sub2api_user_id=participant.sub2api_user_id,
        share_percent=Decimal(str(share_percent)),
        is_owner=is_owner,
        quota_pool_id=membership.account.pool_id,
        quota_pool_name=membership.account.pool.name,
        pool_contract_revision=membership.account.pool.contract_revision,
        **snapshot_fields,
    )


def create_participant_snapshot(**fields) -> ParticipantSnapshot:
    snapshot = participant_snapshot(**fields)
    snapshot.save(force_insert=True)
    return snapshot


def create_recommendation_snapshot(
    participant: Participant,
    recommended: Decimal = Decimal("123.45"),
) -> ParticipantSnapshot:
    account = create_monitored_account(7)
    membership, _created = AccountParticipant.objects.get_or_create(
        account=account,
        participant=participant,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=4)
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    if participant.latest_balance_usd is None:
        participant.latest_balance_usd = Decimal("80")
        participant.save(update_fields=["latest_balance_usd", "updated_at"])
    allocation = PoolParticipant.objects.get(
        pool=account.pool,
        participant=participant,
    )
    return create_participant_snapshot(
        observation=observation,
        participant=participant,
        share_percent=allocation.share_percent,
        is_owner=participant.is_owner,
        raw_selected_cost=Decimal("200"),
        selected_cost=Decimal("200"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=recommended,
        balance_difference_usd=recommended - Decimal("80"),
        needs_manual_update=True,
    )
