"""Subscribe to CPA usage events through a durable local spool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
import signal
from threading import Event, Thread
import time
import uuid

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from ...cpa.collector_state import (
    mark_collector_connected,
    mark_collector_error,
    mark_collector_heartbeat,
    mark_collector_idle,
)
from ...cpa.monitoring import (
    persist_cpa_collection_connected,
    persist_cpa_collection_disconnected,
    persist_cpa_collection_opening_sample,
)
from ...cpa.usage import persist_usage_events
from ...cpa.usage_spool import CPAUsageSpool
from ...integrations.cpa import CPAClient, CPAError, CPAUsageSubscriber
from ...integrations.sub2api import WeeklyWindow
from ...models import AppSettings, MonitoredAccount

BATCH_SIZE = 200
BARRIER_TIMEOUT_SECONDS = 5.0
BOUNDARY_TIMEOUT_SECONDS = 3
CLOSING_BOUNDARY_BUDGET_SECONDS = 20.0
CONFIG_REFRESH_SECONDS = 2.0
HEARTBEAT_SECONDS = 5.0
OPENING_RETRY_SECONDS = 5.0
PING_SECONDS = 5.0
RETRY_SECONDS = 1.0


@dataclass
class _ReaderState:
    error: Exception | None = None
    last_message_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class _BarrierRequest:
    completed: Event = field(default_factory=Event)
    max_usage_id: int = 0
    error: Exception | None = None


@dataclass(frozen=True)
class _BoundarySample:
    account_id: int
    account_name: str
    observed_at: datetime
    window: WeeklyWindow | None
    reliable: bool


def _cpa_accounts() -> list[MonitoredAccount]:
    return list(MonitoredAccount.objects.filter(provider="cpa").order_by("id"))


def _cpa_collection_accounts(
    config: AppSettings,
    accounts: list[MonitoredAccount] | None = None,
) -> list[MonitoredAccount]:
    if not config.monitoring_enabled:
        return []
    rows = accounts if accounts is not None else _cpa_accounts()
    return [account for account in rows if account.enabled]


def _has_cpa_accounts() -> bool:
    return MonitoredAccount.objects.filter(provider="cpa").exists()


def _account_signature(accounts: list[MonitoredAccount] | None = None) -> tuple:
    rows = accounts if accounts is not None else _cpa_accounts()
    return tuple((account.id, account.cpa_auth_index) for account in rows)


def _connection_signature(config: AppSettings, accounts: tuple) -> tuple:
    return (
        config.monitoring_enabled,
        config.cpa_base_url,
        config.cpa_management_key_encrypted,
        config.request_timeout_seconds,
        config.verify_tls,
        accounts,
    )


def _persist_spool_batch(spool: CPAUsageSpool) -> int:
    records = spool.peek(BATCH_SIZE)
    if not records:
        return 0
    persist_usage_events(record.payload for record in records)
    spool.delete(record.id for record in records)
    return len(records)


def _read_subscription(
    subscriber: CPAUsageSubscriber,
    spool_path: Path,
    stop_event: Event,
    finished: Event,
    state: _ReaderState,
    unspooled: list[dict],
    barriers: Queue[_BarrierRequest],
) -> None:
    try:
        with CPAUsageSpool(spool_path) as spool:

            def store(record: dict) -> None:
                try:
                    spool.append([record])
                except Exception:
                    unspooled.append(record)
                    raise
                state.last_message_at = timezone.now()

            next_ping_at = time.monotonic() + PING_SECONDS
            while True:
                if stop_event.is_set():
                    subscriber.unsubscribe(store)
                    return
                try:
                    barrier = barriers.get_nowait()
                except Empty:
                    barrier = None
                if barrier is not None:
                    try:
                        subscriber.ping(store)
                        barrier.max_usage_id = spool.max_usage_id()
                    except Exception as exc:
                        barrier.error = exc
                        raise
                    finally:
                        barrier.completed.set()
                    next_ping_at = time.monotonic() + PING_SECONDS
                    continue
                record = subscriber.read_record(timeout=0.5)
                if record is not None:
                    store(record)
                now = time.monotonic()
                if now >= next_ping_at:
                    subscriber.ping(store)
                    next_ping_at = now + PING_SECONDS
    except Exception as exc:
        state.error = exc
    finally:
        state.finished_at = timezone.now()
        while True:
            try:
                pending = barriers.get_nowait()
            except Empty:
                break
            pending.error = state.error or CPAError(
                "CPA usage reader stopped before the requested barrier"
            )
            pending.completed.set()
        finished.set()


def _request_reader_barrier(
    barriers: Queue[_BarrierRequest],
    reader_finished: Event,
) -> int:
    request = _BarrierRequest()
    barriers.put(request)
    deadline = time.monotonic() + BARRIER_TIMEOUT_SECONDS
    while not request.completed.wait(timeout=0.1):
        if reader_finished.is_set():
            raise CPAError("CPA usage reader stopped before the requested barrier")
        if time.monotonic() >= deadline:
            raise CPAError("CPA usage reader barrier timed out")
    if request.error is not None:
        if isinstance(request.error, CPAError):
            raise request.error
        raise CPAError(
            f"CPA usage reader barrier failed：{request.error.__class__.__name__}"
        ) from request.error
    return request.max_usage_id


def _raise_reader_failure(state: _ReaderState) -> None:
    if state.error is not None:
        if isinstance(state.error, CPAError):
            raise state.error
        raise CPAError(
            f"CPA usage reader failed：{state.error.__class__.__name__}"
        ) from state.error
    raise CPAError("CPA usage stream disconnected")


def _close_old_connections_safely() -> None:
    try:
        close_old_connections()
    except Exception:
        pass


def _current_pending_count(fallback: int, *, unspooled_count: int = 0) -> int:
    try:
        with CPAUsageSpool() as spool:
            return spool.pending_count() + max(0, unspooled_count)
    except Exception:
        return max(0, fallback)


def _serialize_window(window: WeeklyWindow | None) -> dict | None:
    if window is None:
        return None
    return {
        "used_percent": str(window.used_percent),
        "window_seconds": window.window_seconds,
        "reset_after_seconds": window.reset_after_seconds,
        "reset_at": window.reset_at,
        "slot": window.slot,
        "sampled_at": window.sampled_at,
        "plan_type": window.plan_type,
    }


def _deserialize_window(payload: object) -> WeeklyWindow | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise CPAError("CPA subscription boundary window is invalid")
    return WeeklyWindow(
        used_percent=Decimal(str(payload["used_percent"])),
        window_seconds=int(payload["window_seconds"]),
        reset_after_seconds=int(payload["reset_after_seconds"]),
        reset_at=int(payload["reset_at"]),
        slot=str(payload["slot"]),
        sampled_at=str(payload["sampled_at"]),
        plan_type=(
            str(payload["plan_type"])
            if payload.get("plan_type") is not None
            else None
        ),
    )


def _parse_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CPAError("CPA subscription boundary timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed

def _parse_optional_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    return _parse_datetime(value)


def _request_shutdown(_signum, _frame) -> None:
    raise KeyboardInterrupt


class Command(BaseCommand):
    help = "Continuously collect CLIProxyAPI usage events"

    def _safe_status_update(self, label: str, callback) -> bool:
        try:
            callback()
        except Exception as exc:
            self.stderr.write(
                f"CPA collector status update failed ({label}): "
                f"{exc.__class__.__name__}: {exc}"
            )
            _close_old_connections_safely()
            return False
        return True

    def _safe_mark_error(
        self,
        error: Exception,
        *,
        pending_count: int,
        connected: bool,
    ) -> None:
        self._safe_status_update(
            "error",
            lambda: mark_collector_error(
                error,
                pending_count=pending_count,
                connected=connected,
            ),
        )

    def _flush_unspooled(
        self,
        spool: CPAUsageSpool,
        unspooled: list[dict],
    ) -> int:
        if not unspooled:
            return 0
        count = spool.append(unspooled)
        del unspooled[:count]
        return count

    def _query_subscription_boundaries(
        self,
        config: AppSettings,
        boundary: str,
        *,
        account_ids: set[int] | None = None,
        retain_failures: bool = False,
        deadline: float | None = None,
    ) -> list[_BoundarySample]:
        accounts = [
            account
            for account in _cpa_collection_accounts(config)
            if account_ids is None or account.id in account_ids
        ]
        if not accounts:
            return []
        samples: list[_BoundarySample] = []
        try:
            client = CPAClient(
                config,
                request_timeout_seconds=min(
                    config.request_timeout_seconds,
                    BOUNDARY_TIMEOUT_SECONDS,
                ),
            )
        except Exception as exc:
            self.stderr.write(
                f"CPA subscription {boundary} boundary client failed: "
                f"{exc.__class__.__name__}: {exc}"
            )
            if retain_failures:
                observed_at = timezone.now()
                return [
                    _BoundarySample(
                        account_id=account.id,
                        account_name=account.name,
                        observed_at=observed_at,
                        window=None,
                        reliable=False,
                    )
                    for account in accounts
                ]
            return []
        with client:
            for account in accounts:
                if deadline is not None and time.monotonic() >= deadline:
                    self.stderr.write(
                        f"CPA subscription {boundary} boundary query skipped "
                        f"after the closing time budget ({account.name})"
                    )
                    if retain_failures:
                        samples.append(
                            _BoundarySample(
                                account_id=account.id,
                                account_name=account.name,
                                observed_at=timezone.now(),
                                window=None,
                                reliable=False,
                            )
                        )
                    continue
                try:
                    window = client.query_weekly_window(
                        account.cpa_auth_index or ""
                    )
                except Exception as exc:
                    observed_at = timezone.now()
                    self.stderr.write(
                        f"CPA subscription {boundary} boundary query failed "
                        f"({account.name}): {exc.__class__.__name__}: {exc}"
                    )
                    _close_old_connections_safely()
                    if retain_failures:
                        samples.append(
                            _BoundarySample(
                                account_id=account.id,
                                account_name=account.name,
                                observed_at=observed_at,
                                window=None,
                                reliable=False,
                            )
                        )
                else:
                    samples.append(
                        _BoundarySample(
                            account_id=account.id,
                            account_name=account.name,
                            observed_at=timezone.now(),
                            window=window,
                            reliable=True,
                        )
                    )
        return samples

    @staticmethod
    def _opening_sample_records(
        samples: list[_BoundarySample],
        session_key: str,
        connected_at: datetime,
        required_usage_id: int,
    ) -> list[dict]:
        return [
            {
                "event_key": f"{session_key}:opening-sample:{sample.account_id}",
                "kind": "opening_sample",
                "account_id": sample.account_id,
                "session_key": session_key,
                "connected_at": connected_at.isoformat(),
                "sample_observed_at": sample.observed_at.isoformat(),
                "window": _serialize_window(sample.window),
                "required_usage_id": required_usage_id,
            }
            for sample in samples
            if sample.window is not None
        ]

    @staticmethod
    def _disconnect_records(
        samples: list[_BoundarySample],
        session_key: str,
        connected_at: datetime,
        disconnected_at: datetime,
        *,
        end_reliable: bool,
        required_usage_id: int,
    ) -> list[dict]:
        return [
            {
                "event_key": f"{session_key}:disconnected:{sample.account_id}",
                "kind": "disconnected",
                "account_id": sample.account_id,
                "session_key": session_key,
                "connected_at": connected_at.isoformat(),
                "disconnected_at": disconnected_at.isoformat(),
                "sample_observed_at": sample.observed_at.isoformat(),
                "window": _serialize_window(sample.window),
                "end_reliable": end_reliable,
                "required_usage_id": required_usage_id,
            }
            for sample in samples
        ]

    def _persist_pending_boundaries(
        self,
        spool: CPAUsageSpool,
        config: AppSettings,
    ) -> int:
        persisted = 0
        blocked_accounts: set[int] = set()
        for record in spool.peek_boundaries(BATCH_SIZE):
            account_id = int(record.payload.get("account_id") or 0)
            if account_id in blocked_accounts:
                continue
            required_usage_id = int(
                record.payload.get("required_usage_id") or 0
            )
            if spool.has_usage_through(required_usage_id):
                blocked_accounts.add(account_id)
                continue
            account = MonitoredAccount.objects.filter(
                pk=account_id,
                provider="cpa",
            ).first()
            if account is None:
                spool.delete_boundaries([record.id])
                persisted += 1
                continue
            kind = str(record.payload.get("kind") or "")
            session_key = str(record.payload.get("session_key") or "")
            connected_at_value = record.payload.get("connected_at")
            if kind == "connected":
                result = persist_cpa_collection_connected(
                    config,
                    account,
                    session_key=session_key,
                    connected_at=_parse_datetime(connected_at_value),
                )
            elif kind == "opening_sample":
                window = _deserialize_window(record.payload.get("window"))
                if window is None:
                    raise CPAError("CPA opening sample is missing its quota window")
                result = persist_cpa_collection_opening_sample(
                    config,
                    account,
                    session_key=session_key,
                    connected_at=_parse_datetime(connected_at_value),
                    window=window,
                    observed_at=_parse_datetime(
                        record.payload.get("sample_observed_at")
                    ),
                )
            elif kind == "disconnected":
                result = persist_cpa_collection_disconnected(
                    config,
                    account,
                    session_key=session_key,
                    connected_at=_parse_optional_datetime(connected_at_value),
                    disconnected_at=_parse_datetime(
                        record.payload.get("disconnected_at")
                    ),
                    end_reliable=bool(
                        record.payload.get("end_reliable", False)
                    ),
                    window=_deserialize_window(record.payload.get("window")),
                    sample_observed_at=(
                        _parse_datetime(
                            record.payload.get("sample_observed_at")
                        )
                        if record.payload.get("sample_observed_at")
                        else None
                    ),
                )
            else:
                raise CPAError(f"Unknown CPA collection event kind: {kind!r}")
            if result["status"] == "busy":
                blocked_accounts.add(account_id)
                continue
            spool.delete_boundaries([record.id])
            persisted += 1
        return persisted

    def _drain_persistence(
        self,
        spool: CPAUsageSpool,
        config: AppSettings,
    ) -> int:
        persisted = 0
        while True:
            count = _persist_spool_batch(spool)
            persisted += count
            if not count:
                break
        while True:
            count = self._persist_pending_boundaries(spool, config)
            persisted += count
            if not count:
                break
        return persisted

    def _capture_opening_boundaries(
        self,
        config: AppSettings,
        spool: CPAUsageSpool,
        session_key: str,
        connected_at: datetime,
        pending_account_ids: set[int],
        barriers: Queue[_BarrierRequest],
        reader_finished: Event,
    ) -> int:
        samples = self._query_subscription_boundaries(
            config,
            "opened",
            account_ids=pending_account_ids,
        )
        if not samples:
            return 0
        required_usage_id = _request_reader_barrier(
            barriers,
            reader_finished,
        )
        records = self._opening_sample_records(
            samples,
            session_key,
            connected_at,
            required_usage_id,
        )
        spool.append_boundaries(records)
        pending_account_ids.difference_update(
            sample.account_id for sample in samples
        )
        return len(records)

    @staticmethod
    def _uncertain_closing_samples(
        accounts: list[MonitoredAccount],
        observed_at: datetime,
    ) -> list[_BoundarySample]:
        return [
            _BoundarySample(
                account_id=account.id,
                account_name=account.name,
                observed_at=observed_at,
                window=None,
                reliable=False,
            )
            for account in accounts
        ]

    def _finish_subscription_session(
        self,
        *,
        config: AppSettings,
        spool: CPAUsageSpool,
        subscriber: CPAUsageSubscriber,
        session_key: str,
        connected_at: datetime,
        accounts: list[MonitoredAccount],
        reader_state: _ReaderState,
        reader_finished: Event,
        barriers: Queue[_BarrierRequest],
        capture_closing_sample: bool = True,
    ) -> None:
        end_reliable = False
        if reader_finished.is_set() or reader_state.error is not None:
            disconnected_at = reader_state.finished_at or timezone.now()
            samples = self._uncertain_closing_samples(
                accounts,
                disconnected_at,
            )
            required_usage_id = spool.max_usage_id()
        else:
            samples = (
                self._query_subscription_boundaries(
                    config,
                    "closed",
                    account_ids={account.id for account in accounts},
                    retain_failures=True,
                    deadline=(
                        time.monotonic() + CLOSING_BOUNDARY_BUDGET_SECONDS
                    ),
                )
                if capture_closing_sample
                else []
            )
            try:
                required_usage_id = _request_reader_barrier(
                    barriers,
                    reader_finished,
                )
            except Exception:
                subscriber.close()
                disconnected_at = reader_state.finished_at or timezone.now()
                samples = self._uncertain_closing_samples(
                    accounts,
                    disconnected_at,
                )
                required_usage_id = spool.max_usage_id()
            else:
                disconnected_at = timezone.now()
                end_reliable = True
                sampled_account_ids = {
                    sample.account_id for sample in samples
                }
                samples.extend(
                    self._uncertain_closing_samples(
                        [
                            account
                            for account in accounts
                            if account.id not in sampled_account_ids
                        ],
                        disconnected_at,
                    )
                )
        records = self._disconnect_records(
            samples,
            session_key,
            connected_at,
            disconnected_at,
            end_reliable=end_reliable,
            required_usage_id=required_usage_id,
        )
        spool.finish_session(session_key, records)

    def _run_subscription(
        self,
        config: AppSettings,
        spool: CPAUsageSpool,
        unspooled: list[dict],
    ) -> None:
        connection_config = config
        business_config = config
        all_accounts = _cpa_accounts()
        accounts = _cpa_collection_accounts(connection_config, all_accounts)
        account_signature = _account_signature(accounts)
        signature = _connection_signature(
            connection_config,
            account_signature,
        )
        subscriber = CPAUsageSubscriber(connection_config)
        stop_event = Event()
        reader_finished = Event()
        reader_state = _ReaderState()
        barriers: Queue[_BarrierRequest] = Queue()
        reader: Thread | None = None
        session_key = ""
        connected_at: datetime | None = None
        connected = False
        last_persisted_at: datetime | None = None
        last_heartbeat = time.monotonic()
        last_config_refresh = last_heartbeat
        next_opening_attempt = 0.0
        next_unspooled_attempt = 0.0
        next_persist_attempt = 0.0
        persistence_error: Exception | None = None
        unspooled_error: Exception | None = None
        pending_count = spool.pending_count()
        pending_openings = {account.id for account in accounts}
        close_error: Exception | None = None
        capture_closing_sample = True

        try:
            subscriber.connect()
            connected_at = timezone.now()
            session_key = uuid.uuid4().hex
            spool.begin_session(
                session_key,
                pending_openings,
                connected_at.isoformat(),
            )
            connected = True
            reader = Thread(
                target=_read_subscription,
                args=(
                    subscriber,
                    spool.path,
                    stop_event,
                    reader_finished,
                    reader_state,
                    unspooled,
                    barriers,
                ),
                name="cpa-usage-reader",
                daemon=True,
            )
            reader.start()
            self._safe_status_update("connected", mark_collector_connected)
            self.stdout.write("CPA usage collector subscribed")

            while True:
                now = time.monotonic()
                did_work = False

                if reader_finished.is_set():
                    _raise_reader_failure(reader_state)

                if pending_openings and now >= next_opening_attempt:
                    captured = self._capture_opening_boundaries(
                        connection_config,
                        spool,
                        session_key,
                        connected_at,
                        pending_openings,
                        barriers,
                        reader_finished,
                    )
                    next_opening_attempt = now + OPENING_RETRY_SECONDS
                    did_work = bool(captured)

                if unspooled and now >= next_unspooled_attempt:
                    try:
                        self._flush_unspooled(spool, unspooled)
                    except Exception as exc:
                        unspooled_error = exc
                        next_unspooled_attempt = now + RETRY_SECONDS
                        self._safe_mark_error(
                            exc,
                            pending_count=(
                                spool.pending_count() + len(unspooled)
                            ),
                            connected=not reader_finished.is_set(),
                        )
                    else:
                        unspooled_error = None
                        next_unspooled_attempt = 0.0
                        did_work = True

                if now >= next_persist_attempt:
                    try:
                        close_old_connections()
                        persisted = _persist_spool_batch(spool)
                        persisted += self._persist_pending_boundaries(
                            spool,
                            business_config,
                        )
                        pending_count = spool.pending_count()
                    except Exception as exc:
                        persistence_error = exc
                        next_persist_attempt = now + RETRY_SECONDS
                        close_old_connections()
                        self._safe_mark_error(
                            exc,
                            pending_count=(pending_count + len(unspooled)),
                            connected=True,
                        )
                    else:
                        persistence_error = None
                        if persisted:
                            last_persisted_at = timezone.now()
                            did_work = True

                if now - last_config_refresh >= CONFIG_REFRESH_SECONDS:
                    close_old_connections()
                    latest_config = AppSettings.load()
                    latest_collection_accounts = _cpa_collection_accounts(
                        latest_config
                    )
                    should_restart = (
                        not _has_cpa_accounts()
                        or not latest_config.cpa_management_key_encrypted
                        or _connection_signature(
                            latest_config,
                            _account_signature(latest_collection_accounts),
                        )
                        != signature
                    )
                    business_config = latest_config
                    if should_restart:
                        capture_closing_sample = False
                        break
                    last_config_refresh = now

                if now - last_heartbeat >= HEARTBEAT_SECONDS:
                    heartbeat_at = timezone.now()
                    spool.touch_session(
                        session_key,
                        heartbeat_at.isoformat(),
                    )
                    try:
                        pending_count = (
                            spool.pending_count() + len(unspooled)
                        )
                    except Exception as exc:
                        persistence_error = exc
                    health_error = unspooled_error or persistence_error
                    if health_error is not None:
                        self._safe_mark_error(
                            health_error,
                            pending_count=pending_count,
                            connected=True,
                        )
                    else:
                        self._safe_status_update(
                            "heartbeat",
                            lambda: mark_collector_heartbeat(
                                pending_count=pending_count,
                                last_message_at=reader_state.last_message_at,
                                last_persisted_at=last_persisted_at,
                            ),
                        )
                    last_heartbeat = now

                if not did_work:
                    time.sleep(0.05)
        finally:
            try:
                if connected and session_key and connected_at is not None:
                    self._finish_subscription_session(
                        config=connection_config,
                        spool=spool,
                        subscriber=subscriber,
                        session_key=session_key,
                        connected_at=connected_at,
                        accounts=accounts,
                        reader_state=reader_state,
                        reader_finished=reader_finished,
                        barriers=barriers,
                        capture_closing_sample=capture_closing_sample,
                    )
            except Exception as exc:
                close_error = exc
            finally:
                stop_event.set()
                if reader is not None:
                    reader.join(timeout=3)
                if reader is not None and reader.is_alive():
                    subscriber.close()
                    reader.join(timeout=2)
                else:
                    subscriber.close()
                try:
                    self._flush_unspooled(spool, unspooled)
                    self._drain_persistence(spool, business_config)
                    pending_count = spool.pending_count()
                except Exception as exc:
                    self._safe_mark_error(
                        exc,
                        pending_count=_current_pending_count(
                            pending_count + len(unspooled),
                            unspooled_count=len(unspooled),
                        ),
                        connected=False,
                    )
        if close_error is not None:
            raise close_error
        if reader_state.error is not None:
            _raise_reader_failure(reader_state)

    def _run_forever(self) -> None:
        self.stdout.write("CPA usage collector started")
        unspooled = getattr(self, "_unspooled", None)
        if unspooled is None:
            unspooled = []
            self._unspooled = unspooled
        idle_reported = False
        while True:
            _close_old_connections_safely()
            pending_count = 0
            try:
                with CPAUsageSpool() as spool:
                    spool.recover_interrupted_session()
                    self._flush_unspooled(spool, unspooled)
                    config = AppSettings.load()
                    self._drain_persistence(spool, config)
                    pending_count = spool.pending_count()
                    if not _has_cpa_accounts():
                        if not idle_reported:
                            idle_reported = self._safe_status_update(
                                "idle",
                                lambda: mark_collector_idle(
                                    pending_count=spool.pending_count()
                                ),
                            )
                        time.sleep(2)
                        continue
                    if not config.cpa_management_key_encrypted:
                        if not idle_reported:
                            idle_reported = self._safe_status_update(
                                "idle",
                                lambda: mark_collector_idle(
                                    pending_count=spool.pending_count()
                                ),
                            )
                        time.sleep(2)
                        continue
                    idle_reported = False
                    self._run_subscription(config, spool, unspooled)
            except Exception as exc:
                self.stderr.write(
                    f"CPA collector: {exc.__class__.__name__}: {exc}"
                )
                pending_count = _current_pending_count(
                    pending_count + len(unspooled),
                    unspooled_count=len(unspooled),
                )
                self._safe_mark_error(
                    exc,
                    pending_count=pending_count,
                    connected=False,
                )
                _close_old_connections_safely()
                time.sleep(5)

    def handle(self, *_args, **_options):
        self._unspooled = []
        previous_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
        try:
            self._run_forever()
        except KeyboardInterrupt:
            unspooled_count = len(self._unspooled)
            pending_count = _current_pending_count(
                unspooled_count,
                unspooled_count=unspooled_count,
            )
            self._safe_status_update(
                "idle",
                lambda: mark_collector_idle(pending_count=pending_count),
            )
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
