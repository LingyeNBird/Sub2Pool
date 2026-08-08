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
from typing import BinaryIO

from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader


MAX_IMPORT_BYTES = 512 * 1024 * 1024
REQUIRED_TABLES = {
    "auth_user",
    "django_migrations",
    "monitor_appsettings",
    "monitor_participant",
    "monitor_observation",
}


class DatabaseTransferError(RuntimeError):
    """可安全展示给管理员的数据库迁移错误。"""


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


def import_database(uploaded_file: BinaryIO, uploaded_size: int) -> str:
    """校验并覆盖当前数据库，返回覆盖前恢复副本的文件名。"""
    if uploaded_size <= 0:
        raise DatabaseTransferError("请选择非空的 SQLite 备份文件")
    if uploaded_size > MAX_IMPORT_BYTES:
        raise DatabaseTransferError("数据库备份不能超过 512 MiB")

    database_path = _database_path()
    descriptor, temporary_name = tempfile.mkstemp(suffix=".sqlite3")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    recovery_path = database_path.with_name("pinche.before-import.sqlite3")

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
                    raise DatabaseTransferError("数据库备份不能超过 512 MiB")
                destination.write(chunk)

        with temporary_path.open("rb") as uploaded:
            if uploaded.read(16) != b"SQLite format 3\x00":
                raise DatabaseTransferError("上传文件不是 SQLite 3 数据库")

        with closing(sqlite3.connect(temporary_path, timeout=30)) as source:
            _validate_source(source)

        # 关闭当前 Web 进程中的 Django 连接，再用 SQLite Backup API 复制页面。
        # run_lease 由调用方持有，后台采集进程在导入完成前不会开始新任务。
        connections.close_all()
        _backup_to(database_path, recovery_path)
        try:
            with closing(sqlite3.connect(temporary_path, timeout=30)) as source:
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
    except sqlite3.DatabaseError as exc:
        raise DatabaseTransferError(f"SQLite 备份处理失败：{exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    return recovery_path.name
