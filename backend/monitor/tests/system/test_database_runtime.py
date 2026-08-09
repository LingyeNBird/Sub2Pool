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
                CREATE TABLE monitor_observation (id INTEGER PRIMARY KEY);
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

@pytest.mark.django_db
def test_django_serves_vue_entry_for_root_and_history_routes():
    client = Client()

    for route in ("/", "/participants", "/settings"):
        response = client.get(route)
        assert response.status_code == 200
        assert b'id="app"' in response.content
        assert b"/static/frontend/assets/index-" in response.content

    assert client.get("/api/unknown").status_code == 404
