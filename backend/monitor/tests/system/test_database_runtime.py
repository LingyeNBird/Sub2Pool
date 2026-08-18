import json
import hashlib
import sqlite3
import uuid
from io import BytesIO, StringIO

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.management.commands.runmonitor import schedule_next_run
from monitor.models import (
    AppSettings,
    BlockedIPAddress,
    HistoryMaintenanceState,
    LoginEvent,
    NotificationEvent,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantBalanceOperation,
    ParticipantBalanceOperationSource,
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
)

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
                CREATE TABLE monitor_historymaintenancestate (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id BIGINT NOT NULL UNIQUE,
                    fact_revision BIGINT NOT NULL DEFAULT 0,
                    fence_token BIGINT NOT NULL DEFAULT 0,
                    lease_owner CHAR(32),
                    lease_expires_at DATETIME,
                    updated_at DATETIME NOT NULL
                );
                CREATE TABLE monitor_participantbalanceoperation (
                    id CHAR(32) PRIMARY KEY,
                    state VARCHAR(32) NOT NULL,
                    created_at DATETIME NOT NULL
                );
                CREATE TABLE monitor_participantbalanceoperationsource (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id CHAR(32) NOT NULL,
                    account_external_id BIGINT NOT NULL
                );
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

    guard = type(
        "ImportGuard",
        (),
        {
            "account_id": 0,
            "token": 1,
            "owner": uuid.uuid4(),
            "expires_at": timezone.now() + timedelta(minutes=5),
        },
    )()
    with database_transfer.stage_database_import(
        BytesIO(payload),
        len(payload),
    ) as staged:
        recovery_name = database_transfer.import_database(
            staged,
            guard=guard,
        )

    with sqlite3.connect(current_path) as database:
        assert database.execute("SELECT value FROM marker").fetchone()[0] == "after"
        imported_guard = database.execute(
            """
            SELECT fence_token, lease_owner
            FROM monitor_historymaintenancestate
            WHERE account_id = 0
            """
        ).fetchone()
        assert imported_guard == (guard.token, guard.owner.hex)
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


def test_tracked_django_bundle_uses_current_maintenance_api_contract():
    backend_root = Path(__file__).resolve().parents[3]
    javascript = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (backend_root / "static" / "frontend" / "assets").glob(
            "*.js"
        )
    )

    assert "history-rebuild-plans" in javascript
    assert "settings/fast-correction/rebuild" not in javascript
    assert "settings/data-maintenance/history-rebuild-preview" not in javascript
    assert "settings/data-maintenance/history-rebuild\"" not in javascript


def _copy_test_database(path: Path) -> None:
    connection.ensure_connection()
    with sqlite3.connect(path) as destination:
        connection.connection.backup(destination)


def _create_transfer_validation_database(
    path: Path,
    balance_table_sql: str = "",
) -> bytes:
    with sqlite3.connect(path) as database:
        database.executescript(
            f"""
            CREATE TABLE django_migrations (app TEXT, name TEXT);
            CREATE TABLE auth_user (id INTEGER PRIMARY KEY);
            CREATE TABLE monitor_appsettings (id INTEGER PRIMARY KEY);
            CREATE TABLE monitor_participant (id INTEGER PRIMARY KEY);
            CREATE TABLE monitor_observation (id INTEGER PRIMARY KEY);
            {balance_table_sql}
            """
        )
    return path.read_bytes()


def test_database_import_keeps_old_schema_error_clear(monkeypatch, tmp_path):
    payload = _create_transfer_validation_database(
        tmp_path / "old.sqlite3"
    )
    monkeypatch.setattr(
        database_transfer,
        "_expected_leaf_migrations",
        lambda: {("monitor", "0025_participantbalanceoperation")},
    )

    with pytest.raises(
        database_transfer.DatabaseTransferError,
        match="缺少迁移：monitor.0025_participantbalanceoperation",
    ):
        database_transfer.stage_database_import(
            BytesIO(payload),
            len(payload),
        )


def test_database_import_reports_corrupt_balance_table(monkeypatch, tmp_path):
    payload = _create_transfer_validation_database(
        tmp_path / "corrupt.sqlite3",
        """
        CREATE TABLE monitor_participantbalanceoperation (
            id CHAR(32) PRIMARY KEY,
            account_id BIGINT NOT NULL,
            state VARCHAR(32) NOT NULL
        );
        """,
    )
    monkeypatch.setattr(
        database_transfer,
        "_expected_leaf_migrations",
        lambda: set(),
    )

    with pytest.raises(
        database_transfer.DatabaseTransferError,
        match="余额操作表缺失或损坏",
    ):
        database_transfer.stage_database_import(
            BytesIO(payload),
            len(payload),
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "operation_state",
    ("prepared", "reconciliation_required", "remote_confirmed"),
)
def test_database_import_rejects_pending_balance_backup_without_live_writes(
    monkeypatch,
    tmp_path,
    operation_state,
):
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    participant = create_participant(name="待对账车友",
    sub2api_user_id=51,
    share_percent=100,)
    snapshot = create_recommendation_snapshot(participant)
    state, _created = HistoryMaintenanceState.objects.get_or_create(
        account_id=7,
        defaults={"fact_revision": 5},
    )
    state.fact_revision = 5
    state.save(update_fields=["fact_revision"])
    operation = ParticipantBalanceOperation.objects.create(
        participant=participant,
        sub2api_user_id=51,
        requested_balance_usd=Decimal("123.45"),
        confirmed_balance_usd=(
            Decimal("123.45")
            if operation_state == "remote_confirmed"
            else None
        ),
        state=operation_state,
        remote_confirmed_at=(
            timezone.now()
            if operation_state == "remote_confirmed"
            else None
        ),
    )
    ParticipantBalanceOperationSource.objects.create(
        operation=operation,
        account=account,
        account_external_id=7,
        base_revision=5,
        snapshot=snapshot,
        contribution_usd=Decimal("123.45"),
    )
    live_path = tmp_path / "pinche.sqlite3"
    _copy_test_database(live_path)
    monkeypatch.setattr(
        database_transfer,
        "_database_path",
        lambda: live_path,
    )
    backup = database_transfer.export_database_bytes()
    with sqlite3.connect(live_path) as live:
        live.execute(
            "DELETE FROM monitor_participantbalanceoperation WHERE id = ?",
            (operation.id.hex,),
        )
    recovery_path = live_path.with_name("pinche.before-import.sqlite3")
    live_digest = hashlib.sha256(live_path.read_bytes()).hexdigest()
    recovery_digest = (
        hashlib.sha256(recovery_path.read_bytes()).hexdigest()
        if recovery_path.exists()
        else None
    )

    with pytest.raises(
        database_transfer.DatabaseTransferError,
    ) as caught:
        database_transfer.stage_database_import(
            BytesIO(backup),
            len(backup),
        )

    message = str(caught.value)
    assert str(operation.id) in message
    assert operation_state in message
    assert "账号 7" in message
    assert hashlib.sha256(live_path.read_bytes()).hexdigest() == live_digest
    assert (
        hashlib.sha256(recovery_path.read_bytes()).hexdigest()
        if recovery_path.exists()
        else None
    ) == recovery_digest
    with sqlite3.connect(live_path) as live:
        assert (
            live.execute(
                "SELECT COUNT(*) FROM monitor_participantbalanceoperation"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.django_db(transaction=True)
def test_database_import_accepts_committed_balance_backup(
    monkeypatch,
    tmp_path,
):
    config = AppSettings.load()
    account = create_monitored_account(7)
    config.save()
    participant = create_participant(name="已提交车友",
    sub2api_user_id=51,
    share_percent=100,)
    snapshot = create_recommendation_snapshot(participant)
    state, _created = HistoryMaintenanceState.objects.get_or_create(
        account_id=7,
        defaults={"fact_revision": 5},
    )
    state.fact_revision = 5
    state.save(update_fields=["fact_revision"])
    operation = ParticipantBalanceOperation.objects.create(
        participant=participant,
        sub2api_user_id=51,
        requested_balance_usd=Decimal("123.45"),
        confirmed_balance_usd=Decimal("123.45"),
        state="committed",
        remote_confirmed_at=timezone.now(),
        committed_at=timezone.now(),
    )
    ParticipantBalanceOperationSource.objects.create(
        operation=operation,
        account=account,
        account_external_id=7,
        base_revision=5,
        snapshot=snapshot,
        contribution_usd=Decimal("123.45"),
    )
    live_path = tmp_path / "pinche.sqlite3"
    _copy_test_database(live_path)
    monkeypatch.setattr(
        database_transfer,
        "_database_path",
        lambda: live_path,
    )
    backup = database_transfer.export_database_bytes()
    with sqlite3.connect(live_path) as live:
        live.execute(
            "DELETE FROM monitor_participantbalanceoperation WHERE id = ?",
            (operation.id.hex,),
        )
        live.execute(
            "UPDATE monitor_participant SET name = ? WHERE id = ?",
            ("当前库已修改", participant.pk),
        )
    guard = type(
        "ImportGuard",
        (),
        {
            "account_id": 0,
            "token": 1,
            "owner": uuid.uuid4(),
            "expires_at": timezone.now() + timedelta(minutes=5),
        },
    )()

    with database_transfer.stage_database_import(
        BytesIO(backup),
        len(backup),
    ) as staged:
        recovery_name = database_transfer.import_database(
            staged,
            guard=guard,
        )

    with sqlite3.connect(live_path) as restored:
        restored_operation = restored.execute(
            """
            SELECT operation.state, operation_source.base_revision
            FROM monitor_participantbalanceoperation AS operation
            JOIN monitor_participantbalanceoperationsource AS operation_source
                ON operation_source.operation_id = operation.id
            WHERE operation.id = ?
            """,
            (operation.id.hex,),
        ).fetchone()
        restored_name = restored.execute(
            "SELECT name FROM monitor_participant WHERE id = ?",
            (participant.pk,),
        ).fetchone()[0]
    assert restored_operation == ("committed", 5)
    assert restored_name == "已提交车友"
    with sqlite3.connect(tmp_path / recovery_name) as recovery:
        assert (
            recovery.execute(
                "SELECT COUNT(*) FROM monitor_participantbalanceoperation"
            ).fetchone()[0]
            == 0
        )
        assert (
            recovery.execute(
                "SELECT name FROM monitor_participant WHERE id = ?",
                (participant.pk,),
            ).fetchone()[0]
            == "当前库已修改"
        )