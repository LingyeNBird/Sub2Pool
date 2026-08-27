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

from monitor.engine import run_monitor
from monitor.management.commands.runmonitor import schedule_next_run
from monitor.models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    MonitoredAccount,
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
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_recommendation_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_default_query_mode_is_passive():
    account = MonitoredAccount.objects.create(
        external_account_id=7,
        name="主账号",
    )
    assert account.quota_query_mode == "passive"


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
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        window = client.query_weekly_window(42, "passive")

    assert window.used_percent == Decimal("37.5")
    assert requested_paths == ["/api/v1/admin/accounts/42"]
    assert not any("/openai/" in path for path in requested_paths)


@pytest.mark.django_db
def test_direct_quota_exposes_upstream_plan_type():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    reset_at = int((timezone.now() + timedelta(days=3)).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/openai/accounts/42/quota"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "plan_type": "chatgptplus",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 44,
                            "limit_window_seconds": 604800,
                            "reset_after_seconds": 259200,
                            "reset_at": reset_at,
                        }
                    },
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        window = client.query_weekly_window(42, "direct")

    assert window.plan_type == "chatgptplus"
    assert window.used_percent == Decimal("44")


@pytest.mark.django_db
def test_direct_sampling_detects_plus_and_replays_with_plus_profile(
    monkeypatch,
):
    config = AppSettings.load()
    account = create_monitored_account(
        42,
        quota_query_mode="direct",
        quota_profile="auto",
    )
    create_participant(
        name="Plus 车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
        account=account,
    )
    reset_at = int((timezone.now() + timedelta(days=3)).timestamp())

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                used_percent=Decimal("44"),
                window_seconds=604800,
                reset_after_seconds=259200,
                reset_at=reset_at,
                slot="primary_window",
                plan_type="chatgptplus",
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("65"), Decimal("65"))

        def user_balance(self, _user_id):
            return UserBalance(Decimal("100"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)

    result = run_monitor(
        account_id=account.id,
        force_upstream=True,
        source="manual",
    )

    account.refresh_from_db()
    observation = Observation.objects.get(account_id=42)
    snapshot = ParticipantSnapshot.objects.get(observation=observation)
    assert result["status"] == "calibrated"
    assert account.detected_plan_type == "plus"
    assert account.effective_quota_profile == "plus"
    assert observation.model_diagnostics["quota_profile"] == "plus"
    assert observation.model_diagnostics["capacity_range_usd"] == [
        100.0,
        200.0,
    ]
    assert Decimal("1") <= observation.effective_usd_per_percent <= Decimal("2")
    assert Decimal("60") <= snapshot.recommended_balance_usd <= Decimal("100")


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
def test_account_status_resources_normalize_safe_runtime_usage_and_stats():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    now = timezone.now().replace(microsecond=0)
    reset_at = now + timedelta(days=4)
    requested: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/usage"):
            data = {
                "source": "passive",
                "updated_at": now.isoformat(),
                "five_hour": {
                    "utilization": 12.5,
                    "resets_at": (now + timedelta(hours=2)).isoformat(),
                    "remaining_seconds": 7200,
                },
                "seven_day": {
                    "utilization": 37.25,
                    "resets_at": reset_at.isoformat(),
                    "remaining_seconds": 345600,
                    "window_stats": {
                        "requests": 120,
                        "tokens": 456789,
                        "cost": 18.75,
                        "standard_cost": 15,
                        "user_cost": 20.5,
                    },
                },
            }
        elif request.url.path.endswith("/stats"):
            data = {
                "history": [
                    {"date": "2026-08-18"},
                    {"date": "2026-08-19"},
                ],
                "summary": {
                    "days": 30,
                    "actual_days_used": 12,
                    "total_cost": 81.25,
                    "total_standard_cost": 65,
                    "total_user_cost": 90,
                    "total_requests": 730,
                    "total_tokens": 3456789,
                    "avg_daily_cost": 6.7708,
                    "avg_daily_requests": 60.8333,
                    "avg_daily_tokens": 288065.75,
                    "avg_duration_ms": 1240.5,
                    "today": {
                        "date": now.date().isoformat(),
                        "cost": 4.5,
                        "user_cost": 5,
                        "requests": 42,
                        "tokens": 123456,
                    },
                },
            }
        else:
            data = {
                "id": 42,
                "name": "GPT Pro 主账号",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "schedulable": True,
                "current_concurrency": 2,
                "concurrency": 10,
                "last_used_at": now.isoformat(),
                "rate_limited_at": None,
                "rate_limit_reset_at": None,
                "overload_until": None,
                "temp_unschedulable_until": None,
                "temp_unschedulable_reason": "",
                "error_message": "",
                "credentials": {"has_access_token": True},
            }
        return httpx.Response(
            200,
            json={"code": 0, "message": "success", "data": data},
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        runtime = client.account_runtime_status(42)
        usage = client.account_usage_status(42)
        stats = client.account_usage_stats(42, days=30)

    assert runtime == {
        "name": "GPT Pro 主账号",
        "account_type": "oauth",
        "status": "active",
        "schedulable": True,
        "current_concurrency": 2,
        "concurrency_limit": 10,
        "last_used_at": now.isoformat(),
        "rate_limited_at": None,
        "rate_limit_reset_at": None,
        "overload_until": None,
        "temp_unschedulable_until": None,
        "temp_unschedulable_reason": None,
        "error_message": None,
    }
    assert usage["source"] == "passive"
    assert usage["seven_day"] == {
        "used_percent": 37.25,
        "reset_at": reset_at.isoformat(),
        "remaining_seconds": 345600,
        "request_count": 120,
        "token_count": 456789,
        "account_cost_usd": 18.75,
        "standard_cost_usd": 15.0,
        "user_cost_usd": 20.5,
    }
    assert usage["five_hour"]["used_percent"] == 12.5
    assert stats["request_count"] == 730
    assert stats["token_count"] == 3456789
    assert stats["account_cost_usd"] == 81.25
    assert stats["actual_days_used"] == 2
    assert stats["today"]["request_count"] == 42
    assert requested == [
        ("/api/v1/admin/accounts/42", {}),
        ("/api/v1/admin/accounts/42/usage", {"source": "passive"}),
        ("/api/v1/admin/accounts/42/stats", {"days": "30"}),
    ]


@pytest.mark.django_db
def test_sub2api_user_discovery_includes_admin_accounts():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/users"
        assert request.url.params["include_subscriptions"] == "false"
        assert "role" not in request.url.params
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
                            "role": "admin",
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
            "role": "admin",
        }
    ]


@pytest.mark.django_db
def test_all_user_usage_uses_read_only_breakdown_and_keeps_zero_users():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/v1/admin/users":
            data = {
                "items": [
                    {
                        "id": 51,
                        "email": "used@example.com",
                        "username": "used",
                        "status": "active",
                        "role": "user",
                    },
                    {
                        "id": 52,
                        "email": "zero@example.com",
                        "username": "",
                        "status": "active",
                        "role": "user",
                    },
                ],
                "total": 2,
                "page": 1,
                "page_size": 100,
                "pages": 1,
            }
        else:
            assert request.url.path == "/api/v1/admin/dashboard/user-breakdown"
            assert request.url.params["account_id"] == "7"
            assert request.url.params["limit"] == "200"
            data = {
                "users": [
                    {
                        "user_id": 51,
                        "email": "used@example.com",
                        "cost": "120",
                        "actual_cost": "96",
                    }
                ]
            }
        return httpx.Response(
            200,
            json={"code": 0, "message": "success", "data": data},
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        rows = client.all_user_usage_stats(
            account_id=7,
            start_date=timezone.localdate() - timedelta(days=2),
            end_date=timezone.localdate(),
            timezone_name="Asia/Shanghai",
        )

    assert requested_paths == [
        "/api/v1/admin/users",
        "/api/v1/admin/dashboard/user-breakdown",
    ]
    assert [(row.user_id, row.stats) for row in rows] == [
        (51, UsageStats(Decimal("120"), Decimal("96"))),
        (52, UsageStats(Decimal("0"), Decimal("0"))),
    ]


@pytest.mark.django_db
def test_user_balance_reads_user_detail_without_platform_quota_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "id": 51,
                    "balance": "1597.096606",
                    "frozen_balance": "2.5",
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        balance = client.user_balance(51)

    assert balance == UserBalance(
        balance=Decimal("1597.096606"),
        frozen_balance=Decimal("2.5"),
    )
    assert requested_paths == ["/api/v1/admin/users/51"]


@pytest.mark.django_db
def test_recommendation_balance_write_uses_dedicated_sub2api_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requested["method"] = request.method
        requested["path"] = request.url.path
        requested["api_key"] = request.headers["x-api-key"]
        requested["idempotency_key"] = request.headers["idempotency-key"]
        requested["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"id": 51, "balance": 123.45},
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        confirmed = client.set_user_balance_from_recommendation(
            51,
            Decimal("123.45"),
        )

    assert confirmed == Decimal("123.45")
    assert requested["method"] == "POST"
    assert requested["path"] == "/api/v1/admin/users/51/balance"
    assert requested["api_key"] == "admin-secret"
    assert requested["idempotency_key"]
    assert requested["body"] == {
        "balance": 123.45,
        "operation": "set",
        "notes": "Sub2Pool 一键应用额度建议",
    }


@pytest.mark.django_db
def test_zero_recommendation_atomically_subtracts_current_balance():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            requested.append({"method": "GET", "path": request.url.path})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "id": 51,
                        "balance": 80,
                        "frozen_balance": 0,
                    },
                },
            )
        requested.append(
            {
                "method": request.method,
                "path": request.url.path,
                "idempotency_key": request.headers["idempotency-key"],
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"id": 51, "balance": 0},
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        confirmed = client.set_user_balance_from_recommendation(
            51,
            Decimal("0"),
        )

    assert confirmed == Decimal("0")
    assert requested == [
        {"method": "GET", "path": "/api/v1/admin/users/51"},
        {
            "method": "POST",
            "path": "/api/v1/admin/users/51/balance",
            "idempotency_key": requested[1]["idempotency_key"],
            "body": {
                "balance": 80.0,
                "operation": "subtract",
                "notes": "Sub2Pool 一键应用额度建议",
            },
        },
    ]
    assert requested[1]["idempotency_key"]


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
    assert not MonitoredAccount.objects.exists()


@pytest.mark.django_db
def test_participant_user_list_endpoint_uses_saved_admin_connection(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    participant = create_participant(
        name="测试车友",
        sub2api_user_id=51,
        share_percent=Decimal("50"),
    )
    blank_name_participant = create_participant(
        name="不应作为账号身份",
        sub2api_user_id=52,
        sub2api_username="错误的旧缓存",
        share_percent=Decimal("50"),
    )

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
                    "role": "user",
                },
                {
                    "id": 52,
                    "email": "blank-name@example.com",
                    "username": "",
                    "status": "active",
                    "role": "user",
                },
            ]

    monkeypatch.setattr("monitor.views.participants.Sub2APIClient", FakeClient)
    response = client.get("/api/participants/sub2api-users", **headers)

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == 51
    participant.refresh_from_db()
    assert participant.sub2api_username == "rider"
    assert participant.sub2api_email == "rider@example.com"
    blank_name_participant.refresh_from_db()
    assert blank_name_participant.sub2api_username == ""
    assert blank_name_participant.sub2api_email == "blank-name@example.com"
    participants = client.get("/api/participants", **headers).json()["data"]
    blank_name_data = next(
        item for item in participants if item["id"] == blank_name_participant.id
    )
    assert blank_name_data["sub2api_identity"] == "blank-name@example.com"


@pytest.mark.django_db
def test_observation_records_paginate_and_filter_server_side():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    now = timezone.now()
    reset_at = now + timedelta(days=5)
    attribution_started_at = now - timedelta(days=2)

    def create_observation(
        *,
        minutes_ago: int,
        source: str,
        query_mode: str | None,
        valid: bool,
    ):
        raw_window = {}
        if query_mode:
            raw_window["query_mode"] = query_mode
        return Observation.objects.create(
            account_id=7,
            source=source,
            observed_at=now - timedelta(minutes=minutes_ago),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=attribution_started_at,
            upstream_used_percent=10,
            raw_selected_total_cost=200,
            selected_total_cost=200,
            total_standard_cost=200,
            total_actual_cost=200,
            effective_usd_per_percent=20,
            valid_sample=valid,
            raw_window=raw_window,
        )

    newest = create_observation(
        minutes_ago=5,
        source="manual",
        query_mode="direct",
        valid=True,
    )
    legacy_direct = create_observation(
        minutes_ago=10,
        source="manual",
        query_mode=None,
        valid=False,
    )
    create_observation(
        minutes_ago=15,
        source="manual",
        query_mode="passive",
        valid=True,
    )
    create_observation(
        minutes_ago=20,
        source="scheduled",
        query_mode="direct",
        valid=True,
    )

    response = client.get(
        "/api/observations",
        {
            "page": 1,
            "page_size": 1,
            "from": (now - timedelta(minutes=12)).isoformat(),
            "to": now.isoformat(),
            "source": "manual",
            "query_mode": "direct",
        },
        **headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {
        "total": 2,
        "valid_count": 1,
        "passive_count": 0,
        "excluded_count": 0,
    }
    assert data["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 2,
        "total_pages": 2,
    }
    assert [item["id"] for item in data["items"]] == [newest.id]

    second_page = client.get(
        "/api/observations",
        {
            "page": 2,
            "page_size": 1,
            "from": (now - timedelta(minutes=12)).isoformat(),
            "to": now.isoformat(),
            "source": "manual",
            "query_mode": "direct",
        },
        **headers,
    ).json()["data"]
    assert [item["id"] for item in second_page["items"]] == [legacy_direct.id]
