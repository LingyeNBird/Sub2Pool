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
            "share_percent": "40",
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
            "share_percent": "60",
        },
        content_type="application/json",
        **headers,
    )

    assert updated.status_code == 200, updated.json()
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
                "share_percent": "40",
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

    assert [created.status_code, updated.status_code, deleted.status_code] == [
        409,
        409,
        409,
    ]
    participant.refresh_from_db()
    assert participant.share_percent == Decimal("50")
    assert Participant.objects.count() == 1
    assert HistoryMaintenanceState.objects.get(account_id=7).fact_revision == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_participant_creates_revalidate_share_inside_fence():
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    create_participant(name="既有车友",
    sub2api_user_id=50,
    share_percent=20,)
    first = ParticipantWriteSerializer(
        data={
            "name": "并发甲",
            "sub2api_user_id": 51,
            "share_percent": "45",
        }
    )
    second = ParticipantWriteSerializer(
        data={
            "name": "并发乙",
            "sub2api_user_id": 52,
            "share_percent": "45",
        }
    )

    # Both requests cross the DRF validation boundary before either saves.
    assert first.is_valid(), first.errors
    assert second.is_valid(), second.errors
    first.save()
    with pytest.raises(serializers.ValidationError, match="不能超过 100%"):
        second.save()

    assert sum(
        Participant.objects.filter(enabled=True).values_list(
            "share_percent",
            flat=True,
        ),
        Decimal("0"),
    ) == Decimal("65")
    assert (
        HistoryMaintenanceState.objects.get(account_id=7).fact_revision
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_partial_updates_merge_against_locked_latest_instance():
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    participant = create_participant(name="现有车友",
    sub2api_user_id=51,
    share_percent=50,
    notes="old",)
    stale_share = Participant.objects.get(pk=participant.pk)
    stale_notes = Participant.objects.get(pk=participant.pk)
    share_update = ParticipantWriteSerializer(
        stale_share,
        data={"share_percent": "40"},
        partial=True,
    )
    notes_update = ParticipantWriteSerializer(
        stale_notes,
        data={"notes": "new-note"},
        partial=True,
    )

    # Both requests validate the same old row, then saves are serialized.
    assert share_update.is_valid(), share_update.errors
    assert notes_update.is_valid(), notes_update.errors
    share_update.save()
    notes_update.save()

    participant.refresh_from_db()
    assert participant.share_percent == Decimal("40")
    assert participant.notes == "new-note"
    assert (
        HistoryMaintenanceState.objects.get(account_id=7).fact_revision
        == 2
    )


@pytest.mark.django_db(transaction=True)
def test_participant_api_returns_400_for_locked_share_conflict():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    create_participant(name="既有车友",
    sub2api_user_id=51,
    share_percent=70,)
    client = Client()
    headers, _ = jwt_login(client)

    response = client.post(
        "/api/participants",
        data={
            "name": "超额车友",
            "sub2api_user_id": 52,
            "share_percent": "40",
        },
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert "不能超过 100%" in response.json()["message"]
    assert Participant.objects.count() == 1
    assert (
        HistoryMaintenanceState.objects.get(account_id=7).fact_revision
        == 0
    )
