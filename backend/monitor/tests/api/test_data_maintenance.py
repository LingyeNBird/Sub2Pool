from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.accounting.contracts import ALGORITHM_VERSION
from monitor.integrations.sub2api import Sub2APIUsageLog
from monitor.models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    Sub2APIUserUsageSample,
)
from monitor.tests.helpers import jwt_login


def _create_history():
    now = timezone.now().replace(microsecond=0)
    reset_at = now + timedelta(days=4)
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=11,
        share_percent=50,
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=12,
        share_percent=50,
    )
    observations = []
    for index, (used, total, owner_cost, rider_cost) in enumerate(
        (
            (Decimal("5"), Decimal("100"), Decimal("60"), Decimal("40")),
            (Decimal("8"), Decimal("150"), Decimal("80"), Decimal("70")),
        )
    ):
        observed_at = now - timedelta(hours=1 - index)
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=used,
            raw_selected_total_cost=total,
            selected_total_cost=total,
            total_standard_cost=total,
            total_actual_cost=total,
            effective_usd_per_percent=Decimal("16"),
            raw_window={"rate_method": "legacy"},
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=owner,
            raw_selected_cost=owner_cost,
            selected_cost=owner_cost,
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=rider,
            raw_selected_cost=rider_cost,
            selected_cost=rider_cost,
        )
        observations.append(observation)

    logs = [
        Sub2APIUsageLog(
            1,
            11,
            7,
            now - timedelta(hours=2),
            "",
            Decimal("60"),
            Decimal("60"),
        ),
        Sub2APIUsageLog(
            2,
            12,
            7,
            now - timedelta(hours=2),
            "",
            Decimal("40"),
            Decimal("40"),
        ),
        Sub2APIUsageLog(
            3,
            11,
            7,
            now - timedelta(minutes=30),
            "",
            Decimal("20"),
            Decimal("20"),
        ),
        Sub2APIUsageLog(
            4,
            12,
            7,
            now - timedelta(minutes=30),
            "",
            Decimal("30"),
            Decimal("30"),
        ),
    ]
    return observations, logs


def _install_fake_sub2api(monkeypatch, logs):
    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def usage_logs(self, **_kwargs):
            return logs

        def list_users(self):
            return [
                {
                    "id": 11,
                    "username": "owner",
                    "email": "owner@example.com",
                },
                {
                    "id": 12,
                    "username": "rider",
                    "email": "rider@example.com",
                },
            ]

    monkeypatch.setattr("monitor.usage_history.Sub2APIClient", FakeClient)


@pytest.fixture
def admin_client():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    return client, headers


@pytest.mark.django_db
def test_history_preview_is_read_only_and_backfill_replays_all(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.timezone = "Asia/Shanghai"
    config.save()
    observations, logs = _create_history()
    _install_fake_sub2api(monkeypatch, logs)
    original_facts = [
        (item.observed_at, item.upstream_used_percent) for item in observations
    ]

    unauthorized = Client().post(
        "/api/settings/data-maintenance/history-preview"
    )
    assert unauthorized.status_code == 401

    preview = client.post(
        "/api/settings/data-maintenance/history-preview",
        **headers,
    )
    assert preview.status_code == 200
    preview_data = preview.json()["data"]
    assert preview_data["observation_count"] == 2
    assert preview_data["missing_samples"] == 4
    assert preview_data["fillable_samples"] == 4
    assert preview_data["incompatible_segments"] == 0
    assert preview_data["can_backfill"] is True
    assert not Sub2APIUserUsageSample.objects.exists()

    response = client.post(
        "/api/settings/data-maintenance/history-backfill",
        **headers,
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["inserted_samples"] == 4
    assert result["replayed_observations"] == 2
    assert Sub2APIUserUsageSample.objects.count() == 4

    observations = list(Observation.objects.order_by("observed_at", "id"))
    assert [
        (item.observed_at, item.upstream_used_percent) for item in observations
    ] == original_facts
    assert all(
        item.raw_window["rate_method"] == ALGORITHM_VERSION
        for item in observations
    )
    latest_rows = Sub2APIUserUsageSample.objects.filter(
        observed_at=observations[-1].observed_at
    ).order_by("sub2api_user_id")
    assert [row.total_actual_cost for row in latest_rows] == [
        Decimal("80"),
        Decimal("70"),
    ]


@pytest.mark.django_db
def test_history_backfill_rejects_incompatible_aggregate_without_writes(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    _, logs = _create_history()
    logs[-1] = Sub2APIUsageLog(
        logs[-1].id,
        logs[-1].user_id,
        logs[-1].account_id,
        logs[-1].created_at,
        logs[-1].service_tier,
        Decimal("300"),
        Decimal("300"),
    )
    _install_fake_sub2api(monkeypatch, logs)

    response = client.post(
        "/api/settings/data-maintenance/history-backfill",
        **headers,
    )

    assert response.status_code == 409
    assert response.json()["details"]["incompatible_segments"] == 1
    assert not Sub2APIUserUsageSample.objects.exists()


@pytest.mark.django_db
def test_explicit_full_rebuild_ignores_existing_version_markers(
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    observations, _ = _create_history()

    response = client.post(
        "/api/settings/data-maintenance/rebuild-all",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["rebuilt_observations"] == 2
    for observation in observations:
        observation.refresh_from_db()
        assert observation.raw_window["rate_method"] == ALGORITHM_VERSION



@pytest.mark.django_db
def test_cost_history_repair_preserves_raw_totals_and_bridges_false_reset(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.fast_correction_enabled = False
    config.cost_basis = "actual"
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=11,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now().replace(microsecond=0)
    reset_at = now + timedelta(days=3)
    rows = (
        (now - timedelta(hours=3), Decimal("47"), Decimal("1217")),
        (now - timedelta(hours=2), Decimal("18"), Decimal("361")),
        (now - timedelta(hours=1), Decimal("49"), Decimal("379")),
    )
    observations = []
    for observed_at, used_percent, raw_total in rows:
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=used_percent,
            raw_selected_total_cost=raw_total,
            selected_total_cost=raw_total,
            total_standard_cost=raw_total,
            total_actual_cost=raw_total,
            effective_usd_per_percent=Decimal("20"),
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=raw_total,
            selected_cost=raw_total,
            current_balance_usd=Decimal("500"),
        )
        Sub2APIUserUsageSample.objects.create(
            account_id=7,
            sub2api_user_id=11,
            observed_at=observed_at,
            window_started_at=None,
            window_resets_at=reset_at,
            total_standard_cost=raw_total,
            total_actual_cost=raw_total,
        )
        observations.append(observation)

    logs = [
        Sub2APIUsageLog(
            1,
            11,
            7,
            rows[0][0] + timedelta(minutes=30),
            "",
            Decimal("12"),
            Decimal("12"),
        ),
        Sub2APIUsageLog(
            2,
            11,
            7,
            rows[1][0] + timedelta(minutes=30),
            "",
            Decimal("18"),
            Decimal("18"),
        ),
    ]

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def usage_logs(self, **_kwargs):
            return logs

    monkeypatch.setattr("monitor.cost_history.Sub2APIClient", FakeClient)

    unauthorized = Client().post(
        "/api/settings/data-maintenance/cost-history-preview"
    )
    assert unauthorized.status_code == 401

    preview = client.post(
        "/api/settings/data-maintenance/cost-history-preview",
        **headers,
    )
    assert preview.status_code == 200
    preview_data = preview.json()["data"]
    assert preview_data["coordinate_changes"] == 1
    assert preview_data["snapshot_conflicts"] == 0
    assert preview_data["observation_interval_count"] == 3
    assert preview_data["can_repair"] is True
    assert all(
        row.normalized_actual_cost is None
        for row in Observation.objects.order_by("observed_at", "id")
    )

    response = client.post(
        "/api/settings/data-maintenance/cost-history-repair",
        **headers,
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["replayed_observations"] == 2
    assert result["automatic_exclusions"] == 1

    repaired = list(Observation.objects.order_by("observed_at", "id"))
    assert [row.total_actual_cost for row in repaired] == [
        Decimal("1217"),
        Decimal("361"),
        Decimal("379"),
    ]
    assert [row.normalized_actual_cost for row in repaired] == [
        Decimal("1217"),
        Decimal("1229"),
        Decimal("1247"),
    ]
    assert repaired[1].exclusion_source == "automatic"
    assert repaired[2].selected_total_cost == Decimal("1247")
    assert repaired[2].delta_cost == Decimal("30")

    user_rows = list(
        Sub2APIUserUsageSample.objects.order_by("observed_at", "id")
    )
    assert [row.normalized_actual_cost for row in user_rows] == [
        Decimal("1217"),
        Decimal("1229"),
        Decimal("1247"),
    ]