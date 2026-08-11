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
    ObservationFastCorrection,
    Participant,
    ParticipantAPIUsageSnapshot,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.tests.helpers import jwt_login


def _create_history():
    now = timezone.now().replace(microsecond=0)
    first_at = now - timedelta(hours=2)
    middle_at = now - timedelta(minutes=90)
    second_at = now
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
    for observed_at, used, legacy_total in (
        (first_at, Decimal("5"), Decimal("910")),
        (second_at, Decimal("8"), Decimal("960")),
    ):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=used,
            attribution_started_at=reset_at - timedelta(days=7),
            raw_selected_total_cost=legacy_total,
            selected_total_cost=legacy_total,
            total_standard_cost=legacy_total,
            total_actual_cost=legacy_total,
            effective_usd_per_percent=Decimal("16"),
            raw_window={
                "rate_method": "legacy",
                "legacy_cost_rebased": True,
                "query_mode": "passive",
            },
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=owner,
            raw_selected_cost=Decimal("600"),
            selected_cost=Decimal("600"),
            current_balance_usd=Decimal("321"),
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=rider,
            raw_selected_cost=Decimal("310"),
            selected_cost=Decimal("310"),
            current_balance_usd=Decimal("123"),
        )
        observations.append(observation)

    Sub2APIUserUsageSample.objects.create(
        account_id=7,
        sub2api_user_id=11,
        username="legacy-owner",
        email="old-owner@example.com",
        observed_at=middle_at,
        window_resets_at=reset_at,
        total_standard_cost=Decimal("999"),
        total_actual_cost=Decimal("999"),
    )
    ParticipantUsageSample.objects.create(
        participant=owner,
        account_id=7,
        observed_at=middle_at,
        balance_usd=Decimal("222"),
        selected_cost=Decimal("999"),
        raw_selected_cost=Decimal("999"),
    )
    owner.latest_balance_usd = Decimal("222")
    owner.latest_selected_cost = Decimal("999")
    owner.last_checked_at = middle_at
    owner.save(
        update_fields=[
            "latest_balance_usd",
            "latest_selected_cost",
            "last_checked_at",
        ]
    )
    ParticipantAPIUsageSnapshot.objects.create(
        participant=owner,
        observation=observations[-1],
        account_id=7,
        attribution_started_at=reset_at - timedelta(days=7),
        observed_at=second_at,
        cost_basis="actual",
        participant_total_usd=Decimal("1"),
        participant_weekly_percent=Decimal("1"),
        api_keys=[],
    )

    logs = [
        Sub2APIUsageLog(
            1,
            11,
            7,
            now - timedelta(hours=3),
            "priority",
            Decimal("60"),
            Decimal("60"),
        ),
        Sub2APIUsageLog(
            2,
            12,
            7,
            now - timedelta(hours=3),
            "",
            Decimal("40"),
            Decimal("40"),
        ),
        Sub2APIUsageLog(
            3,
            11,
            7,
            now - timedelta(hours=1),
            "priority",
            Decimal("20"),
            Decimal("20"),
        ),
        Sub2APIUsageLog(
            4,
            12,
            7,
            now - timedelta(hours=1),
            "",
            Decimal("30"),
            Decimal("30"),
        ),
    ]
    return observations, logs, middle_at


def _install_fake_sub2api(monkeypatch, logs, *, on_usage_logs=None):
    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def usage_logs(self, **_kwargs):
            if on_usage_logs is not None:
                on_usage_logs()
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
                {
                    "id": 13,
                    "username": "unused",
                    "email": "unused@example.com",
                },
            ]

    monkeypatch.setattr(
        "monitor.historical_rebuild.Sub2APIClient",
        FakeClient,
    )


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
def test_preview_is_read_only_and_full_rebuild_replaces_legacy_costs(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.timezone = "Asia/Shanghai"
    config.cost_basis = "actual"
    config.fast_correction_enabled = True
    config.save()
    observations, logs, middle_at = _create_history()
    latest_old_snapshot = ParticipantSnapshot.objects.get(
        observation=observations[-1],
        participant__sub2api_user_id=11,
    )
    latest_old_snapshot.recommended_balance_usd = Decimal("999")
    latest_old_snapshot.recommendation_applied = True
    latest_old_snapshot.save(
        update_fields=[
            "recommended_balance_usd",
            "recommendation_applied",
        ]
    )
    _install_fake_sub2api(monkeypatch, logs)
    original_quota_facts = [
        (
            row.id,
            row.observed_at,
            row.upstream_used_percent,
            row.upstream_resets_at,
        )
        for row in observations
    ]

    unauthorized = Client().post(
        "/api/settings/data-maintenance/history-rebuild-preview"
    )
    assert unauthorized.status_code == 401

    preview = client.post(
        "/api/settings/data-maintenance/history-rebuild-preview",
        **headers,
    )
    assert preview.status_code == 200
    preview_data = preview.json()["data"]
    assert preview_data["observation_count"] == 2
    assert preview_data["sample_point_count"] == 3
    assert preview_data["request_log_count"] == 4
    assert preview_data["user_count"] == 2
    assert preview_data["rebuilt_user_samples"] == 6
    assert preview_data["rebuilt_participant_samples"] == 6
    assert preview_data["can_rebuild"] is True
    assert Observation.objects.order_by("observed_at").first().total_actual_cost == Decimal(
        "910"
    )
    assert Sub2APIUserUsageSample.objects.count() == 1

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild",
        **headers,
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["replayed_observations"] == 2
    assert result["rebuilt_user_samples"] == 6

    rebuilt = list(Observation.objects.order_by("observed_at", "id"))
    assert [
        (
            row.id,
            row.observed_at,
            row.upstream_used_percent,
            row.upstream_resets_at,
        )
        for row in rebuilt
    ] == original_quota_facts
    assert [row.total_actual_cost for row in rebuilt] == [
        Decimal("100"),
        Decimal("150"),
    ]
    assert [row.interval_actual_cost for row in rebuilt] == [
        Decimal("100"),
        Decimal("50"),
    ]
    assert all(row.interval_cost_source == "historical_logs" for row in rebuilt)
    assert all(
        row.raw_window["rate_method"] == ALGORITHM_VERSION for row in rebuilt
    )
    assert all(row.raw_window["query_mode"] == "passive" for row in rebuilt)
    assert all("legacy_cost_rebased" not in row.raw_window for row in rebuilt)

    latest_users = list(
        Sub2APIUserUsageSample.objects.filter(
            observed_at=rebuilt[-1].observed_at
        ).order_by("sub2api_user_id")
    )
    assert [row.total_actual_cost for row in latest_users] == [
        Decimal("80"),
        Decimal("70"),
    ]
    middle_users = list(
        Sub2APIUserUsageSample.objects.filter(
            observed_at=middle_at
        ).order_by("sub2api_user_id")
    )
    assert [row.total_actual_cost for row in middle_users] == [
        Decimal("60"),
        Decimal("40"),
    ]
    assert middle_users[0].username == "owner"

    owner_middle = ParticipantUsageSample.objects.get(
        participant__sub2api_user_id=11,
        observed_at=middle_at,
    )
    assert owner_middle.raw_selected_cost == Decimal("60")
    assert owner_middle.balance_usd == Decimal("222")
    latest_owner = ParticipantSnapshot.objects.get(
        observation=rebuilt[-1],
        participant__sub2api_user_id=11,
    )
    assert latest_owner.raw_selected_cost == Decimal("80")
    assert latest_owner.current_balance_usd == Decimal("321")
    assert latest_owner.recommendation_applied is False
    config.refresh_from_db()
    assert config.run_lease_until is None
    owner = Participant.objects.get(sub2api_user_id=11)
    owner.refresh_from_db()
    assert owner.latest_balance_usd == Decimal("222")
    assert owner.last_checked_at == middle_at
    assert owner.latest_selected_cost == owner_middle.selected_cost
    assert owner.latest_selected_cost != Decimal("999")

    assert rebuilt[0].fast_correction_request_count == 2
    assert rebuilt[1].fast_correction_request_count == 2
    assert ObservationFastCorrection.objects.filter(
        observation=rebuilt[0],
        sub2api_user_id=11,
        fast_request_count=1,
    ).exists()
    assert not ParticipantAPIUsageSnapshot.objects.exists()


@pytest.mark.django_db
def test_rebuild_does_not_use_old_totals_as_a_conflict_gate(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    _create_history()
    _install_fake_sub2api(monkeypatch, [])

    preview = client.post(
        "/api/settings/data-maintenance/history-rebuild-preview",
        **headers,
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["nonzero_percent_without_cost"] == 2
    assert preview.json()["data"]["can_rebuild"] is True

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild",
        **headers,
    )
    assert response.status_code == 200
    assert all(
        row.total_actual_cost == Decimal("0")
        for row in Observation.objects.filter(account_id=7)
    )



@pytest.mark.django_db
def test_rebuild_refuses_to_overlap_an_active_monitor_lease(
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.run_lease_until = timezone.now() + timedelta(minutes=5)
    config.save()

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild",
        **headers,
    )

    assert response.status_code == 409
    assert "已有采集或历史维护任务" in response.json()["message"]

@pytest.mark.django_db
def test_rebuild_aborts_if_relevant_settings_change_during_log_fetch(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.cost_basis = "actual"
    config.save()
    observations, logs, _middle_at = _create_history()

    def change_settings():
        AppSettings.objects.filter(pk=config.pk).update(cost_basis="standard")

    _install_fake_sub2api(
        monkeypatch,
        logs,
        on_usage_logs=change_settings,
    )

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild",
        **headers,
    )

    assert response.status_code == 409
    assert "系统设置发生变化" in response.json()["message"]
    observations[0].refresh_from_db()
    assert observations[0].total_actual_cost == Decimal("910")


@pytest.mark.django_db
def test_rebuild_aborts_if_participant_mapping_changes_during_log_fetch(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    observations, logs, _middle_at = _create_history()

    def change_participant():
        Participant.objects.filter(sub2api_user_id=11).update(
            sub2api_user_id=99
        )

    _install_fake_sub2api(
        monkeypatch,
        logs,
        on_usage_logs=change_participant,
    )

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild",
        **headers,
    )

    assert response.status_code == 409
    assert "参与者配置发生变化" in response.json()["message"]
    observations[0].refresh_from_db()
    assert observations[0].total_actual_cost == Decimal("910")