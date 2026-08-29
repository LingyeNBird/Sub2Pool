from __future__ import annotations

import json
import socket
import sqlite3
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from queue import Queue
from threading import Event

import httpx
import pytest
from django.utils import timezone

from monitor.cpa.pricing import calculate_cpa_cost
from monitor.cpa.monitoring import (
    _capture_cpa_window,
    persist_cpa_collection_connected,
    persist_cpa_collection_disconnected,
    persist_cpa_collection_opening_sample,
)
from monitor.cpa.usage import (
    cpa_event_cost,
    persist_usage_event,
    persist_usage_events,
)
from monitor.cpa.usage_spool import CPAUsageSpool
from monitor.history_state import LeaseGuard
from monitor.integrations.cpa import CPAClient, CPAError, CPAUsageSubscriber
from monitor.integrations.cpa.client import management_base_url
from monitor.integrations.sub2api import WeeklyWindow
from monitor.integrations.cpa.usage_stream import (
    _RESPConnection,
    decode_usage_message,
)
from monitor.management.commands import runcpacollector
from monitor.models import (
    AppSettings,
    CPAAccountCollectionInterval,
    CPAUsageEvent,
    Observation,
)
from monitor.reporting.usage import cpa_api_key_usage_series
from monitor.secrets import encrypt_secret
from monitor.replay import exclude_observation
from monitor.tests.helpers import create_cpa_account


PRICING = {
    "gpt-test": {
        "input": "1",
        "cached_input": "0.1",
        "output": "10",
    }
}


def calculate(**overrides):
    values = {
        "pricing": PRICING,
        "model": "gpt-test",
        "input_tokens": 1_000_000,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "service_tier": "default",
        "fast_multiplier": Decimal("2.5"),
        "double_billing_enabled": False,
        "double_billing_threshold_tokens": 272_000,
        "double_billing_multiplier": Decimal("2"),
    }
    values.update(overrides)
    return calculate_cpa_cost(**values)


def test_cpa_cost_uses_three_token_prices_and_only_fast_service_tiers():
    normal = calculate(
        input_tokens=1_000_000,
        cached_input_tokens=250_000,
        output_tokens=100_000,
    )
    fast = calculate(service_tier="fast")
    priority = calculate(service_tier="PRIORITY")

    assert normal is not None
    assert normal.base_cost_usd == Decimal("1.775")
    assert normal.estimated_cost_usd == Decimal("1.775")
    assert normal.fast_multiplier == Decimal("1")
    assert fast is not None and fast.estimated_cost_usd == Decimal("2.5")
    assert priority is not None and priority.estimated_cost_usd == Decimal("2.5")


def test_cpa_double_billing_uses_strict_input_threshold_and_whole_request():
    at_threshold = calculate(
        input_tokens=272_000,
        output_tokens=100_000,
        double_billing_enabled=True,
    )
    above_threshold = calculate(
        input_tokens=272_001,
        output_tokens=100_000,
        service_tier="priority",
        double_billing_enabled=True,
    )

    assert at_threshold is not None
    assert at_threshold.base_cost_usd == Decimal("1.272")
    assert at_threshold.double_billing_multiplier == Decimal("1")
    assert at_threshold.estimated_cost_usd == Decimal("1.272")
    assert above_threshold is not None
    assert above_threshold.base_cost_usd == Decimal("1.272001")
    assert above_threshold.fast_multiplier == Decimal("2.5")
    assert above_threshold.double_billing_multiplier == Decimal("2")
    assert above_threshold.estimated_cost_usd == Decimal("6.3600050")


@pytest.mark.django_db
def test_cpa_usage_event_uses_either_tier_field_and_never_stores_raw_api_key():
    account = create_cpa_account()
    config = AppSettings.load()
    config.cpa_model_pricing = PRICING
    config.cpa_fast_multiplier = Decimal("2.5")
    config.save()
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "request-1",
        "timestamp": (timezone.now() + timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "api_key": "sk-cpa-secret-value",
        "service_tier": "priority",
        "response_service_tier": "default",
        "tokens": {
            "input_tokens": 1_000_000,
            "cached_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 1_000_000,
        },
    }

    assert persist_usage_event(payload) == "created"
    assert persist_usage_event(payload) == "duplicate"
    event = CPAUsageEvent.objects.get()
    assert event.account == account
    assert event.api_key_hash and event.api_key_hash != payload["api_key"]
    assert event.api_key_hint == "alue"
    estimated_cost, unknown_model = cpa_event_cost(event, config)
    assert estimated_cost == Decimal("2.5")
    assert unknown_model is False
    assert "api_key" not in {field.name for field in CPAUsageEvent._meta.fields}


@pytest.mark.django_db
def test_cpa_usage_batch_is_atomic_and_deduplicates_in_memory():
    account = create_cpa_account()
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "batch-request",
        "timestamp": (timezone.now() + timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "tokens": {"input_tokens": 1, "total_tokens": 1},
    }

    statuses = persist_usage_events(
        [
            payload,
            payload,
            {**payload, "request_id": "batch-request-2"},
            {**payload, "timestamp": "invalid"},
        ]
    )

    assert statuses == [
        "created",
        "duplicate",
        "created",
        "ignored_invalid_timestamp",
    ]
    assert CPAUsageEvent.objects.count() == 2


@pytest.mark.django_db
def test_cpa_usage_ignores_events_before_local_account_connection():
    account = create_cpa_account()
    payload = {
        "auth_index": account.cpa_auth_index,
        "timestamp": (account.created_at - timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "tokens": {"input_tokens": 1},
    }

    assert persist_usage_event(payload) == "ignored_before_connection"
    assert not CPAUsageEvent.objects.exists()

@pytest.mark.django_db
def test_cpa_usage_persists_for_disabled_monitored_account():
    account = create_cpa_account(enabled=False)
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "disabled-account-request",
        "timestamp": (account.created_at + timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "tokens": {"input_tokens": 1, "total_tokens": 1},
    }

    assert runcpacollector._has_cpa_accounts() is True
    assert persist_usage_event(payload) == "created"
    assert CPAUsageEvent.objects.get().account == account


@pytest.mark.django_db
def test_cpa_collection_controls_skip_disabled_and_globally_paused_accounts(
    monkeypatch,
):
    account = create_cpa_account(enabled=False)
    config = AppSettings.load()
    config.monitoring_enabled = True

    class ForbiddenCPAClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("disabled CPA account must not be queried")

    monkeypatch.setattr(runcpacollector, "CPAClient", ForbiddenCPAClient)
    disabled_accounts = runcpacollector._cpa_collection_accounts(config)
    disabled_signature = runcpacollector._connection_signature(
        config,
        runcpacollector._account_signature(disabled_accounts),
    )
    assert disabled_accounts == []
    assert (
        runcpacollector.Command()._query_subscription_boundaries(
            config,
            "opened",
        )
        == []
    )

    account.enabled = True
    account.save(update_fields=["enabled", "updated_at"])
    enabled_accounts = runcpacollector._cpa_collection_accounts(config)
    enabled_signature = runcpacollector._connection_signature(
        config,
        runcpacollector._account_signature(enabled_accounts),
    )
    assert [item.id for item in enabled_accounts] == [account.id]
    assert enabled_signature != disabled_signature

    config.monitoring_enabled = False
    paused_accounts = runcpacollector._cpa_collection_accounts(config)
    paused_signature = runcpacollector._connection_signature(
        config,
        runcpacollector._account_signature(paused_accounts),
    )
    assert paused_accounts == []
    assert paused_signature != enabled_signature
    assert (
        runcpacollector.Command()._query_subscription_boundaries(
            config,
            "opened",
        )
        == []
    )
    assert runcpacollector._has_cpa_accounts() is True


@pytest.mark.django_db
def test_cpa_usage_rejects_missing_or_invalid_event_time():
    account = create_cpa_account()
    payload = {
        "auth_index": account.cpa_auth_index,
        "model": "gpt-test",
        "tokens": {"input_tokens": 1},
    }

    assert persist_usage_event(payload) == "ignored_invalid_timestamp"
    assert (
        persist_usage_event({**payload, "timestamp": "not-a-time"})
        == "ignored_invalid_timestamp"
    )
    assert not CPAUsageEvent.objects.exists()


@pytest.mark.django_db
def test_cpa_statistics_group_local_cost_by_hashed_api_key():
    account = create_cpa_account()
    config = AppSettings.load()
    config.cpa_model_pricing = PRICING
    config.save()
    now = timezone.now()
    for offset, raw_key, input_tokens in (
        (1, "sk-first-1111", 1_000_000),
        (2, "sk-first-1111", 500_000),
        (3, "sk-second-2222", 250_000),
    ):
        assert (
            persist_usage_event(
                {
                    "auth_index": account.cpa_auth_index,
                    "timestamp": (now + timedelta(seconds=offset)).isoformat(),
                    "model": "gpt-test",
                    "api_key": raw_key,
                    "tokens": {
                        "input_tokens": input_tokens,
                        "total_tokens": input_tokens,
                    },
                }
            )
            == "created"
        )

    series = cpa_api_key_usage_series(
        config=config,
        account=account,
        location=timezone.get_current_timezone(),
        now=now + timedelta(minutes=1),
        usage_days=7,
        usage_precision="hour",
    )

    assert [item["api_key_name"] for item in series] == [
        "API Key ····1111",
        "API Key ····2222",
    ]
    assert series[0]["request_count"] == 2
    assert series[0]["token_count"] == 1_500_000
    assert series[0]["total_usage_usd"] == 1.5
    assert series[0]["points"][0]["usage_usd"] == 1.5




def _resp_bulk(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return f"${len(encoded)}\r\n".encode("ascii") + encoded + b"\r\n"


def _resp_array(*values: str | int) -> bytes:
    payload = bytearray(f"*{len(values)}\r\n".encode("ascii"))
    for value in values:
        if isinstance(value, int):
            payload.extend(f":{value}\r\n".encode("ascii"))
        else:
            payload.extend(_resp_bulk(value))
    return bytes(payload)

def test_cpa_usage_subscriber_preserves_partial_frame_across_timeout(monkeypatch):
    usage = {
        "auth_index": "fragmented-auth",
        "timestamp": "2026-08-28T10:00:00Z",
        "model": "gpt-test",
        "tokens": {"input_tokens": 9},
    }
    frame = _resp_array("message", "usage", json.dumps(usage))
    split = len(frame) // 2
    actions: list[bytes | Exception] = [
        b"+OK\r\n",
        _resp_array("subscribe", "usage", 1),
        frame[:split],
        socket.timeout(),
        frame[split:],
    ]

    class FakeSocket:
        def __init__(self):
            self.sent = bytearray()

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, _count):
            action = actions.pop(0)
            if isinstance(action, Exception):
                raise action
            return action

        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        "monitor.integrations.cpa.usage_stream.socket.create_connection",
        lambda *_args, **_kwargs: FakeSocket(),
    )
    config = AppSettings(
        cpa_base_url="http://cpa.example:8317",
        cpa_management_key_encrypted=encrypt_secret("management-secret"),
    )

    with CPAUsageSubscriber(config) as subscriber:
        assert subscriber.read_record() is None
        assert subscriber.read_record() == usage


def test_cpa_usage_probe_authenticates_without_subscribing(monkeypatch):
    responses = bytearray(b"+OK\r\n")

    class FakeSocket:
        def __init__(self):
            self.sent = bytearray()
            self.closed = False

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, count):
            chunk = bytes(responses[:count])
            del responses[:count]
            return chunk

        def settimeout(self, _timeout):
            pass

        def close(self):
            self.closed = True

    sock = FakeSocket()
    monkeypatch.setattr(
        "monitor.integrations.cpa.usage_stream.socket.create_connection",
        lambda *_args, **_kwargs: sock,
    )
    config = AppSettings(cpa_base_url="http://saved.example:8317")

    result = CPAUsageSubscriber(
        config,
        base_url="http://temporary.example:8317",
        management_key="temporary-secret",
    ).probe()

    assert result == {"resp_transport": "ok", "resp_auth": "ok"}
    assert b"AUTH" in sock.sent
    assert b"temporary-secret" in sock.sent
    assert b"SUBSCRIBE" not in sock.sent
    assert sock.closed is True


@pytest.mark.django_db
def test_persisted_collection_interval_keeps_optional_percentage_samples():
    config = AppSettings.load()
    account = create_cpa_account()
    connected_at = account.created_at + timedelta(seconds=1)
    observed_at = connected_at + timedelta(seconds=1)
    reset_at = observed_at + timedelta(days=3)
    window = WeeklyWindow(
        used_percent=Decimal("49"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400,
        reset_at=int(reset_at.timestamp()),
        slot="secondary_window",
        sampled_at=observed_at.isoformat(),
        plan_type="pro",
    )

    connected = persist_cpa_collection_connected(
        config,
        account,
        session_key="sampled-session",
        connected_at=connected_at,
    )
    result = persist_cpa_collection_opening_sample(
        config,
        account,
        session_key="sampled-session",
        connected_at=connected_at,
        window=window,
        observed_at=observed_at,
    )

    interval = CPAAccountCollectionInterval.objects.get(account=account)
    observation = Observation.objects.get(pk=result["observation_id"])
    assert connected["status"] == "created"
    assert interval.opening_observation == observation
    assert observation.upstream_used_percent == Decimal("49")
    assert observation.source == "cpa_subscription_opened"
    assert observation.raw_window["collection_sample_kind"] == "opening"
    assert "connection_boundary" not in observation.raw_window
    assert observation.attribution_started_at == observed_at
    assert observation.interval_used_percent == Decimal("0")
    assert observation.selected_total_cost == Decimal("0")

    closed_at = observed_at + timedelta(hours=1)
    closed_window = WeeklyWindow(
        used_percent=Decimal("50"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400 - 3600,
        reset_at=int(reset_at.timestamp()),
        slot="secondary_window",
        sampled_at=closed_at.isoformat(),
        plan_type="pro",
    )
    disconnected_at = closed_at + timedelta(seconds=1)
    closed_result = persist_cpa_collection_disconnected(
        config,
        account,
        session_key="sampled-session",
        connected_at=connected_at,
        disconnected_at=disconnected_at,
        end_reliable=True,
        window=closed_window,
        sample_observed_at=closed_at,
    )

    interval.refresh_from_db()
    closed = Observation.objects.get(pk=closed_result["observation_id"])
    assert interval.disconnected_at == disconnected_at
    assert interval.end_reliable is True
    assert interval.closing_observation == closed
    assert closed.upstream_used_percent == Decimal("50")
    assert closed.source == "cpa_subscription_closed"
    assert closed.raw_window["collection_sample_kind"] == "closing"
    assert "connection_boundary" not in closed.raw_window
    observation.full_clean()
    closed.full_clean()
    assert closed.attribution_started_at == observed_at
    assert closed.interval_used_percent == Decimal("1")


@pytest.mark.django_db
def test_regular_cpa_observation_requires_collection_interval_coverage():
    config = AppSettings.load()
    account = create_cpa_account()
    observed_at = account.created_at + timedelta(seconds=1)
    reset_at = observed_at + timedelta(days=3)
    window = WeeklyWindow(
        used_percent=Decimal("49"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400,
        reset_at=int(reset_at.timestamp()),
        slot="secondary_window",
        sampled_at=observed_at.isoformat(),
        plan_type="pro",
    )
    guard = LeaseGuard.acquire(account.fact_key)
    try:
        result = _capture_cpa_window(
            config,
            account,
            "scheduled",
            guard,
            window=window,
            observed_at=observed_at,
        )
    finally:
        guard.release()

    observation = Observation.objects.get(pk=result["observation_id"])
    assert "connection_baseline" not in observation.raw_window
    assert observation.exclusion_source == "automatic"
    assert (
        observation.exclusion_reason
        == "CPA usage 采集区间未覆盖该百分比观测"
    )

    persist_cpa_collection_connected(
        config,
        account,
        session_key="late-connection-fact",
        connected_at=observed_at - timedelta(seconds=1),
    )
    observation.refresh_from_db()
    assert observation.exclusion_source == ""
    assert observation.attribution_started_at == observed_at
    assert observation.raw_window["replay_segment_reason"] == (
        "provider_collection_baseline"
    )


@pytest.mark.django_db
def test_delayed_cpa_usage_reconciles_percentage_observation_and_stays_immutable():
    config = AppSettings.load()
    config.cpa_model_pricing = PRICING
    config.save()
    account = create_cpa_account()
    connected_at = account.created_at + timedelta(seconds=1)
    opened_at = connected_at + timedelta(seconds=1)
    reset_at = opened_at + timedelta(days=3)

    def quota_window(observed_at, percent):
        return WeeklyWindow(
            used_percent=Decimal(percent),
            window_seconds=604800,
            reset_after_seconds=max(
                0,
                int((reset_at - observed_at).total_seconds()),
            ),
            reset_at=int(reset_at.timestamp()),
            slot="secondary_window",
            sampled_at=observed_at.isoformat(),
            plan_type="pro",
        )

    persist_cpa_collection_connected(
        config,
        account,
        session_key="delayed-usage",
        connected_at=connected_at,
    )
    persist_cpa_collection_opening_sample(
        config,
        account,
        session_key="delayed-usage",
        connected_at=connected_at,
        window=quota_window(opened_at, "49"),
        observed_at=opened_at,
    )
    observed_at = opened_at + timedelta(minutes=1)
    guard = LeaseGuard.acquire(account.fact_key)
    try:
        result = _capture_cpa_window(
            config,
            account,
            "scheduled",
            guard,
            window=quota_window(observed_at, "50"),
            observed_at=observed_at,
        )
    finally:
        guard.release()
    observation = Observation.objects.get(pk=result["observation_id"])
    assert observation.raw_selected_total_cost == Decimal("0")

    assert (
        persist_usage_event(
            {
                "auth_index": account.cpa_auth_index,
                "request_id": "arrived-after-percentage",
                "timestamp": (
                    opened_at + timedelta(seconds=30)
                ).isoformat(),
                "model": "gpt-test",
                "tokens": {
                    "input_tokens": 1_000_000,
                    "total_tokens": 1_000_000,
                },
            }
        )
        == "created"
    )

    observation.refresh_from_db()
    assert observation.raw_selected_total_cost == Decimal("1")
    assert observation.selected_total_cost == Decimal("1.000000")
    exclude_observation(observation, "百分比异常")
    assert CPAUsageEvent.objects.filter(
        account=account,
        request_id="arrived-after-percentage",
    ).exists()


@pytest.mark.django_db
def test_collection_events_are_idempotent_by_session_key():
    config = AppSettings.load()
    account = create_cpa_account()
    connected_at = account.created_at + timedelta(seconds=1)
    observed_at = connected_at + timedelta(seconds=1)
    window = WeeklyWindow(
        used_percent=Decimal("49"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400,
        reset_at=int((observed_at + timedelta(days=3)).timestamp()),
        slot="secondary_window",
        sampled_at=observed_at.isoformat(),
        plan_type="pro",
    )

    connected = persist_cpa_collection_connected(
        config,
        account,
        session_key="idempotent-session",
        connected_at=connected_at,
    )
    duplicate_connected = persist_cpa_collection_connected(
        config,
        account,
        session_key="idempotent-session",
        connected_at=connected_at,
    )
    first = persist_cpa_collection_opening_sample(
        config,
        account,
        session_key="idempotent-session",
        connected_at=connected_at,
        window=window,
        observed_at=observed_at,
    )
    duplicate = persist_cpa_collection_opening_sample(
        config,
        account,
        session_key="idempotent-session",
        connected_at=connected_at,
        window=window,
        observed_at=observed_at,
    )

    assert connected["status"] == "created"
    assert duplicate_connected["status"] == "duplicate"
    assert first["status"] == "calibrated"
    assert duplicate["status"] == "duplicate"
    assert duplicate["observation_id"] == first["observation_id"]
    assert CPAAccountCollectionInterval.objects.filter(account=account).count() == 1
    assert Observation.objects.filter(account_id=account.fact_key).count() == 1


@pytest.mark.django_db
def test_new_collection_session_closes_stale_open_interval():
    config = AppSettings.load()
    account = create_cpa_account()
    stale_connected_at = account.created_at + timedelta(seconds=1)
    new_connected_at = stale_connected_at + timedelta(minutes=5)
    stale = CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="stale-session",
        connected_at=stale_connected_at,
    )

    result = persist_cpa_collection_connected(
        config,
        account,
        session_key="new-session",
        connected_at=new_connected_at,
    )

    stale.refresh_from_db()
    current = CPAAccountCollectionInterval.objects.get(
        account=account,
        session_key="new-session",
    )
    assert result["status"] == "created"
    assert stale.disconnected_at == new_connected_at
    assert stale.end_reliable is False
    assert current.connected_at == new_connected_at
    assert current.disconnected_at is None
    assert (
        CPAAccountCollectionInterval.objects.filter(
            account=account,
            disconnected_at__isnull=True,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_uncertain_subscription_disconnect_does_not_invent_percentage_sample():
    config = AppSettings.load()
    account = create_cpa_account()
    connected_at = account.created_at + timedelta(seconds=1)
    opened_at = connected_at + timedelta(seconds=1)
    window = WeeklyWindow(
        used_percent=Decimal("49"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400,
        reset_at=int((opened_at + timedelta(days=3)).timestamp()),
        slot="secondary_window",
        sampled_at=opened_at.isoformat(),
        plan_type="pro",
    )
    persist_cpa_collection_connected(
        config,
        account,
        session_key="interrupted-session",
        connected_at=connected_at,
    )
    persist_cpa_collection_opening_sample(
        config,
        account,
        session_key="interrupted-session",
        connected_at=connected_at,
        window=window,
        observed_at=opened_at,
    )
    observation_count = Observation.objects.filter(
        account_id=account.fact_key
    ).count()
    disconnected_at = opened_at + timedelta(minutes=5)

    result = persist_cpa_collection_disconnected(
        config,
        account,
        session_key="interrupted-session",
        connected_at=connected_at,
        disconnected_at=disconnected_at,
        end_reliable=False,
    )

    interval = CPAAccountCollectionInterval.objects.get(account=account)
    assert result["status"] == "created"
    assert interval.disconnected_at == disconnected_at
    assert interval.end_reliable is False
    assert interval.closing_observation is None
    assert (
        Observation.objects.filter(account_id=account.fact_key).count()
        == observation_count
    )


@pytest.mark.django_db
def test_cpa_usage_spool_survives_reopen_without_raw_api_key(tmp_path):
    account = create_cpa_account()
    spool_path = tmp_path / "cpa-usage-spool.sqlite3"
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "durable-request",
        "timestamp": (account.created_at + timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "api_key": "sk-never-write-this-secret",
        "tokens": {"input_tokens": 1, "total_tokens": 1},
    }

    with CPAUsageSpool(spool_path) as spool:
        assert spool.append([payload]) == 1
        assert spool.count() == 1

    assert all(
        b"sk-never-write-this-secret" not in path.read_bytes()
        for path in tmp_path.glob("cpa-usage-spool.sqlite3*")
    )
    with CPAUsageSpool(spool_path) as spool:
        records = spool.peek(10)
        assert len(records) == 1
        assert "api_key" not in records[0].payload
        assert persist_usage_events([records[0].payload]) == ["created"]
        assert spool.delete([records[0].id]) == 1
        assert spool.count() == 0

    assert persist_usage_event(payload) == "duplicate"


def test_cpa_spool_recovers_interrupted_session_as_uncertain_disconnect(tmp_path):
    spool_path = tmp_path / "cpa-usage-spool.sqlite3"
    connected_at = "2026-08-29T09:55:00+00:00"
    heartbeat_at = "2026-08-29T10:00:00+00:00"
    with CPAUsageSpool(spool_path) as spool:
        spool.begin_session("session-1", [7, 9], connected_at)
        spool.touch_session("session-1", heartbeat_at)

    with CPAUsageSpool(spool_path) as spool:
        assert spool.recover_interrupted_session() == 2
        assert spool.pending_count() == 4
        recovered = {
            row.payload["account_id"]: row
            for row in spool.peek_boundaries(10)
            if row.payload["kind"] == "disconnected"
        }
        assert set(recovered) == {7, 9}
        assert all(
            row.payload["end_reliable"] is False
            for row in recovered.values()
        )
        assert all(
            row.payload["connected_at"] == connected_at
            and row.payload["disconnected_at"] == heartbeat_at
            for row in recovered.values()
        )
        assert spool.recover_interrupted_session() == 0

@pytest.mark.django_db
def test_cpa_spool_migrates_legacy_pending_boundaries(tmp_path):
    account = create_cpa_account()
    config = AppSettings.load()
    opened_at = account.created_at + timedelta(seconds=1)
    closed_at = opened_at + timedelta(minutes=5)
    reset_at = opened_at + timedelta(days=3)

    def window_payload(observed_at, percent):
        return {
            "used_percent": percent,
            "window_seconds": 604800,
            "reset_after_seconds": int(
                (reset_at - observed_at).total_seconds()
            ),
            "reset_at": int(reset_at.timestamp()),
            "slot": "secondary_window",
            "sampled_at": observed_at.isoformat(),
            "plan_type": "pro",
        }

    spool_path = tmp_path / "legacy-boundaries.sqlite3"
    with CPAUsageSpool(spool_path) as spool:
        spool.connection.executemany(
            """
            INSERT INTO boundary_events (event_key, payload)
            VALUES (?, ?)
            """,
            [
                (
                    f"legacy-session:opened:{account.id}",
                    json.dumps(
                        {
                            "account_id": account.id,
                            "boundary": "opened",
                            "observed_at": opened_at.isoformat(),
                            "window": window_payload(opened_at, "49"),
                            "reliable": True,
                            "required_usage_id": 0,
                        }
                    ),
                ),
                (
                    f"legacy-session:closed:{account.id}",
                    json.dumps(
                        {
                            "account_id": account.id,
                            "boundary": "closed",
                            "observed_at": closed_at.isoformat(),
                            "window": window_payload(closed_at, "50"),
                            "reliable": True,
                            "required_usage_id": 0,
                        }
                    ),
                ),
            ],
        )
        spool.connection.commit()

    with CPAUsageSpool(spool_path) as spool:
        records = spool.peek_boundaries(10)
        assert [record.payload["kind"] for record in records] == [
            "opening_sample",
            "disconnected",
        ]
        assert records[0].payload["connected_at"] == opened_at.isoformat()
        assert records[1].payload["connected_at"] == opened_at.isoformat()
        assert runcpacollector.Command()._persist_pending_boundaries(
            spool,
            config,
        ) == 2
        assert spool.boundary_count() == 0

    interval = CPAAccountCollectionInterval.objects.get(account=account)
    assert interval.session_key == "legacy-session"
    assert interval.connected_at == opened_at
    assert interval.disconnected_at == closed_at
    assert interval.end_reliable is True


@pytest.mark.django_db
def test_cpa_spool_recovers_old_active_session_against_migrated_interval(
    tmp_path,
):
    account = create_cpa_account()
    config = AppSettings.load()
    opened_at = account.created_at + timedelta(seconds=1)
    heartbeat_at = opened_at + timedelta(minutes=5)
    interval = CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="old-active-session",
        connected_at=opened_at,
    )
    spool_path = tmp_path / "old-active-session.sqlite3"
    connection = sqlite3.connect(spool_path)
    connection.execute(
        """
        CREATE TABLE collector_session (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            session_key TEXT NOT NULL,
            active INTEGER NOT NULL,
            heartbeat_at TEXT NOT NULL,
            account_ids TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO collector_session (
            id,
            session_key,
            active,
            heartbeat_at,
            account_ids
        )
        VALUES (1, ?, 1, ?, ?)
        """,
        (
            "old-active-session",
            heartbeat_at.isoformat(),
            json.dumps([account.id]),
        ),
    )
    connection.commit()
    connection.close()

    with CPAUsageSpool(spool_path) as spool:
        assert spool.recover_interrupted_session() == 1
        recovered = spool.peek_boundaries(10)
        assert recovered[0].payload["connected_at"] is None
        assert runcpacollector.Command()._persist_pending_boundaries(
            spool,
            config,
        ) == 1
        assert spool.boundary_count() == 0

    interval.refresh_from_db()
    assert interval.disconnected_at == heartbeat_at
    assert interval.end_reliable is False


def test_cpa_spool_pending_count_includes_usage_and_collection_events(tmp_path):
    with CPAUsageSpool(tmp_path / "cpa-usage-spool.sqlite3") as spool:
        spool.append([{"request_id": "pending-usage"}])
        spool.append_boundaries(
            [
                {
                    "event_key": "pending-collection-event",
                    "kind": "disconnected",
                    "account_id": 1,
                    "session_key": "test-session",
                    "connected_at": "2026-08-29T09:00:00+00:00",
                    "disconnected_at": "2026-08-29T10:00:00+00:00",
                    "window": None,
                    "end_reliable": False,
                    "required_usage_id": 0,
                }
            ]
        )

        assert spool.count() == 1
        assert spool.boundary_count() == 1
        assert spool.pending_count() == 2


def test_cpa_subscription_reader_spools_all_prefetched_frames(tmp_path):
    class FakeSocket:
        def __init__(self):
            self.chunks = [
                _resp_array("message", "usage", json.dumps({"request_id": "one"}))
                + _resp_array(
                    "message",
                    "usage",
                    json.dumps({"request_id": "two"}),
                )
            ]

        def recv(self, _count):
            return self.chunks.pop(0) if self.chunks else b""

    class FakeSubscriber:
        def __init__(self):
            self.connection = _RESPConnection(FakeSocket())

        def read_record(self, timeout):
            assert timeout == 0.5
            return decode_usage_message(self.connection.read_response())

        def unsubscribe(self, _store):
            raise AssertionError("reader stopped unexpectedly")

    spool_path = tmp_path / "cpa-usage-spool.sqlite3"
    finished = Event()
    state = runcpacollector._ReaderState()
    runcpacollector._read_subscription(
        FakeSubscriber(),
        spool_path,
        Event(),
        finished,
        state,
        [],
        Queue(),
    )

    assert finished.is_set()
    assert isinstance(state.error, ConnectionError)
    with CPAUsageSpool(spool_path) as spool:
        assert [item.payload["request_id"] for item in spool.peek(10)] == [
            "one",
            "two",
        ]

def test_cpa_subscription_reader_surfaces_ping_failure(monkeypatch, tmp_path):
    class FakeSubscriber:
        def read_record(self, timeout):
            assert timeout == 0.5
            return None

        def ping(self, _store):
            raise CPAError("ping failed")

        def unsubscribe(self, _store):
            raise AssertionError("reader stopped unexpectedly")

    monkeypatch.setattr(runcpacollector, "PING_SECONDS", 0)
    finished = Event()
    state = runcpacollector._ReaderState()

    runcpacollector._read_subscription(
        FakeSubscriber(),
        tmp_path / "cpa-usage-spool.sqlite3",
        Event(),
        finished,
        state,
        [],
        Queue(),
    )

    assert finished.is_set()
    assert isinstance(state.error, CPAError)
    assert str(state.error) == "ping failed"


@pytest.mark.django_db
def test_cpa_spool_retains_batch_until_business_write_recovers(
    monkeypatch,
    tmp_path,
):
    account = create_cpa_account()
    spool_path = tmp_path / "cpa-usage-spool.sqlite3"
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "retry-after-database-failure",
        "timestamp": (account.created_at + timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "tokens": {"input_tokens": 1, "total_tokens": 1},
    }
    with CPAUsageSpool(spool_path) as spool:
        spool.append([payload])

        original_persist = runcpacollector.persist_usage_events

        def fail_persist(_records):
            raise RuntimeError("business database unavailable")

        monkeypatch.setattr(
            runcpacollector,
            "persist_usage_events",
            fail_persist,
        )
        with pytest.raises(RuntimeError, match="business database unavailable"):
            runcpacollector._persist_spool_batch(spool)
        assert spool.count() == 1
        assert not CPAUsageEvent.objects.exists()

        monkeypatch.setattr(
            runcpacollector,
            "persist_usage_events",
            original_persist,
        )
        assert runcpacollector._persist_spool_batch(spool) == 1
        assert spool.count() == 0

    assert CPAUsageEvent.objects.get().request_id == payload["request_id"]


def test_cpa_unsubscribe_drains_frames_before_acknowledgement():
    usage = {"request_id": "before-unsubscribe"}
    frames = bytearray(
        _resp_array("message", "usage", json.dumps(usage))
        + _resp_array("unsubscribe", "usage", 0)
    )

    class FakeSocket:
        def __init__(self):
            self.sent = bytearray()

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, count):
            chunk = bytes(frames[:count])
            del frames[:count]
            return chunk

        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    sock = FakeSocket()
    subscriber = CPAUsageSubscriber(
        AppSettings(cpa_base_url="http://cpa.example:8317"),
        management_key="management-secret",
    )
    subscriber.sock = sock


    subscriber.connection = _RESPConnection(sock)
    received = []

    subscriber.unsubscribe(received.append)

    assert received == [usage]
    assert b"UNSUBSCRIBE" in sock.sent
def test_collector_sigterm_handler_requests_graceful_shutdown():
    with pytest.raises(KeyboardInterrupt):
        runcpacollector._request_shutdown(None, None)


@pytest.mark.django_db
def test_durable_opening_sample_waits_for_usage_and_retries_busy_writer(
    monkeypatch,
    tmp_path,
):
    account = create_cpa_account()
    config = AppSettings.load()
    connected_at = account.created_at + timedelta(seconds=1)
    observed_at = connected_at + timedelta(seconds=1)
    window = WeeklyWindow(
        used_percent=Decimal("40"),
        window_seconds=604800,
        reset_after_seconds=3 * 86400,
        reset_at=int((observed_at + timedelta(days=3)).timestamp()),
        slot="secondary_window",
        sampled_at=observed_at.isoformat(),
        plan_type="pro",
    )
    payload = {
        "auth_index": account.cpa_auth_index,
        "request_id": "before-opening-sample",
        "timestamp": (observed_at - timedelta(seconds=1)).isoformat(),
        "model": "gpt-test",
        "tokens": {"input_tokens": 1, "total_tokens": 1},
    }
    attempts = []

    def persist_opening(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            return {"status": "busy"}
        return {"status": "calibrated"}

    monkeypatch.setattr(
        runcpacollector,
        "persist_cpa_collection_opening_sample",
        persist_opening,
    )
    command = runcpacollector.Command()
    sample = runcpacollector._BoundarySample(
        account_id=account.id,
        account_name=account.name,
        observed_at=observed_at,
        window=window,
        reliable=True,
    )

    with CPAUsageSpool(tmp_path / "durable-opening.sqlite3") as spool:
        spool.append([payload])
        required_usage_id = spool.max_usage_id()
        spool.append_boundaries(
            command._opening_sample_records(
                [sample],
                "session-1",
                connected_at,
                required_usage_id,
            )
        )

        assert command._persist_pending_boundaries(spool, config) == 0
        assert attempts == []
        assert runcpacollector._persist_spool_batch(spool) == 1
        assert command._persist_pending_boundaries(spool, config) == 0
        assert spool.boundary_count() == 1
        assert command._persist_pending_boundaries(spool, config) == 1
        assert spool.boundary_count() == 0

    assert len(attempts) == 2


@pytest.mark.django_db
def test_collector_disconnect_reliability_is_independent_of_opening_sample(
    monkeypatch,
    tmp_path,
):
    account = create_cpa_account()
    config = AppSettings.load()
    command = runcpacollector.Command()
    connected_at = timezone.now()
    monkeypatch.setattr(
        runcpacollector,
        "_request_reader_barrier",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        command,
        "_query_subscription_boundaries",
        lambda *_args, **_kwargs: [
            runcpacollector._BoundarySample(
                account_id=account.id,
                account_name=account.name,
                observed_at=timezone.now(),
                window=None,
                reliable=False,
            )
        ],
    )

    class FakeSubscriber:
        def close(self):
            raise AssertionError("live barrier should not close the subscriber")

    spool_path = tmp_path / "unsampled-close.sqlite3"
    with CPAUsageSpool(spool_path) as spool:
        spool.begin_session(
            "unsampled-session",
            [account.id],
            connected_at.isoformat(),
        )
        command._finish_subscription_session(
            config=config,
            spool=spool,
            subscriber=FakeSubscriber(),
            session_key="unsampled-session",
            connected_at=connected_at,
            accounts=[account],
            reader_state=runcpacollector._ReaderState(),
            reader_finished=Event(),
            barriers=Queue(),
        )
        disconnected = next(
            row
            for row in spool.peek_boundaries(10)
            if row.payload["kind"] == "disconnected"
        )

    assert disconnected.payload["account_id"] == account.id
    assert disconnected.payload["end_reliable"] is True
    assert disconnected.payload["window"] is None


@pytest.mark.django_db
def test_collector_config_restart_closes_without_quota_probe(
    monkeypatch,
    tmp_path,
):
    account = create_cpa_account()
    config = AppSettings.load()
    command = runcpacollector.Command()
    connected_at = timezone.now()
    monkeypatch.setattr(
        runcpacollector,
        "_request_reader_barrier",
        lambda *_args, **_kwargs: 0,
    )
    def reject_closing_query(*_args, **_kwargs):
        raise AssertionError("config restart must not query closing quota")

    monkeypatch.setattr(
        command,
        "_query_subscription_boundaries",
        reject_closing_query,
    )

    class FakeSubscriber:
        def close(self):
            raise AssertionError("live barrier should not close the subscriber")

    with CPAUsageSpool(tmp_path / "config-restart-close.sqlite3") as spool:
        spool.begin_session(
            "config-restart-session",
            [account.id],
            connected_at.isoformat(),
        )
        command._finish_subscription_session(
            config=config,
            spool=spool,
            subscriber=FakeSubscriber(),
            session_key="config-restart-session",
            connected_at=connected_at,
            accounts=[account],
            reader_state=runcpacollector._ReaderState(),
            reader_finished=Event(),
            barriers=Queue(),
            capture_closing_sample=False,
        )
        disconnected = next(
            row
            for row in spool.peek_boundaries(10)
            if row.payload["kind"] == "disconnected"
        )

    assert disconnected.payload["account_id"] == account.id
    assert disconnected.payload["end_reliable"] is True
    assert disconnected.payload["window"] is None


def test_cpa_ping_drains_usage_before_pong():
    usage = {"request_id": "before-pong"}
    frames = bytearray(
        _resp_array("message", "usage", json.dumps(usage))
        + _resp_array("pong", "sub2pool")
    )

    class FakeSocket:
        def __init__(self):
            self.sent = bytearray()

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, count):
            chunk = bytes(frames[:count])
            del frames[:count]
            return chunk

        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    sock = FakeSocket()
    subscriber = CPAUsageSubscriber(
        AppSettings(cpa_base_url="http://cpa.example:8317"),
        management_key="management-secret",
    )
    subscriber.sock = sock
    subscriber.connection = _RESPConnection(sock)
    received = []

    subscriber.ping(received.append)

    assert received == [usage]
    assert b"PING" in sock.sent
    assert b"sub2pool" in sock.sent


def test_collector_status_write_failure_does_not_escape():
    stderr = StringIO()
    command = runcpacollector.Command(stderr=stderr)

    def fail():
        raise RuntimeError("status database unavailable")

    assert command._safe_status_update("test", fail) is False

    assert "status database unavailable" in stderr.getvalue()
    assert command._safe_status_update("test", lambda: None) is True


def test_collector_pending_count_includes_unspooled_memory(monkeypatch):
    class FakeSpool:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def pending_count(self):
            return 3

    monkeypatch.setattr(runcpacollector, "CPAUsageSpool", FakeSpool)

    assert (
        runcpacollector._current_pending_count(
            0,
            unspooled_count=4,
        )
        == 7
    )


def test_collector_throttles_unspooled_retries(monkeypatch, tmp_path):
    class FakeSpool:
        def __init__(self):
            self.path = tmp_path / "unused.sqlite3"
            self.append_calls = 0

        def append(self, _records):
            self.append_calls += 1
            raise RuntimeError("spool unavailable")

        def pending_count(self):
            return 0

        def begin_session(self, *_args):
            pass

    class FakeSubscriber:
        def connect(self):
            pass

        def close(self):
            pass

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    clock = iter([0.0, 0.0, 0.1, 0.2, 0.3])
    config = AppSettings(
        cpa_base_url="http://cpa.example:8317",
        cpa_management_key_encrypted=encrypt_secret("management-secret"),
    )
    command = runcpacollector.Command(stdout=StringIO())
    monkeypatch.setattr(runcpacollector, "_cpa_accounts", lambda: [])
    monkeypatch.setattr(
        command,
        "_capture_opening_boundaries",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        command,
        "_finish_subscription_session",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(command, "_safe_mark_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runcpacollector,
        "CPAUsageSubscriber",
        lambda _config: FakeSubscriber(),
    )
    monkeypatch.setattr(runcpacollector, "Thread", FakeThread)
    monkeypatch.setattr(runcpacollector.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runcpacollector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runcpacollector, "CONFIG_REFRESH_SECONDS", 0.3)
    monkeypatch.setattr(runcpacollector, "close_old_connections", lambda: None)
    monkeypatch.setattr(runcpacollector, "_has_cpa_accounts", lambda: False)
    monkeypatch.setattr(runcpacollector.AppSettings, "load", lambda: config)
    monkeypatch.setattr(runcpacollector, "mark_collector_connected", lambda: None)
    monkeypatch.setattr(runcpacollector, "_current_pending_count", lambda *_args, **_kwargs: 0)
    spool = FakeSpool()

    command._run_subscription(config, spool, [{"request_id": "pending"}])

    assert spool.append_calls == 2


@pytest.mark.django_db
@pytest.mark.parametrize("interrupt", [False, True])
def test_collector_brackets_collection_events_inside_live_subscription(
    monkeypatch,
    tmp_path,
    interrupt,
):
    account = create_cpa_account()
    reset_at = timezone.now() + timedelta(days=3)
    collection_calls = []
    lifecycle = []

    class FakeSubscriber:
        def __init__(self):
            self.connected = False
            self.unsubscribed = False

        def connect(self):
            self.connected = True
            lifecycle.append("connected")

        def read_record(self, timeout):
            Event().wait(0.01)
            return None

        def ping(self, _store):
            assert self.connected is True
            assert self.unsubscribed is False
            lifecycle.append("barrier")

        def unsubscribe(self, _store):
            self.unsubscribed = True
            lifecycle.append("unsubscribed")

        def close(self):
            pass

    class FakeCPAClient:
        def __init__(self, _config, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, auth_index):
            assert subscriber.connected is True
            assert subscriber.unsubscribed is False
            lifecycle.append("quota")
            return WeeklyWindow(
                used_percent=Decimal("49"),
                window_seconds=604800,
                reset_after_seconds=3 * 86400,
                reset_at=int(reset_at.timestamp()),
                slot="secondary_window",
                sampled_at=timezone.now().isoformat(),
                plan_type="pro",
            )

    def persist_connected(_config, observed_account, **kwargs):
        collection_calls.append(
            (
                observed_account.cpa_auth_index,
                "connected",
                kwargs["connected_at"],
            )
        )
        return {"status": "created"}

    def persist_opening(_config, observed_account, **kwargs):
        collection_calls.append(
            (
                observed_account.cpa_auth_index,
                "opening_sample",
                kwargs["observed_at"],
            )
        )
        return {"status": "calibrated"}

    def persist_disconnected(_config, observed_account, **kwargs):
        collection_calls.append(
            (
                observed_account.cpa_auth_index,
                "disconnected",
                kwargs["disconnected_at"],
            )
        )
        return {"status": "created"}

    config = AppSettings(
        cpa_base_url="http://cpa.example:8317",
        cpa_management_key_encrypted=encrypt_secret("management-secret"),
    )
    subscriber = FakeSubscriber()
    monkeypatch.setattr(
        runcpacollector,
        "CPAUsageSubscriber",
        lambda _config: subscriber,
    )
    monkeypatch.setattr(runcpacollector, "CPAClient", FakeCPAClient)
    monkeypatch.setattr(
        runcpacollector,
        "persist_cpa_collection_connected",
        persist_connected,
    )
    monkeypatch.setattr(
        runcpacollector,
        "persist_cpa_collection_opening_sample",
        persist_opening,
    )
    monkeypatch.setattr(
        runcpacollector,
        "persist_cpa_collection_disconnected",
        persist_disconnected,
    )
    monkeypatch.setattr(runcpacollector, "CONFIG_REFRESH_SECONDS", 0)
    monkeypatch.setattr(runcpacollector, "close_old_connections", lambda: None)
    monkeypatch.setattr(runcpacollector, "_has_cpa_accounts", lambda: False)

    def load_config():
        if interrupt:
            raise KeyboardInterrupt
        return config

    monkeypatch.setattr(runcpacollector.AppSettings, "load", load_config)
    monkeypatch.setattr(runcpacollector, "mark_collector_connected", lambda: None)

    with CPAUsageSpool(tmp_path / "subscriber-only.sqlite3") as spool:
        command = runcpacollector.Command(stdout=StringIO())
        if interrupt:
            with pytest.raises(KeyboardInterrupt):
                command._run_subscription(config, spool, [])
        else:
            command._run_subscription(config, spool, [])

    assert subscriber.connected is True
    assert subscriber.unsubscribed is True
    assert lifecycle[0] == "connected"
    assert lifecycle.index("quota") > lifecycle.index("connected")
    assert lifecycle.index("unsubscribed") > max(
        index for index, value in enumerate(lifecycle) if value == "quota"
    )
    assert [item[1] for item in collection_calls] == [
        "connected",
        "opening_sample",
        "disconnected",
    ]
    assert collection_calls[0][2] <= collection_calls[1][2]
    assert collection_calls[1][2] <= collection_calls[2][2]


@pytest.mark.django_db
def test_opening_sample_persists_usage_drained_by_its_resp_barrier(
    monkeypatch,
    tmp_path,
):
    account = create_cpa_account()
    config = AppSettings.load()
    config.cpa_management_key_encrypted = encrypt_secret("management-secret")
    config.cpa_model_pricing = PRICING
    config.save()
    reset_at = timezone.now() + timedelta(days=3)
    event_at = account.created_at + timedelta(microseconds=1)

    class FakeSubscriber:
        def __init__(self):
            self.sent_usage = False

        def connect(self):
            pass

        def read_record(self, timeout):
            Event().wait(0.01)
            return None

        def ping(self, store):
            if not self.sent_usage:
                store(
                    {
                        "auth_index": account.cpa_auth_index,
                        "request_id": "before-opening-barrier",
                        "timestamp": event_at.isoformat(),
                        "model": "gpt-test",
                        "tokens": {
                            "input_tokens": 1_000_000,
                            "total_tokens": 1_000_000,
                        },
                    }
                )
                self.sent_usage = True

        def unsubscribe(self, _store):
            pass

        def close(self):
            pass

    class FakeCPAClient:
        def __init__(self, _config, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _auth_index):
            return WeeklyWindow(
                used_percent=Decimal("50"),
                window_seconds=604800,
                reset_after_seconds=3 * 86400,
                reset_at=int(reset_at.timestamp()),
                slot="secondary_window",
                sampled_at=timezone.now().isoformat(),
                plan_type="pro",
            )

    monkeypatch.setattr(
        runcpacollector,
        "CPAUsageSubscriber",
        lambda _config: FakeSubscriber(),
    )
    monkeypatch.setattr(runcpacollector, "CPAClient", FakeCPAClient)
    monkeypatch.setattr(runcpacollector, "CONFIG_REFRESH_SECONDS", 0)
    monkeypatch.setattr(runcpacollector, "_has_cpa_accounts", lambda: False)
    monkeypatch.setattr(runcpacollector.AppSettings, "load", lambda: config)
    monkeypatch.setattr(runcpacollector, "mark_collector_connected", lambda: None)

    with CPAUsageSpool(tmp_path / "opening-barrier.sqlite3") as spool:
        runcpacollector.Command(stdout=StringIO())._run_subscription(
            config,
            spool,
            [],
        )

    opening = Observation.objects.get(source="cpa_subscription_opened")
    assert CPAUsageEvent.objects.get().request_id == "before-opening-barrier"
    assert opening.raw_selected_total_cost == Decimal("1")
    assert opening.selected_total_cost == Decimal("0")
    assert opening.raw_window["collection_sample_kind"] == "opening"


@pytest.mark.django_db
def test_collector_does_not_query_boundaries_before_resp_connects(monkeypatch, tmp_path):
    create_cpa_account()
    queried = []

    class FailingSubscriber:
        def connect(self):
            raise CPAError("RESP unavailable")

        def close(self):
            pass

    class ForbiddenCPAClient:
        def __init__(self, *_args, **_kwargs):
            queried.append(True)

    config = AppSettings(
        cpa_base_url="http://cpa.example:8317",
        cpa_management_key_encrypted=encrypt_secret("management-secret"),
    )
    monkeypatch.setattr(
        runcpacollector,
        "CPAUsageSubscriber",
        lambda _config: FailingSubscriber(),
    )
    monkeypatch.setattr(runcpacollector, "CPAClient", ForbiddenCPAClient)

    with CPAUsageSpool(tmp_path / "connect-first.sqlite3") as spool:
        with pytest.raises(CPAError, match="RESP unavailable"):
            runcpacollector.Command()._run_subscription(config, spool, [])

    assert queried == []


def test_cpa_usage_subscriber_authenticates_and_decodes_pubsub(monkeypatch):
    usage = {
        "auth_index": "auth-1",
        "timestamp": "2026-08-28T10:00:00Z",
        "model": "gpt-test",
        "tokens": {"input_tokens": 1},
    }
    responses = bytearray(
        b"+OK\r\n"
        + _resp_array("subscribe", "usage", 1)
        + _resp_array(
            "message",
            "usage",
            json.dumps({"support_refresh": True}),
        )
        + _resp_array(
            "message",
            "usage",
            json.dumps(usage),
        )
    )

    class FakeSocket:
        def __init__(self):
            self.sent = bytearray()
            self.closed = False

        def sendall(self, payload):
            self.sent.extend(payload)

        def recv(self, count):
            if not responses:
                return b""
            chunk = bytes(responses[:count])
            del responses[:count]
            return chunk

        def settimeout(self, _timeout):
            pass

        def close(self):
            self.closed = True

    sock = FakeSocket()
    monkeypatch.setattr(
        "monitor.integrations.cpa.usage_stream.socket.create_connection",
        lambda *_args, **_kwargs: sock,
    )
    config = AppSettings(
        cpa_base_url="http://cpa.example:8317",
        cpa_management_key_encrypted=encrypt_secret("management-secret"),
    )

    with CPAUsageSubscriber(config) as subscriber:
        assert subscriber.read_record() is None
        assert subscriber.read_record() == usage

    assert b"AUTH" in sock.sent
    assert b"management-secret" in sock.sent
    assert b"SUBSCRIBE" in sock.sent
    assert b"usage" in sock.sent
    assert sock.closed is True


def test_cpa_connection_requires_usage_statistics():
    config = AppSettings(cpa_base_url="https://cpa.example", verify_tls=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(200, json={"files": []})
        if request.url.path.endswith("/usage-statistics-enabled"):
            return httpx.Response(
                200,
                json={"usage-statistics-enabled": False},
            )
        raise AssertionError(request.url)

    client = CPAClient(config, management_key="management-secret")
    headers = client.client.headers
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers=headers,
    )
    try:
        with pytest.raises(CPAError, match="usage statistics 未启用"):
            client.test_connection()
    finally:
        client.client.close()


def test_cpa_management_client_lists_codex_accounts_and_reads_weekly_window():
    config = AppSettings(cpa_base_url="https://cpa.example", verify_tls=True)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth-files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "provider": "codex",
                            "auth_index": "auth-1",
                            "email": "owner@example.com",
                            "id_token": {
                                "chatgpt_account_id": "chatgpt-1",
                                "plan_type": "pro",
                            },
                        },
                        {"provider": "gemini", "auth_index": "ignored"},
                    ]
                },
            )
        if request.url.path.endswith("/api-call"):
            assert request.headers["authorization"] == "Bearer management-secret"
            assert request.content
            return httpx.Response(
                200,
                json={
                    "status_code": 200,
                    "body": {
                        "rate_limit": {
                            "primary_window": {
                                "used_percent": 35,
                                "limit_window_seconds": 18_000,
                                "reset_after_seconds": 60,
                                "reset_at": 1_800_000_000,
                            },
                            "secondary_window": {
                                "used_percent": 42,
                                "limit_window_seconds": 604_800,
                                "reset_after_seconds": 86_400,
                                "reset_at": 1_800_086_400,
                            },
                        }
                    },
                },
            )
        raise AssertionError(request.url)

    client = CPAClient(config, management_key="management-secret")
    headers = client.client.headers
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers=headers,
    )
    try:
        accounts = client.list_codex_accounts()
        window = client.query_weekly_window("auth-1")
    finally:
        client.client.close()

    assert management_base_url("https://cpa.example/") == (
        "https://cpa.example/v0/management/"
    )
    assert [item["auth_index"] for item in accounts] == ["auth-1"]
    assert window.used_percent == Decimal("42")
    assert window.window_seconds == 604_800
    assert window.plan_type == "pro"
    assert [request.url.path for request in requests] == [
        "/v0/management/auth-files",
        "/v0/management/auth-files",
        "/v0/management/api-call",
    ]
