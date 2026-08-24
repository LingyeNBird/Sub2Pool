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
def test_regular_user_page_access_and_participant_scope_are_enforced():
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    first = create_participant(
        name="甲",
        sub2api_user_id=101,
        sub2api_username="rider-a",
        share_percent=50,
    )
    second = create_participant(
        name="乙",
        sub2api_user_id=102,
        sub2api_username="rider-b",
        share_percent=50,
    )
    third = create_participant(
        name="丙",
        sub2api_user_id=103,
        sub2api_username="unbound-rider",
        share_percent=0,
    )
    visible_account = create_monitored_account(7, name="授权账号")
    hidden_account = create_monitored_account(8, name="隐藏账号")
    client = Client()
    admin_headers, _ = jwt_login(client)

    created = client.post(
        "/api/system-users",
        data=json.dumps(
            {
                "username": "rider-viewer",
                "email": "viewer@example.com",
                "password": "Rider-Access-2026!secure",
                "is_active": True,
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["data"]["page_permissions"] == []
    assert created.json()["data"]["participant_ids"] == []
    assert created.json()["data"]["account_ids"] == []
    assert created.json()["data"]["account_names"] == []
    user_id = created.json()["data"]["id"]

    missing_scope = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": ["participants"],
                "participant_ids": [],
                "account_ids": [],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert missing_scope.status_code == 400
    assert missing_scope.json()["details"]["participant_ids"]

    missing_account_scope = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": ["account_status"],
                "participant_ids": [],
                "account_ids": [],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert missing_account_scope.status_code == 400
    assert missing_account_scope.json()["details"]["account_ids"]

    missing_particle_account_scope = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": ["particle_filter"],
                "participant_ids": [],
                "account_ids": [],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert missing_particle_account_scope.status_code == 400
    assert missing_particle_account_scope.json()["details"]["account_ids"]

    automatic_settings = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": ["settings"],
                "participant_ids": [],
                "account_ids": [],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert automatic_settings.status_code == 400
    assert automatic_settings.json()["details"]["page_permissions"]

    granted_pages = [
        "dashboard",
        "account_status",
        "participants",
        "system_users",
        "observations",
        "statistics",
        "notifications",
    ]
    effective_pages = [*granted_pages, "settings"]
    permissions = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": granted_pages,
                "participant_ids": [first.id, second.id],
                "account_ids": [visible_account.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert permissions.status_code == 200
    assert permissions.json()["data"]["page_permissions"] == granted_pages
    assert permissions.json()["data"]["participant_ids"] == [first.id, second.id]
    assert permissions.json()["data"]["account_ids"] == [visible_account.id]
    assert permissions.json()["data"]["account_names"] == [visible_account.name]

    identity_scope_attempt = client.patch(
        f"/api/system-users/{user_id}",
        data=json.dumps(
            {
                "participant_ids": [third.id],
                "account_ids": [hidden_account.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert identity_scope_attempt.status_code == 400
    assert identity_scope_attempt.json()["details"]["participant_ids"]
    assert identity_scope_attempt.json()["details"]["account_ids"]

    regular = User.objects.get(pk=user_id)
    assert regular.is_staff is False
    assert list(
        regular.quota_participants.order_by("id").values_list("id", flat=True)
    ) == [first.id, second.id]
    assert list(
        regular.visible_monitored_accounts.order_by("id").values_list(
            "id",
            flat=True,
        )
    ) == [visible_account.id]

    hidden_user = User.objects.create_user(
        username="hidden-rider",
        password="hidden-rider-password",
    )
    third.authorized_users.add(hidden_user)
    hidden_account.authorized_users.add(hidden_user)

    config = AppSettings.load()
    config.save()
    now = timezone.now()
    attribution_started_at = now - timedelta(days=2)
    for participant, cost in ((first, 120), (second, 240), (third, 360)):
        ParticipantUsageSample.objects.create(
            participant=participant,
            account_id=7,
            attribution_started_at=attribution_started_at,
            observed_at=now,
            balance_usd=Decimal("500"),
            raw_selected_cost=cost,
            selected_cost=cost,
        )
        create_recommendation_snapshot(participant)
        NotificationEvent.objects.create(
            event_type="recommendation_changed",
            participant=participant,
            dedupe_key=f"participant-{participant.id}",
            recipient="audit@example.com",
            subject=f"{participant.name} 通知",
            body="participant notification",
            status="sent",
        )
    NotificationEvent.objects.create(
        event_type="collection_error",
        participant=None,
        dedupe_key="system-notification",
        recipient="audit@example.com",
        subject="系统通知",
        body="system notification",
        status="sent",
    )

    regular_client = Client()
    regular_headers, logged_in = jwt_login(
        regular_client,
        username="rider-viewer",
        password="Rider-Access-2026!secure",
    )
    login_identity = logged_in.json()["data"]
    assert login_identity["is_staff"] is False
    assert login_identity["page_permissions"] == effective_pages
    me_identity = regular_client.get("/api/auth/me", **regular_headers).json()[
        "data"
    ]
    assert me_identity["is_staff"] is False
    assert me_identity["page_permissions"] == effective_pages

    statistics = regular_client.get("/api/statistics", **regular_headers)
    assert statistics.status_code == 200
    assert [
        item["participant_id"]
        for item in statistics.json()["data"]["participant_series"]
    ] == [first.id, second.id]

    visible_participants = regular_client.get(
        "/api/participants",
        **regular_headers,
    )
    assert visible_participants.status_code == 200
    assert [item["id"] for item in visible_participants.json()["data"]] == [
        first.id,
        second.id,
    ]
    visible_allocation = regular_client.get(
        "/api/quota-allocation",
        **regular_headers,
    )
    assert visible_allocation.status_code == 200
    assert [
        item["id"] for item in visible_allocation.json()["data"]["participants"]
    ] == [first.id, second.id]
    assert {
        allocation["participant_id"]
        for pool in visible_allocation.json()["data"]["pools"]
        for allocation in pool["allocations"]
    } == {first.id, second.id}
    forbidden_allocation_write = regular_client.put(
        "/api/quota-allocation",
        data=json.dumps({"pools": []}),
        content_type="application/json",
        **regular_headers,
    )
    assert forbidden_allocation_write.status_code == 403


    dashboard = regular_client.get("/api/dashboard", **regular_headers)
    assert dashboard.status_code == 200
    assert {
        item["id"] for item in dashboard.json()["data"]["participants"]
    } == {first.id, second.id}

    account_status = regular_client.get("/api/account-status", **regular_headers)
    assert account_status.status_code == 200
    assert [
        item["id"] for item in account_status.json()["data"]["accounts"]
    ] == [visible_account.id]

    observations = regular_client.get("/api/observations", **regular_headers)
    assert observations.status_code == 200
    observed_participant_ids = {
        snapshot["participant_id"]
        for row in observations.json()["data"]["items"]
        for snapshot in row["participants"]
    }
    assert observed_participant_ids == {first.id, second.id}

    notifications = regular_client.get("/api/notifications", **regular_headers)
    assert notifications.status_code == 200
    assert {
        item["participant_name"]
        for item in notifications.json()["data"]["items"]
    } == {None, first.name, second.name}

    system_users = regular_client.get("/api/system-users", **regular_headers)
    assert system_users.status_code == 200
    assert third.name not in {
        name
        for user in system_users.json()["data"]
        for name in user["participant_names"]
    }
    assert hidden_account.name not in {
        name
        for user in system_users.json()["data"]
        for name in user["account_names"]
    }

    for denied_path in (
        "/api/login-events",
        "/api/settings",
        "/api/particle-trajectory",
    ):
        assert regular_client.get(denied_path, **regular_headers).status_code == 403

    assert (
        regular_client.post(
            "/api/participants",
            data=json.dumps(
                {
                    "name": "越权创建",
                    "sub2api_user_id": 104,
                    "share_percent": 0,
                }
            ),
            content_type="application/json",
            **regular_headers,
        ).status_code
        == 403
    )
    assert (
        regular_client.post(
            "/api/monitor/run",
            data=json.dumps({"account_id": 7}),
            content_type="application/json",
            **regular_headers,
        ).status_code
        == 403
    )
    assert (
        regular_client.patch(
            f"/api/system-users/{user_id}/permissions",
            data=json.dumps(
                {
                    "page_permissions": ["dashboard"],
                    "participant_ids": [first.id],
                    "account_ids": [visible_account.id],
                }
            ),
            content_type="application/json",
            **regular_headers,
        ).status_code
        == 403
    )

    updated = client.patch(
        f"/api/system-users/{user_id}/permissions",
        data=json.dumps(
            {
                "page_permissions": ["statistics"],
                "participant_ids": [second.id],
                "account_ids": [visible_account.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert updated.status_code == 200
    assert regular_client.get("/api/participants", **regular_headers).status_code == 403
    filtered = regular_client.get("/api/statistics", **regular_headers)
    assert [
        item["participant_id"]
        for item in filtered.json()["data"]["participant_series"]
    ] == [second.id]
    assert regular_client.get("/api/auth/me", **regular_headers).json()["data"][
        "page_permissions"
    ] == ["statistics", "settings"]

    deleted = client.delete(
        f"/api/system-users/{user_id}",
        **admin_headers,
    )
    assert deleted.status_code == 200
    assert not User.objects.filter(pk=user_id).exists()

@pytest.mark.django_db
def test_system_user_validation_returns_field_errors():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.post(
        "/api/system-users",
        data=json.dumps(
            {
                "username": "viewer",
                "email": "viewer@example.com",
                "password": "123",
                "is_active": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["message"] == "系统用户校验失败"
    assert payload["details"]["password"]
    assert not get_user_model().objects.filter(username="viewer").exists()


@pytest.mark.django_db
def test_settings_access_is_automatic_for_unassigned_system_users():
    get_user_model().objects.create_user(
        username="settings-only",
        password="settings-only-password",
    )
    client = Client()
    headers, logged_in = jwt_login(
        client,
        username="settings-only",
        password="settings-only-password",
    )

    assert logged_in.json()["data"]["page_permissions"] == ["settings"]
    assert client.get("/api/auth/me", **headers).json()["data"][
        "page_permissions"
    ] == ["settings"]
    assert client.get("/api/settings/my-api-key", **headers).status_code == 200
    assert client.get("/api/settings", **headers).status_code == 403


@pytest.mark.django_db
def test_non_participant_page_grants_allow_read_dependencies():
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    viewer = User.objects.create_user(
        username="page-viewer",
        password="page-viewer-password",
    )
    account = create_monitored_account(7, name="授权账号")
    admin_client = Client()
    admin_headers, _ = jwt_login(admin_client)
    response = admin_client.patch(
        f"/api/system-users/{viewer.id}/permissions",
        data=json.dumps(
            {
                "page_permissions": [
                    "particle_filter",
                    "login_records",
                ],
                "participant_ids": [],
                "account_ids": [account.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert response.status_code == 200

    client = Client()
    headers, _ = jwt_login(
        client,
        username="page-viewer",
        password="page-viewer-password",
    )
    for path in (
        "/api/particle-trajectory",
        "/api/login-events",
        "/api/ip-blocks",
        "/api/settings/monitored-accounts",
    ):
        assert client.get(path, **headers).status_code != 403
    assert client.get("/api/settings", **headers).status_code == 403
    assert client.get("/api/settings/my-api-key", **headers).status_code == 200
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
    payload["fast_correction_rules"] = [
        {
            "model_pattern": " GPT-5.6* ",
            "source_multiplier": "2.50",
            "target_multiplier": "2.500",
        },
        {
            "model_pattern": "*",
            "source_multiplier": "2",
            "target_multiplier": "2.5",
        },
    ]
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
    assert config.fast_correction_enabled is True
    assert config.fast_correction_rules == [
        {
            "model_pattern": "gpt-5.6*",
            "source_multiplier": "2.5",
            "target_multiplier": "2.5",
        },
        {
            "model_pattern": "*",
            "source_multiplier": "2",
            "target_multiplier": "2.5",
        },
    ]

    invalid = client.patch(
        "/api/settings",
        data=json.dumps(
            {
                "fast_correction_rules": [
                    {
                        "model_pattern": "*",
                        "source_multiplier": "2.5",
                        "target_multiplier": "2",
                    }
                ]
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert invalid.status_code == 400
    assert "不能小于源倍率" in str(invalid.json())
