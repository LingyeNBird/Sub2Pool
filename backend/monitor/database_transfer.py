"""SQLite 数据库的完整备份与恢复。

导出使用 SQLite Online Backup API，能在后台监控仍运行时得到一致快照。导入前先校验
SQLite 完整性、关键表和迁移版本，并在数据目录保留一份覆盖前的恢复副本。
"""

from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
import uuid

from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

if TYPE_CHECKING:
    from .history_state import LeaseGuard


MAX_IMPORT_BYTES = 512 * 1024 * 1024
REQUIRED_TABLES = {
    "auth_user",
    "django_migrations",
    "monitor_appsettings",
    "monitor_participant",
    "monitor_observation",
}

PENDING_BALANCE_STATES = {
    "prepared",
    "reconciliation_required",
    "remote_confirmed",
}


class DatabaseTransferError(RuntimeError):
    """可安全展示给管理员的数据库迁移错误。"""


class StagedDatabaseImport:
    """Validated upload copy that has not touched the live database."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "StagedDatabaseImport":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


def _database_path() -> Path:
    engine = connection.settings_dict.get("ENGINE", "")
    if engine != "django.db.backends.sqlite3":
        raise DatabaseTransferError("数据库导入导出仅支持当前项目使用的 SQLite")
    return Path(connection.settings_dict["NAME"])


def _backup_to(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path, timeout=30)) as source:
        with closing(sqlite3.connect(destination_path, timeout=30)) as destination:
            source.backup(destination)


def export_database_bytes() -> bytes:
    """生成包含 WAL 中已提交数据的一致 SQLite 快照。"""
    database_path = _database_path()
    if not database_path.exists():
        raise DatabaseTransferError("当前 SQLite 数据库文件不存在")

    descriptor, temporary_name = tempfile.mkstemp(suffix=".sqlite3")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        _backup_to(database_path, temporary_path)
        return temporary_path.read_bytes()
    finally:
        temporary_path.unlink(missing_ok=True)


def _expected_leaf_migrations() -> set[tuple[str, str]]:
    loader = MigrationLoader(connection, ignore_no_migrations=True)
    return set(loader.graph.leaf_nodes())




def _reject_unfinished_balance_operations(
    source: sqlite3.Connection,
) -> None:
    try:
        rows = list(
            source.execute(
                """
                SELECT pending.id, pending.state, operation_source.account_external_id
                FROM (
                    SELECT id, state, created_at
                    FROM monitor_participantbalanceoperation
                    WHERE state <> 'committed' OR state IS NULL
                    ORDER BY created_at, id
                    LIMIT 21
                ) AS pending
                LEFT JOIN monitor_participantbalanceoperationsource AS operation_source
                    ON operation_source.operation_id = pending.id
                ORDER BY
                    pending.created_at,
                    pending.id,
                    operation_source.account_external_id
                """
            )
        )
    except sqlite3.DatabaseError as exc:
        raise DatabaseTransferError(
            "备份的余额操作表缺失或损坏，无法安全导入"
        ) from exc
    if not rows:
        return

    operations: dict[tuple[object, object], list[object]] = {}
    for raw_id, raw_state, raw_account_id in rows:
        operations.setdefault((raw_id, raw_state), [])
        if raw_account_id is not None:
            operations[(raw_id, raw_state)].append(raw_account_id)

    diagnostics = []
    for (raw_id, raw_state), raw_account_ids in list(operations.items())[:20]:
        try:
            operation_id = str(uuid.UUID(str(raw_id)))
        except (AttributeError, TypeError, ValueError):
            operation_id = "<无效 UUID>"
        state = (
            str(raw_state)
            if raw_state in PENDING_BALANCE_STATES
            else "<无效状态>"
        )
        account_ids = []
        for raw_account_id in raw_account_ids:
            try:
                account_ids.append(str(int(raw_account_id)))
            except (TypeError, ValueError):
                account_ids.append("<无效账号>")
        accounts = "、".join(account_ids) if account_ids else "<缺少账号来源>"
        diagnostics.append(
            f"{operation_id}（状态 {state}，账号 {accounts}）"
        )
    suffix = "；另有未列出的操作" if len(operations) > 20 else ""
    raise DatabaseTransferError(
        "备份包含未完成的上游余额操作，已拒绝导入；"
        "请先在来源系统完成对账后重新导出："
        + "、".join(diagnostics)
        + suffix
    )
def _validate_source(source: sqlite3.Connection) -> None:
    integrity_rows = [row[0] for row in source.execute("PRAGMA integrity_check")]
    if integrity_rows != ["ok"]:
        raise DatabaseTransferError("上传的 SQLite 数据库完整性检查失败")

    tables = {
        row[0]
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = REQUIRED_TABLES - tables
    if missing_tables:
        names = "、".join(sorted(missing_tables))
        raise DatabaseTransferError(f"上传文件不是有效的本系统数据库，缺少表：{names}")

    applied = {
        (row[0], row[1])
        for row in source.execute("SELECT app, name FROM django_migrations")
    }
    missing_migrations = _expected_leaf_migrations() - applied
    if missing_migrations:
        versions = "、".join(
            f"{app}.{name}" for app, name in sorted(missing_migrations)
        )
        raise DatabaseTransferError(
            f"备份版本早于当前程序，缺少迁移：{versions}；请先用对应旧版本恢复后再升级"
        )
    _reject_unfinished_balance_operations(source)


def _install_import_guard(
    source: sqlite3.Connection,
    guard: "LeaseGuard",
) -> int:
    if guard.account_id != 0:
        raise DatabaseTransferError("数据库导入必须持有全局 fencing 租约")
    source.execute(
        """
        UPDATE monitor_historymaintenancestate
        SET lease_owner = NULL, lease_expires_at = NULL
        """
    )
    row = source.execute(
        """
        SELECT fence_token
        FROM monitor_historymaintenancestate
        WHERE account_id = 0
        """
    ).fetchone()
    staged_token = max(int(row[0]) if row else 0, guard.token) + 1
    now = timezone.now().isoformat(sep=" ")
    source.execute(
        """
        INSERT INTO monitor_historymaintenancestate (
            account_id,
            fact_revision,
            fence_token,
            lease_owner,
            lease_expires_at,
            updated_at
        )
        VALUES (0, 0, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            fence_token = excluded.fence_token,
            lease_owner = excluded.lease_owner,
            lease_expires_at = excluded.lease_expires_at,
            updated_at = excluded.updated_at
        """,
        (
            staged_token,
            guard.owner.hex,
            guard.expires_at.isoformat(sep=" "),
            now,
        ),
    )
    # Always revoke consent and the copied schedule. Preserve a usable identity
    # for deduplication/withdrawal on same-key restores, but a different SECRET_KEY
    # cannot decrypt it. Clear only that unusable delivery identity/state; never
    # try to withdraw from the source installation under a newly generated key.
    if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='monitor_researchsettings'").fetchone():
        from .research.transport import DeliveryError, decode_identity_seed

        source.execute("""
            UPDATE monitor_researchsettings SET enabled=0, consent_hash='',
                consent_at=NULL, config_revision=config_revision+1,
                lease_token='', lease_until=NULL, next_run_at=NULL,
                summary='{}', last_computed_at=NULL, failures=0,
                last_status='disabled', last_error=''
        """)
        for identity_id, encrypted, sent_endpoint in source.execute(
            "SELECT id, identity_encrypted, last_sent_endpoint FROM monitor_researchsettings"
        ).fetchall():
            if encrypted:
                try:
                    decode_identity_seed(encrypted)
                except DeliveryError:
                    pass
                else:
                    continue
            notice = (
                "导入的科研签名身份不可用，已重置本地发送状态；重新授权后将创建新身份。"
                "旧贡献未自动撤回，请在原实例撤回，或使用原密钥和备份恢复后撤回。"
            ) if encrypted or sent_endpoint else ""
            source.execute("""
                UPDATE monitor_researchsettings SET identity_encrypted='',
                    report_revision=0, last_sent_at=NULL, last_sent_hash='',
                    last_sent_endpoint='', last_error=? WHERE id=?
            """, (notice, identity_id))
    source.commit()
    return staged_token


def stage_database_import(
    uploaded_file: BinaryIO,
    uploaded_size: int,
) -> StagedDatabaseImport:
    """Copy and validate an upload before acquiring any live database lease."""
    if uploaded_size <= 0:
        raise DatabaseTransferError("请选择非空的 SQLite 备份文件")
    if uploaded_size > MAX_IMPORT_BYTES:
        raise DatabaseTransferError("数据库备份不能超过 512 MiB")

    descriptor, temporary_name = tempfile.mkstemp(suffix=".sqlite3")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        written = 0
        with temporary_path.open("wb") as destination:
            chunks = (
                uploaded_file.chunks()
                if hasattr(uploaded_file, "chunks")
                else iter(lambda: uploaded_file.read(1024 * 1024), b"")
            )
            for chunk in chunks:
                written += len(chunk)
                if written > MAX_IMPORT_BYTES:
                    raise DatabaseTransferError(
                        "数据库备份不能超过 512 MiB"
                    )
                destination.write(chunk)

        with temporary_path.open("rb") as uploaded:
            if uploaded.read(16) != b"SQLite format 3\x00":
                raise DatabaseTransferError("上传文件不是 SQLite 3 数据库")

        with closing(sqlite3.connect(temporary_path, timeout=30)) as source:
            _validate_source(source)
        return StagedDatabaseImport(temporary_path)
    except sqlite3.DatabaseError as exc:
        temporary_path.unlink(missing_ok=True)
        raise DatabaseTransferError(f"SQLite 备份处理失败：{exc}") from exc
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def import_database(
    staged: StagedDatabaseImport,
    *,
    guard: "LeaseGuard",
) -> str:
    """Replace SQLite from a validated stage while carrying the global fence."""
    database_path = _database_path()
    recovery_path = database_path.with_name("pinche.before-import.sqlite3")

    try:
        with closing(sqlite3.connect(staged.path, timeout=30)) as source:
            staged_guard_token = _install_import_guard(source, guard)

        # 关闭当前 Web 进程中的 Django 连接，再用 SQLite Backup API 复制页面。
        # run_lease 由调用方持有，后台采集进程在导入完成前不会开始新任务。
        connections.close_all()
        _backup_to(database_path, recovery_path)
        try:
            with closing(sqlite3.connect(staged.path, timeout=30)) as source:
                with closing(
                    sqlite3.connect(database_path, timeout=30)
                ) as target:
                    source.backup(target)
                    violations = target.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchone()
                    if violations:
                        raise DatabaseTransferError(
                            "备份中存在无效的外键关系"
                        )
        except Exception:
            _backup_to(recovery_path, database_path)
            raise
        finally:
            connections.close_all()
        guard.token = staged_guard_token
    except sqlite3.DatabaseError as exc:
        raise DatabaseTransferError(f"SQLite 备份处理失败：{exc}") from exc

    return recovery_path.name
