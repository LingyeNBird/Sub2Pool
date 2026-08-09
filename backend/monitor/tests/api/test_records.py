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
from monitor.tests.helpers import create_recommendation_snapshot, jwt_login

@pytest.mark.django_db
def test_notification_records_paginate_and_apply_all_filters():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    participant = Participant.objects.create(
        name="筛选车友",
        sub2api_user_id=88,
        share_percent=100,
    )
    now = timezone.now()
    target = NotificationEvent.objects.create(
        event_type="test",
        participant=participant,
        dedupe_key="target",
        recipient="rider@example.com",
        subject="Quota notice",
        body="target",
        status="sent",
    )
    NotificationEvent.objects.create(
        event_type="collection_error",
        participant=participant,
        dedupe_key="wrong-type",
        recipient="rider@example.com",
        subject="Quota notice",
        body="wrong type",
        status="failed",
    )
    old = NotificationEvent.objects.create(
        event_type="test",
        participant=participant,
        dedupe_key="old",
        recipient="rider@example.com",
        subject="Quota notice",
        body="old",
        status="sent",
    )
    NotificationEvent.objects.filter(pk=old.pk).update(
        created_at=now - timedelta(days=2)
    )

    response = client.get(
        "/api/notifications",
        {
            "page_size": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(hours=1)).isoformat(),
            "event_type": "test",
            "participant": participant.id,
            "subject": "quota",
            "status": "sent",
        },
        **headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {
        "total": 1,
        "sent_count": 1,
        "failed_count": 0,
    }
    assert data["pagination"]["total"] == 1
    assert [item["id"] for item in data["items"]] == [target.id]
    assert {"id": participant.id, "name": participant.name} in data[
        "filter_options"
    ]["participants"]

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
