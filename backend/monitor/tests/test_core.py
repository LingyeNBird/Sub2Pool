import json

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import (
    AppSettings,
    LoginEvent,
    NotificationEvent,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    QuotaCycle,
)
from monitor.notifications import send_notification
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
    assert ParticipantUsageSample.objects.count() == 2


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


def test_django_serves_vue_entry_for_root_and_history_routes():
    client = Client()

    for route in ("/", "/participants", "/settings"):
        response = client.get(route)
        assert response.status_code == 200
        assert b'id="app"' in response.content
        assert b"/static/frontend/assets/index-" in response.content

    assert client.get("/api/unknown").status_code == 404


@pytest.mark.django_db
def test_login_audit_records_server_and_webrtc_addresses(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    common = {
        "content_type": "application/json",
        "REMOTE_ADDR": "10.0.0.2",
        "HTTP_X_FORWARDED_FOR": "198.51.100.23",
        "HTTP_USER_AGENT": "Audit Browser/1.0",
    }
    failed = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": "owner",
                "password": "wrong-password",
                "client_network": {
                    "webrtc_supported": True,
                    "webrtc_ips": [
                        "192.168.1.8",
                        "host.local",
                        "999.1.1.1",
                    ],
                },
            }
        ),
        **common,
    )
    assert failed.status_code == 401

    success = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": "owner",
                "password": "very-strong-password",
                "client_network": {
                    "webrtc_supported": True,
                    "webrtc_ips": ["192.168.1.8", "203.0.113.9"],
                },
            }
        ),
        **common,
    )
    assert success.status_code == 200

    rows = list(LoginEvent.objects.order_by("created_at"))
    assert len(rows) == 2
    assert rows[0].success is False
    assert rows[0].failure_reason == "用户名、密码或权限错误"
    assert rows[1].success is True
    assert rows[1].request_ip == "198.51.100.23"
    assert rows[1].remote_ip == "10.0.0.2"
    assert rows[1].webrtc_ips == ["192.168.1.8", "203.0.113.9"]
    assert rows[1].user_agent == "Audit Browser/1.0"

    audit = client.get("/api/login-events").json()["data"]
    assert audit["success_count"] == 1
    assert audit["failure_count"] == 1
    assert audit["unique_request_ips"] == 1


@pytest.mark.django_db
def test_statistics_groups_capacity_and_participant_usage():
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
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=22,
        share_percent=50,
    )
    now = timezone.now()
    base = (now - timedelta(days=60)).replace(
        day=10,
        hour=8,
        minute=0,
        second=0,
        microsecond=0,
    )
    cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=base - timedelta(days=7),
        resets_at=now + timedelta(days=3),
    )

    def observation(at, rate):
        return Observation.objects.create(
            cycle=cycle,
            observed_at=at,
            upstream_used_percent=10,
            selected_total_cost=100,
            total_standard_cost=100,
            total_actual_cost=100,
            effective_usd_per_percent=Decimal(rate),
        )

    observation(base, "10")
    observation(base + timedelta(hours=2), "12")
    observation(base + timedelta(days=1), "14")
    observation(now, "16")

    hour = now.replace(minute=5, second=0, microsecond=0)
    ParticipantUsageSample.objects.create(
        participant=participant,
        cycle=cycle,
        observed_at=hour,
        weekly_usage_usd=10,
        weekly_limit_usd=100,
        selected_cost=10,
    )
    ParticipantUsageSample.objects.create(
        participant=participant,
        cycle=cycle,
        observed_at=hour + timedelta(minutes=30),
        weekly_usage_usd=12,
        weekly_limit_usd=100,
        selected_cost=12,
    )

    daily = client.get(
        "/api/statistics?capacity_period=day&capacity_days=365"
        "&usage_days=7&usage_precision=hour"
    ).json()["data"]
    assert daily["capacity_series"][-1]["weekly_total_usd"] == 1600.0
    assert len(daily["participant_series"][0]["points"]) == 1
    assert (
        daily["participant_series"][0]["points"][0]["weekly_usage_usd"]
        == 12.0
    )

    monthly = client.get(
        "/api/statistics?capacity_period=month&capacity_days=365"
    ).json()["data"]
    base_month = base.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    month = next(
        item for item in monthly["capacity_series"] if item["period"] == base_month
    )
    assert month["weekly_total_usd"] == 1300.0
    assert month["minimum_usd"] == 1000.0
    assert month["maximum_usd"] == 1400.0
    assert month["sample_count"] == 2


@pytest.mark.django_db
def test_resend_provider_sends_with_encrypted_key(monkeypatch):
    config = AppSettings.load()
    config.email_provider = "resend"
    config.notification_email = "owner@example.com"
    config.resend_from_email = "拼车额度 <notice@example.com>"
    config.resend_api_key_encrypted = encrypt_secret("re_secret")
    config.save()
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(200, json={"id": "email_123"})

    monkeypatch.setattr("monitor.notifications.httpx.post", fake_post)
    event = send_notification(
        config=config,
        event_type="test",
        dedupe_key="resend-test",
        subject="测试",
        body="正文",
        ignore_cooldown=True,
    )

    assert event is not None
    assert event.status == "sent"
    assert NotificationEvent.objects.get(pk=event.pk).sent_at is not None
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_secret"
    assert captured["headers"]["Idempotency-Key"].startswith("pinche-")
    assert captured["json"] == {
        "from": "拼车额度 <notice@example.com>",
        "to": ["owner@example.com"],
        "subject": "测试",
        "text": "正文",
    }
