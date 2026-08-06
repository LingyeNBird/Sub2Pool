import json

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import AppSettings, Participant, ParticipantSnapshot
from monitor.secrets import encrypt_secret
from monitor.sub2api import PlatformQuota, Sub2APIClient, UsageStats, WeeklyWindow


@pytest.mark.django_db
def test_default_query_mode_is_passive():
    assert AppSettings.load().quota_query_mode == "passive"


@pytest.mark.django_db
def test_passive_quota_reads_account_snapshot_without_official_quota_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_paths: list[str] = []
    reset_at = timezone.now() + timedelta(days=3)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "platform": "openai",
                    "extra": {
                        "codex_7d_used_percent": 37.5,
                        "codex_7d_reset_at": reset_at.isoformat(),
                        "codex_usage_updated_at": timezone.now().isoformat(),
                    },
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"x-api-key": "admin-secret"})
        window = client.query_weekly_window(42, "passive")

    assert window.used_percent == Decimal("37.5")
    assert requested_paths == ["/api/v1/admin/accounts/42"]
    assert not any("/openai/" in path for path in requested_paths)


@pytest.mark.django_db
def test_initial_observation_conserves_percent_and_builds_manual_recommendations(monkeypatch):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.quota_query_mode = "passive"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = Participant.objects.create(name="车主", sub2api_user_id=1, share_percent=50, is_owner=True)
    rider = Participant.objects.create(name="车友", sub2api_user_id=2, share_percent=50)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, account_id, mode):
            assert account_id == 7
            assert mode == "passive"
            return WeeklyWindow(Decimal("40"), 604800, 345600, int(reset_at.timestamp()), "passive_snapshot")

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("400"), 1: Decimal("300"), 2: Decimal("100")}
            return UsageStats(costs[user_id], costs[user_id])

        def platform_quota(self, user_id, platform):
            assert platform == "openai"
            usage = Decimal("300") if user_id == 1 else Decimal("100")
            return PlatformQuota(usage, Decimal("500"), None, None)

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    snapshots = {item.participant_id: item for item in ParticipantSnapshot.objects.all()}
    assert snapshots[owner.id].charged_cycle_percent == Decimal("30")
    assert snapshots[rider.id].charged_cycle_percent == Decimal("10")
    assert sum((item.charged_cycle_percent for item in snapshots.values()), Decimal("0")) == Decimal("40")
    assert snapshots[owner.id].recommended_weekly_limit_usd == Decimal("604.00")
    assert snapshots[rider.id].recommended_weekly_limit_usd == Decimal("708.00")


@pytest.mark.django_db
def test_api_requires_admin_session_and_accepts_admin_login():
    user = get_user_model().objects.create_superuser(username="owner", password="very-strong-password", email="owner@example.com")
    client = Client()
    unauthorized = client.get("/api/dashboard")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["ok"] is False

    logged_in = client.post(
        "/api/auth/login",
        data='{"username":"owner","password":"very-strong-password"}',
        content_type="application/json",
    )
    assert logged_in.status_code == 200
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["quota_query_mode"] == "passive"
    assert user.is_staff


@pytest.mark.django_db
def test_settings_round_trip_accepts_internal_docker_url_and_decimal_values():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    client.post(
        "/api/auth/login",
        data='{"username":"owner","password":"very-strong-password"}',
        content_type="application/json",
    )
    payload = client.get("/api/settings").json()["data"]
    payload["local_poll_minutes"] = 11
    response = client.patch(
        "/api/settings",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 200
    config = AppSettings.load()
    assert config.sub2api_base_url == "http://host.docker.internal:8080"
    assert config.safety_factor == Decimal("0.95")
    assert config.local_poll_minutes == 11
