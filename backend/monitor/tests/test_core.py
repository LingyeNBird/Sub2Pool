import json
import sqlite3
from io import BytesIO

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
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
from monitor import database_transfer

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
def test_openai_account_discovery_uses_filtered_paginated_admin_api():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/accounts"
        assert request.url.params["platform"] == "openai"
        assert request.url.params["lite"] == "true"
        assert request.headers["x-api-key"] == "admin-secret"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        item = (
            {
                "id": 41,
                "name": "GPT Pro 主账号",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "schedulable": True,
            }
            if page == 1
            else {
                "id": 42,
                "name": "备用账号",
                "platform": "openai",
                "type": "oauth",
                "status": "disabled",
                "schedulable": False,
            }
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [item],
                    "total": 2,
                    "page": page,
                    "page_size": 1,
                    "pages": 2,
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        accounts = client.list_openai_accounts()

    assert requested_pages == [1, 2]
    assert accounts == [
        {
            "id": 41,
            "name": "GPT Pro 主账号",
            "type": "oauth",
            "status": "active",
            "schedulable": True,
        },
        {
            "id": 42,
            "name": "备用账号",
            "type": "oauth",
            "status": "disabled",
            "schedulable": False,
        },
    ]


@pytest.mark.django_db
def test_sub2api_user_discovery_reads_regular_users_only():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/users"
        assert request.url.params["role"] == "user"
        assert request.url.params["include_subscriptions"] == "false"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [
                        {
                            "id": 51,
                            "email": "rider@example.com",
                            "username": "rider",
                            "status": "active",
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 100,
                    "pages": 1,
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        users = client.list_users()

    assert users == [
        {
            "id": 51,
            "email": "rider@example.com",
            "username": "rider",
            "status": "active",
        }
    ]


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
def test_midcycle_initialization_assigns_existing_ten_percent_to_owner(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=2,
        share_percent=50,
    )
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("10"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("100"), 1: Decimal("100"), 2: Decimal("0")}
            return UsageStats(costs[user_id], costs[user_id])

        def platform_quota(self, user_id, _platform):
            usage = Decimal("100") if user_id == 1 else Decimal("0")
            return PlatformQuota(usage, Decimal("800"), None, None)

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(force_upstream=True, source="manual")

    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    assert snapshots[owner.id].charged_cycle_percent == Decimal("10")
    assert snapshots[owner.id].remaining_share_percent == Decimal("40")
    assert snapshots[rider.id].charged_cycle_percent == Decimal("0")
    assert snapshots[rider.id].remaining_share_percent == Decimal("50")


@pytest.mark.django_db
def test_manual_upstream_refresh_starts_new_cycle_without_negative_ledger(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=4)
    old_cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=reset_at - timedelta(days=7),
        resets_at=reset_at,
        active=True,
    )
    previous = Observation.objects.create(
        cycle=old_cycle,
        source="manual",
        observed_at=now - timedelta(hours=1),
        upstream_used_percent=Decimal("10"),
        selected_total_cost=Decimal("100"),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
        effective_usd_per_percent=Decimal("10"),
    )
    ParticipantSnapshot.objects.create(
        observation=previous,
        participant=owner,
        selected_cost=Decimal("100"),
        charged_delta_percent=Decimal("10"),
        charged_cycle_percent=Decimal("10"),
        remaining_share_percent=Decimal("40"),
        platform_weekly_usage_usd=Decimal("100"),
        platform_weekly_limit_usd=Decimal("500"),
        recommended_weekly_limit_usd=Decimal("500"),
    )

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("0"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("0"), Decimal("0"))

        def platform_quota(self, _user_id, _platform):
            return PlatformQuota(
                Decimal("0"),
                Decimal("500"),
                None,
                None,
            )

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["reason"] == "检测到官方手动刷新"
    old_cycle.refresh_from_db()
    assert old_cycle.active is False
    current = QuotaCycle.objects.get(active=True)
    assert current.id != old_cycle.id
    snapshot = ParticipantSnapshot.objects.get(observation__cycle=current)
    assert snapshot.charged_delta_percent == Decimal("0")
    assert snapshot.charged_cycle_percent == Decimal("0")
    assert snapshot.remaining_share_percent == Decimal("50")
    assert snapshot.delta_cost is None


@pytest.mark.django_db
def test_api_requires_admin_jwt_and_accepts_admin_login():
    user = get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    unauthorized = client.get("/api/dashboard")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["ok"] is False

    headers, logged_in = jwt_login(client)
    assert "access" in logged_in.json()["data"]
    assert "refresh" not in logged_in.json()["data"]
    assert logged_in.cookies["pinche_refresh"]["httponly"]
    assert "sessionid" not in logged_in.cookies

    dashboard = client.get("/api/dashboard", **headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["quota_query_mode"] == "passive"
    assert user.is_staff

@pytest.mark.django_db
def test_refresh_rotation_blacklists_old_cookie_and_logout_clears_current_cookie():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    _, logged_in = jwt_login(client)
    old_refresh = logged_in.cookies["pinche_refresh"].value

    refreshed = client.post(
        "/api/auth/refresh",
        data="{}",
        content_type="application/json",
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["data"]["access"]
    new_refresh = refreshed.cookies["pinche_refresh"].value
    assert new_refresh != old_refresh

    replay = Client()
    replay.cookies["pinche_refresh"] = old_refresh
    assert replay.post("/api/auth/refresh").status_code == 401

    logout = client.post(
        "/api/auth/logout",
        HTTP_AUTHORIZATION=f"Bearer {new_access}",
    )
    assert logout.status_code == 200
    assert logout.cookies["pinche_refresh"]["max-age"] == 0
    assert client.post("/api/auth/refresh").status_code == 401


@pytest.mark.django_db
def test_password_change_reissues_tokens_and_revokes_old_access():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    old_headers, _ = jwt_login(client)

    changed = client.post(
        "/api/auth/password",
        data=json.dumps(
            {
                "old_password": "very-strong-password",
                "new_password": "another-very-strong-password",
            }
        ),
        content_type="application/json",
        **old_headers,
    )
    assert changed.status_code == 200
    new_access = changed.json()["data"]["access"]
    assert client.get("/api/auth/me", **old_headers).status_code == 401
    assert (
        client.get(
            "/api/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {new_access}",
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_settings_round_trip_accepts_internal_docker_url_and_decimal_values():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    headers, _ = jwt_login(client)
    payload = client.get("/api/settings", **headers).json()["data"]
    payload["local_poll_minutes"] = 11
    response = client.patch(
        "/api/settings",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    config = AppSettings.load()
    assert config.sub2api_base_url == "http://host.docker.internal:8080"
    assert config.safety_factor == Decimal("0.95")
    assert config.local_poll_minutes == 11


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


@pytest.mark.django_db
def test_connection_test_uses_unsaved_form_without_persisting(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    captured: dict = {}

    class FakeClient:
        def __init__(self, _config, **overrides):
            captured.update(overrides)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def test_connection(self, account_id, mode):
            captured["account_id"] = account_id
            captured["mode"] = mode
            return {"users_api": "ok", "quota_api": "ok"}

    monkeypatch.setattr("monitor.views.settings.Sub2APIClient", FakeClient)
    response = client.post(
        "/api/settings/test-sub2api",
        data=json.dumps(
            {
                "sub2api_base_url": "https://new-sub2api.example",
                "sub2api_admin_token": "new-admin-token",
                "openai_account_id": 91,
                "quota_query_mode": "direct",
                "request_timeout_seconds": 25,
                "verify_tls": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert captured == {
        "base_url": "https://new-sub2api.example",
        "admin_token": "new-admin-token",
        "request_timeout_seconds": 25,
        "verify_tls": True,
        "account_id": 91,
        "mode": "direct",
    }
    config = AppSettings.load()
    assert config.openai_account_id is None
    assert config.quota_query_mode == "passive"


@pytest.mark.django_db
def test_participant_user_list_endpoint_uses_saved_admin_connection(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_users(self):
            return [
                {
                    "id": 51,
                    "email": "rider@example.com",
                    "username": "rider",
                    "status": "active",
                }
            ]

    monkeypatch.setattr("monitor.views.participants.Sub2APIClient", FakeClient)
    response = client.get("/api/participants/sub2api-users", **headers)

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == 51


@pytest.mark.django_db
def test_database_transfer_endpoints_require_admin_and_clear_refresh_on_import(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    monkeypatch.setattr(
        "monitor.views.database.export_database_bytes",
        lambda: b"SQLite format 3\x00backup",
    )
    captured = {}

    def fake_import(uploaded, size):
        captured["name"] = uploaded.name
        captured["size"] = size
        return "pinche.before-import.sqlite3"

    monkeypatch.setattr("monitor.views.database.import_database", fake_import)

    unauthorized = Client().get("/api/database/export")
    assert unauthorized.status_code == 401
    exported = client.get("/api/database/export", **headers)
    assert exported.status_code == 200
    assert exported.content.startswith(b"SQLite format 3\x00")

    imported = client.post(
        "/api/database/import",
        data={
            "database": SimpleUploadedFile(
                "backup.sqlite3",
                b"SQLite format 3\x00backup",
                content_type="application/vnd.sqlite3",
            )
        },
        **headers,
    )
    assert imported.status_code == 200
    assert captured["name"] == "backup.sqlite3"
    assert captured["size"] == len(b"SQLite format 3\x00backup")
    assert imported.cookies["pinche_refresh"]["max-age"] == 0


def test_sqlite_import_replaces_database_and_keeps_recovery_copy(
    monkeypatch,
    tmp_path,
):
    current_path = tmp_path / "pinche.sqlite3"
    source_path = tmp_path / "uploaded.sqlite3"

    def create_database(path, marker):
        with sqlite3.connect(path) as database:
            database.executescript(
                """
                CREATE TABLE django_migrations (app TEXT, name TEXT);
                CREATE TABLE auth_user (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_appsettings (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_participant (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_quotacycle (id INTEGER PRIMARY KEY);
                CREATE TABLE marker (value TEXT);
                """
            )
            database.execute("INSERT INTO marker(value) VALUES (?)", (marker,))

    create_database(current_path, "before")
    create_database(source_path, "after")
    payload = source_path.read_bytes()
    monkeypatch.setattr(
        database_transfer,
        "_database_path",
        lambda: current_path,
    )
    monkeypatch.setattr(
        database_transfer,
        "_expected_leaf_migrations",
        lambda: set(),
    )

    recovery_name = database_transfer.import_database(
        BytesIO(payload),
        len(payload),
    )

    with sqlite3.connect(current_path) as database:
        assert database.execute("SELECT value FROM marker").fetchone()[0] == "after"
    with sqlite3.connect(tmp_path / recovery_name) as recovery:
        assert recovery.execute("SELECT value FROM marker").fetchone()[0] == "before"


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
    headers = {
        "HTTP_AUTHORIZATION": (
            f"Bearer {success.json()['data']['access']}"
        )
    }

    rows = list(LoginEvent.objects.order_by("created_at"))
    assert len(rows) == 2
    assert rows[0].success is False
    assert rows[0].failure_reason == "用户名、密码或权限错误"
    assert rows[1].success is True
    assert rows[1].request_ip == "198.51.100.23"
    assert rows[1].remote_ip == "10.0.0.2"
    assert rows[1].webrtc_ips == ["192.168.1.8", "203.0.113.9"]
    assert rows[1].user_agent == "Audit Browser/1.0"

    audit = client.get("/api/login-events", **headers).json()["data"]
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
    headers, _ = jwt_login(client)
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
        "&usage_days=7&usage_precision=hour",
        **headers,
    ).json()["data"]
    assert daily["capacity_series"][-1]["weekly_total_usd"] == 1600.0
    assert len(daily["participant_series"][0]["points"]) == 1
    assert (
        daily["participant_series"][0]["points"][0]["weekly_usage_usd"]
        == 12.0
    )

    monthly = client.get(
        "/api/statistics?capacity_period=month&capacity_days=365",
        **headers,
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
