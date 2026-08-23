from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from rest_framework import serializers

from monitor.fact_utils import expected_user_digest
from monitor.history_state import LeaseGuard
from monitor.models import (
    AppSettings,
    HistoryMaintenanceState,
    Observation,
    Participant,
    ParticipantSnapshot,
    PoolParticipant,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
    jwt_login,
)
from monitor.serializers import ParticipantWriteSerializer


@pytest.mark.django_db
def test_participant_crud_never_invents_or_rewrites_historical_membership():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    observed_at = timezone.now().replace(microsecond=0) - timedelta(days=30)
    window_started_at = observed_at - timedelta(hours=2)
    reset_at = observed_at + timedelta(days=6)
    point = UsageSamplePoint.objects.create(
        account_id=7,
        observed_at=observed_at,
        window_started_at=window_started_at,
        window_ended_at=observed_at,
        window_resets_at=reset_at,
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=Decimal("20"),
        account_actual_cost=Decimal("20"),
        interval_started_at=window_started_at,
        interval_standard_cost=Decimal("20"),
        interval_actual_cost=Decimal("20"),
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=1,
        expected_user_digest=expected_user_digest([51]),
        write_status="complete",
        reconciliation_status="reconciled",
    )
    observation = Observation.objects.create(
        sample_point=point,
        account_id=7,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("20"),
        selected_total_cost=Decimal("20"),
        total_standard_cost=Decimal("20"),
        total_actual_cost=Decimal("20"),
        cost_window_started_at=window_started_at,
        cost_window_ended_at=observed_at,
        interval_cost_started_at=window_started_at,
        interval_standard_cost=Decimal("20"),
        effective_usd_per_percent=Decimal("2"),
        interval_actual_cost=Decimal("20"),
        interval_cost_source="window_total",
    )
    Sub2APIUserUsageSample.objects.create(
        sample_point=point,
        account_id=7,
        sub2api_user_id=51,
        username="historical-user",
        email="historical@example.com",
        observed_at=observed_at,
        window_started_at=window_started_at,
        window_ended_at=observed_at,
        window_resets_at=reset_at,
        total_standard_cost=Decimal("20"),
        total_actual_cost=Decimal("20"),
        interval_started_at=window_started_at,
        interval_standard_cost=Decimal("20"),
        interval_actual_cost=Decimal("20"),
        interval_source="window_total",
    )
    client = Client()
    headers, _ = jwt_login(client)

    created = client.post(
        "/api/participants",
        data={
            "name": "current participant",
            "sub2api_user_id": 51,
            "is_owner": False,
            "enabled": True,
        },
        content_type="application/json",
        **headers,
    )

    assert created.status_code == 201, created.json()
    participant = Participant.objects.get(pk=created.json()["data"]["id"])
    assert not ParticipantUsageSample.objects.filter(
        participant=participant
    ).exists()
    assert not ParticipantSnapshot.objects.filter(participant=participant).exists()
    PoolParticipant.objects.create(
        pool=account.pool,
        participant=participant,
        share_percent=Decimal("40"),
    )

    historical_usage = ParticipantUsageSample.objects.create(
        participant=participant,
        account_id=7,
        sample_point=point,
        attribution_started_at=window_started_at,
        observed_at=observed_at,
        balance_usd=Decimal("180"),
        selected_cost=Decimal("20"),
        raw_selected_cost=Decimal("20"),
    )
    historical_snapshot = create_participant_snapshot(observation=observation,
    participant=participant,
    selected_cost=Decimal("20"),
    raw_selected_cost=Decimal("20"),
    remaining_share_percent=Decimal("40"),)

    updated = client.put(
        f"/api/participants/{participant.id}",
        data={
            "sub2api_user_id": 52,
        },
        content_type="application/json",
        **headers,
    )

    assert updated.status_code == 200, updated.json()
    account.pool.refresh_from_db()
    assert account.pool.contract_revision == 1
    assert updated.json()["data"]["snapshot"]["recommendation_complete"] is False
    assert updated.json()["data"]["snapshot"]["sources"][0]["snapshot"] is None
    historical_usage.refresh_from_db()
    historical_snapshot.refresh_from_db()
    assert historical_usage.selected_cost == Decimal("20")
    assert historical_snapshot.remaining_share_percent == Decimal("40")
    assert ParticipantUsageSample.objects.filter(participant=participant).count() == 1
    assert ParticipantSnapshot.objects.filter(participant=participant).count() == 1


@pytest.mark.django_db(transaction=True)
def test_participant_create_update_delete_respect_active_account_fence():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    participant = create_participant(name="现有车友",
    sub2api_user_id=51,
    share_percent=50,)
    client = Client()
    headers, _ = jwt_login(client)
    guard = LeaseGuard.acquire(7)
    try:
        created = client.post(
            "/api/participants",
            data={
                "name": "新增车友",
                "sub2api_user_id": 52,
                "share_percent": "40",
            },
            content_type="application/json",
            **headers,
        )
        updated = client.put(
            f"/api/participants/{participant.id}",
            data={
                "notes": "blocked",
            },
            content_type="application/json",
            **headers,
        )
        allocation = client.put(
            "/api/quota-allocation",
            data={
                "pools": [
                    {
                        "id": account.pool_id,
                        "name": account.pool.name,
                        "account_ids": [account.id],
                        "allocations": [
                            {
                                "participant_id": participant.id,
                                "share_percent": "40",
                            }
                        ],
                    }
                ]
            },
            content_type="application/json",
            **headers,
        )
        deleted = client.delete(
            f"/api/participants/{participant.id}",
            **headers,
        )
    finally:
        guard.release()

    assert [
        created.status_code,
        updated.status_code,
        allocation.status_code,
        deleted.status_code,
    ] == [409, 409, 409, 409]
    participant.refresh_from_db()
    assert participant.notes == ""
    assert participant.pool_allocations.get().share_percent == Decimal("50")
    assert Participant.objects.count() == 1
    assert HistoryMaintenanceState.objects.get(account_id=7).fact_revision == 0


@pytest.mark.django_db(transaction=True)
def test_pool_allocation_serializer_rejects_more_than_one_hundred_percent():
    account = create_monitored_account(7)
    first = create_participant(
        name="车友甲",
        sub2api_user_id=51,
        share_percent=60,
        account=account,
    )
    second = create_participant(
        name="车友乙",
        sub2api_user_id=52,
    )
    from monitor.serializers import QuotaAllocationWriteSerializer

    serializer = QuotaAllocationWriteSerializer(
        data={
            "pools": [
                {
                    "id": account.pool_id,
                    "account_ids": [account.id],
                    "allocations": [
                        {"participant_id": first.id, "share_percent": "60"},
                        {"participant_id": second.id, "share_percent": "45"},
                    ],
                }
            ]
        }
    )

    assert not serializer.is_valid()
    assert "不能超过 100%" in str(serializer.errors)


@pytest.mark.django_db(transaction=True)
def test_pool_allocation_apply_rejects_account_set_changed_after_validation():
    account = create_monitored_account(7)
    participant = create_participant(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        account=account,
    )
    from monitor.serializers import QuotaAllocationWriteSerializer

    serializer = QuotaAllocationWriteSerializer(
        data={
            "pools": [
                {
                    "id": account.pool_id,
                    "account_ids": [account.id],
                    "allocations": [
                        {
                            "participant_id": participant.id,
                            "share_percent": "50",
                        }
                    ],
                }
            ]
        }
    )
    assert serializer.is_valid(), serializer.errors
    create_monitored_account(8)

    with pytest.raises(serializers.ValidationError, match="监控账号集合已变化"):
        serializer.apply()

    assert account.pool.allocations.get(
        participant=participant
    ).share_percent == Decimal("50")


@pytest.mark.django_db(transaction=True)
def test_participant_delete_bumps_affected_pool_contract_revision():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    account = create_monitored_account(7)
    participant = create_participant(
        name="可删除车友",
        sub2api_user_id=51,
        share_percent=50,
        account=account,
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.delete(f"/api/participants/{participant.id}", **headers)

    assert response.status_code == 200, response.json()
    account.pool.refresh_from_db()
    assert account.pool.contract_revision == 2
    assert not Participant.objects.filter(pk=participant.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_partial_updates_merge_against_locked_latest_instance():
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    participant = create_participant(name="现有车友",
    sub2api_user_id=51,
    share_percent=50,
    notes="old",)
    stale_name = Participant.objects.get(pk=participant.pk)
    stale_notes = Participant.objects.get(pk=participant.pk)
    name_update = ParticipantWriteSerializer(
        stale_name,
        data={"name": "新名称"},
        partial=True,
    )
    notes_update = ParticipantWriteSerializer(
        stale_notes,
        data={"notes": "new-note"},
        partial=True,
    )

    # Both requests validate the same old row, then saves are serialized.
    assert name_update.is_valid(), name_update.errors
    assert notes_update.is_valid(), notes_update.errors
    name_update.save()
    notes_update.save()

    participant.refresh_from_db()
    assert participant.name == "新名称"
    assert participant.notes == "new-note"
    assert (
        HistoryMaintenanceState.objects.get(account_id=7).fact_revision
        == 2
    )


@pytest.mark.django_db(transaction=True)
def test_allocation_api_returns_400_for_pool_share_conflict():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    account = create_monitored_account(7)
    existing = create_participant(
        name="既有车友",
        sub2api_user_id=51,
        share_percent=70,
        account=account,
    )
    extra = create_participant(name="超额车友", sub2api_user_id=52)
    client = Client()
    headers, _ = jwt_login(client)

    response = client.put(
        "/api/quota-allocation",
        data={
            "pools": [
                {
                    "id": account.pool_id,
                    "account_ids": [account.id],
                    "allocations": [
                        {"participant_id": existing.id, "share_percent": "70"},
                        {"participant_id": extra.id, "share_percent": "40"},
                    ],
                }
            ]
        },
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert "不能超过 100%" in str(response.json()["details"])
    assert existing.pool_allocations.get().share_percent == Decimal("70")
    assert not extra.pool_allocations.exists()
    assert not HistoryMaintenanceState.objects.filter(account_id=7).exists()
