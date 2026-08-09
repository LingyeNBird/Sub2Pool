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
from monitor.sub2api import (
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
def test_regular_user_only_reads_bound_participant_statistics():
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    first = Participant.objects.create(
        name="甲",
        sub2api_user_id=101,
        sub2api_username="rider-a",
        share_percent=50,
    )
    second = Participant.objects.create(
        name="乙",
        sub2api_user_id=102,
        sub2api_username="rider-b",
        share_percent=50,
    )
    third = Participant.objects.create(
        name="丙",
        sub2api_user_id=103,
        sub2api_username="unbound-rider",
        share_percent=0,
    )
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
                "participant_ids": [first.id, second.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert created.status_code == 201
    user_id = created.json()["data"]["id"]
    regular = User.objects.get(pk=user_id)
    assert regular.is_staff is False
    assert list(
        regular.quota_participants.order_by("id").values_list("id", flat=True)
    ) == [first.id, second.id]

    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    now = timezone.now()
    attribution_started_at = now - timedelta(days=2)
    for participant, cost in ((first, 120), (second, 240)):
        ParticipantUsageSample.objects.create(
            participant=participant,
            account_id=7,
            attribution_started_at=attribution_started_at,
            observed_at=now,
            balance_usd=Decimal("500"),
            raw_selected_cost=cost,
            selected_cost=cost,
        )

    regular_client = Client()
    regular_headers, logged_in = jwt_login(
        regular_client,
        username="rider-viewer",
        password="Rider-Access-2026!secure",
    )
    assert logged_in.json()["data"]["is_staff"] is False
    assert regular_client.get("/api/auth/me", **regular_headers).json()["data"][
        "is_staff"
    ] is False

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
    assert third.id not in {
        item["id"] for item in visible_participants.json()["data"]
    }
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
        regular_client.put(
            f"/api/participants/{first.id}",
            data=json.dumps({"name": "越权修改"}),
            content_type="application/json",
            **regular_headers,
        ).status_code
        == 403
    )
    assert (
        regular_client.delete(
            f"/api/participants/{first.id}",
            **regular_headers,
        ).status_code
        == 403
    )
    for admin_path in (
        "/api/dashboard",
        "/api/login-events",
        "/api/settings",
        "/api/system-users",
    ):
        assert regular_client.get(admin_path, **regular_headers).status_code == 403

    updated = client.patch(
        f"/api/system-users/{user_id}",
        data=json.dumps(
            {
                "username": "rider-viewer",
                "email": "viewer@example.com",
                "is_active": True,
                "participant_ids": [second.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert updated.status_code == 200
    filtered = regular_client.get("/api/statistics", **regular_headers)
    assert [
        item["participant_id"]
        for item in filtered.json()["data"]["participant_series"]
    ] == [second.id]
    filtered_participants = regular_client.get(
        "/api/participants",
        **regular_headers,
    )
    assert [item["id"] for item in filtered_participants.json()["data"]] == [
        second.id
    ]

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
    participant = Participant.objects.create(
        name="甲",
        sub2api_user_id=101,
        share_percent=100,
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
                "participant_ids": [participant.id],
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
