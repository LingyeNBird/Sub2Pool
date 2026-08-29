"""Persistent health reporting for the CPA usage collector."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from ..models import CPACollectorState

COLLECTOR_STATE_ID = 1
STALE_AFTER = timedelta(seconds=15)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def get_collector_status(*, include_error: bool = True) -> dict:
    state = CPACollectorState.objects.filter(pk=COLLECTOR_STATE_ID).first()
    if state is None:
        result = {
            "state": "idle",
            "connected": False,
            "stale": False,
            "connected_at": None,
            "heartbeat_at": None,
            "last_message_at": None,
            "last_persisted_at": None,
            "pending_count": 0,
            "last_error_at": None,
        }
        if include_error:
            result["last_error"] = ""
        return result

    stale = bool(
        state.connected
        and (
            state.heartbeat_at is None
            or timezone.now() - state.heartbeat_at > STALE_AFTER
        )
    )
    if stale:
        status = "stale"
    elif state.last_error:
        status = "error"
    elif state.connected:
        status = "connected"
    else:
        status = "idle"
    result = {
        "state": status,
        "connected": state.connected and not stale,
        "stale": stale,
        "connected_at": _iso(state.connected_at),
        "heartbeat_at": _iso(state.heartbeat_at),
        "last_message_at": _iso(state.last_message_at),
        "last_persisted_at": _iso(state.last_persisted_at),
        "pending_count": state.pending_count,
        "last_error_at": _iso(state.last_error_at),
    }
    if include_error:
        result["last_error"] = state.last_error
    return result


def mark_collector_connected() -> None:
    now = timezone.now()
    CPACollectorState.objects.update_or_create(
        pk=COLLECTOR_STATE_ID,
        defaults={
            "connected": True,
            "connected_at": now,
            "heartbeat_at": now,
            "pending_count": 0,
            "last_error": "",
        },
    )


def mark_collector_heartbeat(
    *,
    pending_count: int,
    last_message_at: datetime | None,
    last_persisted_at: datetime | None,
) -> None:
    defaults = {
        "connected": True,
        "heartbeat_at": timezone.now(),
        "pending_count": max(0, pending_count),
        "last_error": "",
    }
    if last_message_at is not None:
        defaults["last_message_at"] = last_message_at
    if last_persisted_at is not None:
        defaults["last_persisted_at"] = last_persisted_at
    CPACollectorState.objects.update_or_create(
        pk=COLLECTOR_STATE_ID,
        defaults=defaults,
    )


def mark_collector_error(
    error: Exception,
    *,
    pending_count: int,
    connected: bool = False,
) -> None:
    now = timezone.now()
    detail = f"{error.__class__.__name__}: {error}"[:2000]
    CPACollectorState.objects.update_or_create(
        pk=COLLECTOR_STATE_ID,
        defaults={
            "connected": connected,
            "heartbeat_at": now,
            "pending_count": max(0, pending_count),
            "last_error": detail,
            "last_error_at": now,
        },
    )


def mark_collector_idle(*, pending_count: int = 0) -> None:
    CPACollectorState.objects.update_or_create(
        pk=COLLECTOR_STATE_ID,
        defaults={
            "connected": False,
            "heartbeat_at": timezone.now(),
            "pending_count": max(0, pending_count),
            "last_error": "",
        },
    )
