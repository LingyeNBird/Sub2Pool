"""Durable local queue for CPA usage records awaiting business persistence."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterable

from django.conf import settings

from .usage import prepare_usage_payload_for_spool


class CPAUsageSpoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpooledUsageRecord:
    id: int
    payload: dict


@dataclass(frozen=True)
class SpooledBoundaryRecord:
    id: int
    event_key: str
    payload: dict


def default_spool_path() -> Path:
    return Path(settings.DATA_DIR) / "cpa-usage-spool.sqlite3"


class CPAUsageSpool:
    """A separate SQLite queue so main-database stalls cannot block RESP reads."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_spool_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(self.path, timeout=30)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA busy_timeout=30000")
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    enqueued_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS boundary_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    enqueued_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_session (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    session_key TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    connected_at TEXT,
                    heartbeat_at TEXT NOT NULL,
                    account_ids TEXT NOT NULL
                )
                """
            )
            session_columns = {
                str(row[1])
                for row in self.connection.execute(
                    "PRAGMA table_info(collector_session)"
                ).fetchall()
            }
            if "connected_at" not in session_columns:
                self.connection.execute(
                    "ALTER TABLE collector_session ADD COLUMN connected_at TEXT"
                )
            self._migrate_legacy_boundaries()
            self.connection.commit()
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"无法打开 CPA usage 持久队列：{exc.__class__.__name__}"
            ) from exc

    def __enter__(self) -> "CPAUsageSpool":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _legacy_session_key(
        event_key: str,
        boundary: str,
        account_id: int,
    ) -> str:
        suffixes = [f":{boundary}:{account_id}"]
        if boundary == "closed":
            suffixes.append(f":interrupted-close:{account_id}")
        for suffix in suffixes:
            if event_key.endswith(suffix):
                session_key = event_key[: -len(suffix)]
                if session_key:
                    return session_key[:64]
        raise CPAUsageSpoolError(
            f"旧版 CPA 订阅边界 {event_key!r} 缺少可恢复的 session"
        )

    def _migrate_legacy_boundaries(self) -> None:
        rows = self.connection.execute(
            """
            SELECT id, event_key, payload
            FROM boundary_events
            ORDER BY id
            """
        ).fetchall()
        decoded: list[tuple[int, str, dict]] = []
        opening_times: dict[tuple[str, int], object] = {}
        for row_id, event_key, serialized in rows:
            try:
                payload = json.loads(serialized)
            except (json.JSONDecodeError, TypeError) as exc:
                raise CPAUsageSpoolError(
                    f"CPA 订阅边界队列记录 {row_id} 已损坏"
                ) from exc
            if not isinstance(payload, dict):
                raise CPAUsageSpoolError(
                    f"CPA 订阅边界队列记录 {row_id} 不是对象"
                )
            decoded.append((int(row_id), str(event_key), payload))
            if payload.get("kind"):
                continue
            boundary = str(payload.get("boundary") or "")
            if boundary not in {"opened", "closed"}:
                raise CPAUsageSpoolError(
                    f"旧版 CPA 订阅边界 {event_key!r} 类型无效"
                )
            try:
                account_id = int(payload["account_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CPAUsageSpoolError(
                    f"旧版 CPA 订阅边界 {event_key!r} 缺少账号"
                ) from exc
            session_key = self._legacy_session_key(
                str(event_key),
                boundary,
                account_id,
            )
            if boundary == "opened":
                opening_times[(session_key, account_id)] = payload.get(
                    "observed_at"
                )

        for row_id, event_key, payload in decoded:
            if payload.get("kind"):
                continue
            boundary = str(payload["boundary"])
            account_id = int(payload["account_id"])
            session_key = self._legacy_session_key(
                event_key,
                boundary,
                account_id,
            )
            observed_at = payload.get("observed_at")
            window = payload.get("window")
            required_usage_id = int(payload.get("required_usage_id") or 0)
            if boundary == "opened":
                if window is None:
                    migrated = {
                        "kind": "connected",
                        "account_id": account_id,
                        "session_key": session_key,
                        "connected_at": observed_at,
                        "required_usage_id": required_usage_id,
                    }
                else:
                    migrated = {
                        "kind": "opening_sample",
                        "account_id": account_id,
                        "session_key": session_key,
                        "connected_at": observed_at,
                        "sample_observed_at": observed_at,
                        "window": window,
                        "required_usage_id": required_usage_id,
                    }
            else:
                migrated = {
                    "kind": "disconnected",
                    "account_id": account_id,
                    "session_key": session_key,
                    "connected_at": opening_times.get(
                        (session_key, account_id)
                    ),
                    "disconnected_at": observed_at,
                    "sample_observed_at": (
                        observed_at if window is not None else None
                    ),
                    "window": window,
                    "end_reliable": bool(payload.get("reliable", True)),
                    "required_usage_id": required_usage_id,
                }
            self.connection.execute(
                "UPDATE boundary_events SET payload = ? WHERE id = ?",
                (
                    json.dumps(
                        migrated,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row_id,
                ),
            )

    def append(self, records: Iterable[dict]) -> int:
        serialized = [
            json.dumps(
                prepare_usage_payload_for_spool(record),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for record in records
            if isinstance(record, dict)
        ]
        if not serialized:
            return 0
        try:
            with self.connection:
                self.connection.executemany(
                    "INSERT INTO usage_events (payload) VALUES (?)",
                    ((payload,) for payload in serialized),
                )
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"写入 CPA usage 持久队列失败：{exc.__class__.__name__}"
            ) from exc
        return len(serialized)

    def peek(self, limit: int) -> list[SpooledUsageRecord]:
        count = max(1, min(int(limit), 1000))
        try:
            rows = self.connection.execute(
                "SELECT id, payload FROM usage_events ORDER BY id LIMIT ?",
                (count,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"读取 CPA usage 持久队列失败：{exc.__class__.__name__}"
            ) from exc
        records: list[SpooledUsageRecord] = []
        for row_id, serialized in rows:
            try:
                payload = json.loads(serialized)
            except (json.JSONDecodeError, TypeError) as exc:
                raise CPAUsageSpoolError(
                    f"CPA usage 持久队列记录 {row_id} 已损坏"
                ) from exc
            if not isinstance(payload, dict):
                raise CPAUsageSpoolError(
                    f"CPA usage 持久队列记录 {row_id} 不是对象"
                )
            records.append(SpooledUsageRecord(id=int(row_id), payload=payload))
        return records

    def delete(self, record_ids: Iterable[int]) -> int:
        ids = [int(record_id) for record_id in record_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _record_id in ids)
        try:
            with self.connection:
                cursor = self.connection.execute(
                    f"DELETE FROM usage_events WHERE id IN ({placeholders})",
                    ids,
                )
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"清理 CPA usage 持久队列失败：{exc.__class__.__name__}"
            ) from exc
        return cursor.rowcount

    def count(self) -> int:
        try:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM usage_events"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"统计 CPA usage 持久队列失败：{exc.__class__.__name__}"
            ) from exc
        return int(row[0]) if row is not None else 0

    def max_usage_id(self) -> int:
        try:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM usage_events"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"读取 CPA usage 队列边界失败：{exc.__class__.__name__}"
            ) from exc
        return int(row[0]) if row is not None else 0

    def has_usage_through(self, record_id: int) -> bool:
        if record_id <= 0:
            return False
        try:
            row = self.connection.execute(
                "SELECT 1 FROM usage_events WHERE id <= ? LIMIT 1",
                (int(record_id),),
            ).fetchone()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"检查 CPA usage 队列边界失败：{exc.__class__.__name__}"
            ) from exc
        return row is not None

    @staticmethod
    def _boundary_values(records: Iterable[dict]) -> list[tuple[str, str]]:
        values: list[tuple[str, str]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            event_key = str(record.get("event_key") or "").strip()
            if not event_key:
                raise CPAUsageSpoolError("CPA 订阅边界缺少 event_key")
            payload = {
                key: value for key, value in record.items() if key != "event_key"
            }
            values.append(
                (
                    event_key,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
        return values

    def _insert_boundaries(self, records: Iterable[dict]) -> int:
        values = self._boundary_values(records)
        if not values:
            return 0
        before = self.connection.total_changes
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO boundary_events (event_key, payload)
            VALUES (?, ?)
            """,
            values,
        )
        return self.connection.total_changes - before

    def append_boundaries(self, records: Iterable[dict]) -> int:
        try:
            with self.connection:
                return self._insert_boundaries(records)
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"写入 CPA 订阅边界队列失败：{exc.__class__.__name__}"
            ) from exc

    def peek_boundaries(self, limit: int) -> list[SpooledBoundaryRecord]:
        count = max(1, min(int(limit), 1000))
        try:
            rows = self.connection.execute(
                """
                SELECT id, event_key, payload
                FROM boundary_events
                ORDER BY id
                LIMIT ?
                """,
                (count,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"读取 CPA 订阅边界队列失败：{exc.__class__.__name__}"
            ) from exc
        records: list[SpooledBoundaryRecord] = []
        for row_id, event_key, serialized in rows:
            try:
                payload = json.loads(serialized)
            except (json.JSONDecodeError, TypeError) as exc:
                raise CPAUsageSpoolError(
                    f"CPA 订阅边界队列记录 {row_id} 已损坏"
                ) from exc
            if not isinstance(payload, dict):
                raise CPAUsageSpoolError(
                    f"CPA 订阅边界队列记录 {row_id} 不是对象"
                )
            records.append(
                SpooledBoundaryRecord(
                    id=int(row_id),
                    event_key=str(event_key),
                    payload=payload,
                )
            )
        return records

    def delete_boundaries(self, record_ids: Iterable[int]) -> int:
        ids = [int(record_id) for record_id in record_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _record_id in ids)
        try:
            with self.connection:
                cursor = self.connection.execute(
                    f"DELETE FROM boundary_events WHERE id IN ({placeholders})",
                    ids,
                )
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"清理 CPA 订阅边界队列失败：{exc.__class__.__name__}"
            ) from exc
        return cursor.rowcount

    def boundary_count(self) -> int:
        try:
            row = self.connection.execute(
                "SELECT COUNT(*) FROM boundary_events"
            ).fetchone()
        except sqlite3.Error as exc:
            raise CPAUsageSpoolError(
                f"统计 CPA 订阅边界队列失败：{exc.__class__.__name__}"
            ) from exc
        return int(row[0]) if row is not None else 0

    def pending_count(self) -> int:
        return self.count() + self.boundary_count()

    def recover_interrupted_session(self) -> int:
        try:
            with self.connection:
                row = self.connection.execute(
                    """
                    SELECT session_key, connected_at, heartbeat_at, account_ids
                    FROM collector_session
                    WHERE id = 1 AND active = 1
                    """
                ).fetchone()
                if row is None:
                    return 0
                session_key, connected_at, heartbeat_at, serialized_accounts = row
                account_ids = json.loads(serialized_accounts)
                if not isinstance(account_ids, list):
                    raise CPAUsageSpoolError(
                        "CPA collector session 账号列表已损坏"
                    )
                required_usage_id = self.max_usage_id()
                records = [
                    {
                        "event_key": (
                            f"{session_key}:interrupted-disconnect:{int(account_id)}"
                        ),
                        "kind": "disconnected",
                        "account_id": int(account_id),
                        "session_key": str(session_key),
                        "connected_at": connected_at,
                        "disconnected_at": str(heartbeat_at),
                        "sample_observed_at": None,
                        "window": None,
                        "end_reliable": False,
                        "required_usage_id": required_usage_id,
                    }
                    for account_id in account_ids
                ]
                inserted = self._insert_boundaries(records)
                self.connection.execute(
                    "UPDATE collector_session SET active = 0 WHERE id = 1"
                )
                return inserted
        except CPAUsageSpoolError:
            raise
        except (json.JSONDecodeError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise CPAUsageSpoolError(
                f"恢复 CPA collector session 失败：{exc.__class__.__name__}"
            ) from exc

    def begin_session(
        self,
        session_key: str,
        account_ids: Iterable[int],
        heartbeat_at: str,
    ) -> None:
        normalized_account_ids = sorted(
            {int(account_id) for account_id in account_ids}
        )
        serialized_accounts = json.dumps(
            normalized_account_ids,
            separators=(",", ":"),
        )
        connection_records = [
            {
                "event_key": f"{session_key}:connected:{account_id}",
                "kind": "connected",
                "account_id": account_id,
                "session_key": session_key,
                "connected_at": heartbeat_at,
                "required_usage_id": 0,
            }
            for account_id in normalized_account_ids
        ]
        try:
            with self.connection:
                active = self.connection.execute(
                    "SELECT active FROM collector_session WHERE id = 1"
                ).fetchone()
                if active is not None and int(active[0]) == 1:
                    raise CPAUsageSpoolError(
                        "上一个 CPA collector session 尚未恢复"
                    )
                self.connection.execute(
                    """
                    INSERT INTO collector_session (
                        id,
                        session_key,
                        active,
                        connected_at,
                        heartbeat_at,
                        account_ids
                    )
                    VALUES (1, ?, 1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        session_key = excluded.session_key,
                        active = 1,
                        connected_at = excluded.connected_at,
                        heartbeat_at = excluded.heartbeat_at,
                        account_ids = excluded.account_ids
                    """,
                    (
                        session_key,
                        heartbeat_at,
                        heartbeat_at,
                        serialized_accounts,
                    ),
                )
                self._insert_boundaries(connection_records)
        except CPAUsageSpoolError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"开始 CPA collector session 失败：{exc.__class__.__name__}"
            ) from exc

    def touch_session(self, session_key: str, heartbeat_at: str) -> None:
        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    UPDATE collector_session
                    SET heartbeat_at = ?
                    WHERE id = 1 AND active = 1 AND session_key = ?
                    """,
                    (heartbeat_at, session_key),
                )
                if cursor.rowcount != 1:
                    raise CPAUsageSpoolError(
                        "CPA collector session 已失效"
                    )
        except CPAUsageSpoolError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"更新 CPA collector session 失败：{exc.__class__.__name__}"
            ) from exc

    def finish_session(
        self,
        session_key: str,
        boundaries: Iterable[dict],
    ) -> int:
        try:
            with self.connection:
                inserted = self._insert_boundaries(boundaries)
                cursor = self.connection.execute(
                    """
                    UPDATE collector_session
                    SET active = 0
                    WHERE id = 1 AND active = 1 AND session_key = ?
                    """,
                    (session_key,),
                )
                if cursor.rowcount != 1:
                    raise CPAUsageSpoolError(
                        "CPA collector session 已失效"
                    )
                return inserted
        except CPAUsageSpoolError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise CPAUsageSpoolError(
                f"结束 CPA collector session 失败：{exc.__class__.__name__}"
            ) from exc
