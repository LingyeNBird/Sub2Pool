import json
import sqlite3
from io import BytesIO, StringIO

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from monitor.history_state import LeaseGuard
from monitor.engine import run_monitor
from monitor.management.commands.runmonitor import schedule_next_run
from monitor.models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    HistoryMaintenanceState,
    NotificationEvent,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.notifications import send_notification
from monitor.replay import (
    RATE_METHOD,
    exclude_observation,
    rebuild_account,
    rebuild_observation_suffix,
)
from monitor.secrets import encrypt_secret
from monitor.integrations.sub2api import (
    Sub2APIClient,
    Sub2APIError,
    Sub2APIUserUsage,
    Sub2APIUsageLog,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor import database_transfer
from monitor.tests.helpers import create_recommendation_snapshot, jwt_login

@pytest.mark.django_db
def test_partial_settings_patch_does_not_touch_other_cards():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example"
    config.smtp_host = "smtp.original.example"
    config.save()
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"local_poll_minutes": 17}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    config.refresh_from_db()
    assert config.local_poll_minutes == 17
    assert config.sub2api_base_url == "https://sub2api.example"
    assert config.smtp_host == "smtp.original.example"


@pytest.mark.django_db(transaction=True)
def test_replay_settings_respect_fence_but_unrelated_email_settings_do_not():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])
    client = Client()
    headers, _ = jwt_login(client)
    guard = LeaseGuard.acquire(7)
    try:
        blocked = client.patch(
            "/api/settings",
            data=json.dumps({"safety_factor": "0.9"}),
            content_type="application/json",
            **headers,
        )
        unrelated = client.patch(
            "/api/settings",
            data=json.dumps({"smtp_host": "smtp.changed.example"}),
            content_type="application/json",
            **headers,
        )
    finally:
        guard.release()

    assert blocked.status_code == 409
    assert unrelated.status_code == 200, unrelated.json()
    config.refresh_from_db()
    assert config.safety_factor == Decimal("0.95")
    assert config.smtp_host == "smtp.changed.example"



@pytest.mark.django_db
def test_allocation_settings_rebuild_existing_derived_results(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    now = timezone.now()
    Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    rebuilt: list[tuple[int, Decimal]] = []

    def fake_rebuild(account_id, config, *, guard):
        assert guard.account_id == account_id
        rebuilt.append((account_id, config.safety_factor))

    monkeypatch.setattr(
        "monitor.views.settings.rebuild_account",
        fake_rebuild,
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"safety_factor": "0.9"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert rebuilt == [(7, Decimal("0.9000"))]
    assert AppSettings.load().safety_factor == Decimal("0.9000")


@pytest.mark.django_db
def test_allocation_setting_rolls_back_when_derived_replay_fails(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    now = timezone.now()
    Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("500"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )

    def fail_rebuild(_account_id, _config, *, guard):
        assert guard.account_id == 7
        raise ValueError("历史成本不满足模型约束")

    monkeypatch.setattr(
        "monitor.views.settings.rebuild_account",
        fail_rebuild,
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"cost_basis": "standard"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 409
    assert response.json()["message"] == "设置未保存：历史派生结果重建失败"
    assert AppSettings.load().cost_basis == "actual"
@pytest.mark.django_db
def test_settings_rejects_invalid_iana_timezone():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"timezone": "Shanghai"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert response.json()["details"]["timezone"]
    assert AppSettings.load().timezone == "Asia/Shanghai"

@pytest.mark.django_db
def test_global_monitor_schedule_records_next_wake_time():
    config = AppSettings.load()
    config.local_poll_minutes = 13
    config.save()
    now = timezone.now()

    sleep_seconds = schedule_next_run(config, now=now)

    config.refresh_from_db()
    assert sleep_seconds == 13 * 60
    assert config.next_local_check_at == now + timedelta(minutes=13)

@pytest.mark.django_db
def test_global_monitor_schedule_does_not_add_run_duration_to_interval():
    config = AppSettings.load()
    config.local_poll_minutes = 10
    config.save()
    cycle_started_at = timezone.now()

    sleep_seconds = schedule_next_run(
        config,
        now=cycle_started_at + timedelta(minutes=3),
        cycle_started_at=cycle_started_at,
    )

    config.refresh_from_db()
    assert sleep_seconds == 7 * 60
    assert config.next_local_check_at == cycle_started_at + timedelta(minutes=10)

@pytest.mark.django_db
def test_global_monitor_schedule_skips_elapsed_slots_after_slow_run():
    config = AppSettings.load()
    config.local_poll_minutes = 10
    config.save()
    cycle_started_at = timezone.now()

    sleep_seconds = schedule_next_run(
        config,
        now=cycle_started_at + timedelta(minutes=25),
        cycle_started_at=cycle_started_at,
    )

    config.refresh_from_db()
    assert sleep_seconds == 5 * 60
    assert config.next_local_check_at == cycle_started_at + timedelta(minutes=30)

@pytest.mark.django_db
def test_monitor_status_exposes_global_countdown_and_hides_it_when_disabled():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    now = timezone.now()
    config.monitoring_enabled = True
    config.local_poll_minutes = 10
    config.next_local_check_at = now + timedelta(minutes=7)
    config.openai_account_id = 7
    config.save()
    HistoryMaintenanceState.objects.create(
        account_id=7,
        lease_owner="61d20cbf-c1b5-4e90-bd40-4837436db565",
        lease_expires_at=now + timedelta(minutes=1),
    )

    enabled = client.get("/api/monitor/run", **headers)

    assert enabled.status_code == 200
    data = enabled.json()["data"]
    assert data["monitoring_enabled"] is True
    assert data["interval_seconds"] == 600
    assert data["next_local_check_at"] == config.next_local_check_at.isoformat()
    assert data["server_time"]
    assert data["run_in_progress"] is True

    config.monitoring_enabled = False
    config.save(update_fields=["monitoring_enabled"])
    disabled = client.get("/api/monitor/run", **headers).json()["data"]
    assert disabled["monitoring_enabled"] is False
    assert disabled["next_local_check_at"] is None

@pytest.mark.django_db
def test_account_discovery_uses_unsaved_address_and_token(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    captured: dict = {}

    class FakeClient:
        def __init__(self, config, **overrides):
            captured["saved_url"] = config.sub2api_base_url
            captured.update(overrides)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_openai_accounts(self):
            return [
                {
                    "id": 88,
                    "name": "测试账号",
                    "type": "oauth",
                    "status": "active",
                    "schedulable": True,
                }
            ]

    monkeypatch.setattr("monitor.views.settings.Sub2APIClient", FakeClient)
    response = client.post(
        "/api/settings/openai-accounts",
        data=json.dumps(
            {
                "sub2api_base_url": "http://unsaved-sub2api:8088",
                "sub2api_admin_token": "unsaved-admin-token",
                "request_timeout_seconds": 37,
                "verify_tls": False,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == 88
    assert captured == {
        "saved_url": "http://host.docker.internal:8080",
        "base_url": "http://unsaved-sub2api:8088",
        "admin_token": "unsaved-admin-token",
        "request_timeout_seconds": 37,
        "verify_tls": False,
    }
    config = AppSettings.load()
    assert config.sub2api_base_url == "http://host.docker.internal:8080"
    assert config.sub2api_admin_token_encrypted == ""
