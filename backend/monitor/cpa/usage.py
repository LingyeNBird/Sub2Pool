"""Persist immutable CPA usage facts and calculate costs from current pricing."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any, Iterable

from django.conf import settings as django_settings
from django.db import transaction

from ..history_state import fenced_fact_write
from ..models import AppSettings, CPAUsageEvent, MonitoredAccount, Observation
from .pricing import calculate_cpa_cost

ZERO = Decimal("0")
_SPOOL_FINGERPRINT_KEY = "__sub2pool_event_fingerprint"
_SPOOL_API_KEY_HASH_KEY = "__sub2pool_api_key_hash"
_SPOOL_API_KEY_HINT_KEY = "__sub2pool_api_key_hint"
_SPOOL_INTERNAL_KEYS = frozenset(
    {
        _SPOOL_FINGERPRINT_KEY,
        _SPOOL_API_KEY_HASH_KEY,
        _SPOOL_API_KEY_HINT_KEY,
    }
)


def _external_usage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key not in _SPOOL_INTERNAL_KEYS
    }


def _valid_sha256(value: Any) -> str:
    candidate = str(value or "")
    if len(candidate) != 64:
        return ""
    try:
        int(candidate, 16)
    except ValueError:
        return ""
    return candidate.lower()


def prepare_usage_payload_for_spool(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Remove the raw API key before a usage event reaches durable storage."""

    source = _external_usage_payload(payload)
    api_key_hash, api_key_hint = _api_key_identity(
        str(source.get("api_key") or "")
    )
    safe = dict(source)
    safe.pop("api_key", None)
    safe[_SPOOL_FINGERPRINT_KEY] = _fingerprint(source)
    safe[_SPOOL_API_KEY_HASH_KEY] = api_key_hash
    safe[_SPOOL_API_KEY_HINT_KEY] = api_key_hint
    return safe



def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _event_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed.astimezone(dt_timezone.utc)
    return None


def _api_key_identity(raw_key: str) -> tuple[str, str]:
    value = raw_key.strip()
    if not value:
        return "", ""
    digest = hmac.new(
        str(django_settings.SECRET_KEY).encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest, value[-4:]


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_usage_event(
    payload: dict[str, Any],
    accounts_by_auth_index: dict[str, MonitoredAccount],
) -> tuple[str, CPAUsageEvent | None]:
    auth_index = str(payload.get("auth_index") or "").strip()
    if not auth_index:
        return "ignored_missing_auth", None
    account = accounts_by_auth_index.get(auth_index)
    if account is None:
        return "ignored_unmonitored", None

    occurred_at = _event_time(payload.get("timestamp"))
    if occurred_at is None:
        return "ignored_invalid_timestamp", None
    if account.created_at and occurred_at < account.created_at:
        return "ignored_before_connection", None

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    cached_tokens = max(
        _nonnegative_int(tokens.get("cached_tokens")),
        _nonnegative_int(tokens.get("cache_read_tokens")),
    )
    spooled_api_key_hash = _valid_sha256(payload.get(_SPOOL_API_KEY_HASH_KEY))
    if spooled_api_key_hash:
        api_key_hash = spooled_api_key_hash
        api_key_hint = str(payload.get(_SPOOL_API_KEY_HINT_KEY) or "")[-4:]
    else:
        api_key_hash, api_key_hint = _api_key_identity(
            str(payload.get("api_key") or "")
        )
    event_fingerprint = _valid_sha256(payload.get(_SPOOL_FINGERPRINT_KEY))
    if not event_fingerprint:
        event_fingerprint = _fingerprint(_external_usage_payload(payload))
    return "pending", CPAUsageEvent(
        account=account,
        event_fingerprint=event_fingerprint,
        request_id=str(payload.get("request_id") or "")[:255],
        occurred_at=occurred_at,
        model=str(payload.get("model") or "unknown")[:255],
        alias=str(payload.get("alias") or "")[:255],
        endpoint=str(payload.get("endpoint") or "")[:255],
        provider=str(payload.get("provider") or "")[:64],
        api_key_hash=api_key_hash,
        api_key_hint=api_key_hint,
        input_tokens=_nonnegative_int(tokens.get("input_tokens")),
        cached_input_tokens=cached_tokens,
        output_tokens=_nonnegative_int(tokens.get("output_tokens")),
        reasoning_tokens=_nonnegative_int(tokens.get("reasoning_tokens")),
        total_tokens=_nonnegative_int(tokens.get("total_tokens")),
        failed=bool(payload.get("failed")),
        latency_ms=_nonnegative_int(payload.get("latency_ms")),
        ttft_ms=_nonnegative_int(payload.get("ttft_ms")),
        requested_service_tier=str(payload.get("service_tier") or "")[:32],
        response_service_tier=str(
            payload.get("response_service_tier") or ""
        )[:32],
    )


def persist_usage_events(payloads: Iterable[dict[str, Any]]) -> list[str]:
    """Persist one queue batch atomically after filtering irrelevant records."""

    records = [payload for payload in payloads if isinstance(payload, dict)]
    if not records:
        return []
    auth_indexes = {
        str(payload.get("auth_index") or "").strip()
        for payload in records
        if str(payload.get("auth_index") or "").strip()
    }
    accounts_by_auth_index = {
        account.cpa_auth_index or "": account
        for account in MonitoredAccount.objects.filter(
            provider="cpa",
            cpa_auth_index__in=auth_indexes,
        )
    }

    statuses: list[str] = []
    first_candidates: dict[str, tuple[int, CPAUsageEvent]] = {}
    for payload in records:
        status, event = _prepare_usage_event(payload, accounts_by_auth_index)
        statuses.append(status)
        if event is None:
            continue
        fingerprint = event.event_fingerprint
        if fingerprint in first_candidates:
            statuses[-1] = "duplicate"
            continue
        first_candidates[fingerprint] = (len(statuses) - 1, event)

    if not first_candidates:
        return statuses
    fingerprints = set(first_candidates)
    existing = set(
        CPAUsageEvent.objects.filter(
            event_fingerprint__in=fingerprints
        ).values_list("event_fingerprint", flat=True)
    )
    pending = {
        fingerprint: candidate
        for fingerprint, candidate in first_candidates.items()
        if fingerprint not in existing
    }
    for fingerprint in existing:
        candidate = first_candidates.get(fingerprint)
        if candidate is not None:
            statuses[candidate[0]] = "duplicate"
    if not pending:
        return statuses

    fact_keys = {event.account.fact_key for _index, event in pending.values()}
    with fenced_fact_write(fact_keys) as guards:
        existing = set(
            CPAUsageEvent.objects.filter(
                event_fingerprint__in=pending
            ).values_list("event_fingerprint", flat=True)
        )
        to_create = [
            event
            for fingerprint, (_index, event) in pending.items()
            if fingerprint not in existing
        ]
        CPAUsageEvent.objects.bulk_create(to_create, batch_size=500)

        earliest_by_account: dict[int, tuple[MonitoredAccount, datetime]] = {}
        for event in to_create:
            fact_key = event.account.fact_key
            current = earliest_by_account.get(fact_key)
            if current is None or event.occurred_at < current[1]:
                earliest_by_account[fact_key] = (event.account, event.occurred_at)
        if earliest_by_account:
            from ..replay import rebuild_account

            config = AppSettings.load()
            for fact_key, (account, earliest_at) in earliest_by_account.items():
                if not Observation.objects.filter(
                    account_id=fact_key,
                    observed_at__gte=earliest_at,
                ).exists():
                    continue
                refreshed = _refresh_cpa_account_history(
                    config,
                    account,
                    observed_from=earliest_at,
                )
                if refreshed:
                    rebuild_account(
                        fact_key,
                        config,
                        guard=guards[fact_key],
                    )

        for fingerprint, (index, _event) in pending.items():
            statuses[index] = "duplicate" if fingerprint in existing else "created"
    return statuses


def persist_usage_event(payload: dict[str, Any]) -> str:
    """Persist a single event through the same atomic batch path."""

    return persist_usage_events([payload])[0]


def cpa_event_cost(
    event: CPAUsageEvent,
    config: AppSettings,
) -> tuple[Decimal, bool]:
    """Return current estimated cost and whether the model price is unknown."""

    tiers = (event.requested_service_tier, event.response_service_tier)
    tier = next(
        (
            value
            for value in tiers
            if value.strip().lower() in {"fast", "priority"}
        ),
        event.response_service_tier or event.requested_service_tier,
    )
    cost = calculate_cpa_cost(
        pricing=config.cpa_model_pricing,
        model=event.model,
        input_tokens=event.input_tokens,
        cached_input_tokens=event.cached_input_tokens,
        output_tokens=event.output_tokens,
        service_tier=tier,
        fast_multiplier=config.cpa_fast_multiplier,
        double_billing_enabled=config.cpa_double_billing_enabled,
        double_billing_threshold_tokens=config.cpa_double_billing_threshold_tokens,
        double_billing_multiplier=config.cpa_double_billing_multiplier,
    )
    if cost is None:
        return ZERO, True
    return cost.estimated_cost_usd, False


def cpa_events_cost(
    events: Iterable[CPAUsageEvent],
    config: AppSettings,
) -> tuple[Decimal, int]:
    total = ZERO
    unknown_count = 0
    for event in events:
        cost, unknown = cpa_event_cost(event, config)
        total += cost
        unknown_count += int(unknown)
    return total, unknown_count


def cpa_window_cost(
    account: MonitoredAccount,
    started_at: datetime,
    ended_at: datetime,
    config: AppSettings,
) -> Decimal:
    events = CPAUsageEvent.objects.filter(
        account=account,
        occurred_at__gte=started_at,
        occurred_at__lte=ended_at,
    ).iterator(chunk_size=1000)
    total, _unknown_count = cpa_events_cost(events, config)
    return total


def _refresh_cpa_account_history(
    config: AppSettings,
    account: MonitoredAccount,
    *,
    observed_from: datetime | None = None,
) -> int:
    refreshed = 0
    observations = Observation.objects.filter(
        account_id=account.fact_key,
    ).select_related("sample_point")
    if observed_from is not None:
        observations = observations.filter(observed_at__gte=observed_from)
    for observation in observations.order_by("observed_at", "id"):
        official_start = observation.upstream_resets_at - timedelta(
            seconds=observation.window_seconds
        )
        started_at = max(official_start, account.created_at)
        total = cpa_window_cost(
            account,
            started_at,
            observation.observed_at,
            config,
        )
        observation.raw_selected_total_cost = total
        observation.total_standard_cost = total
        observation.total_actual_cost = total
        observation.cost_window_started_at = started_at
        observation.cost_window_ended_at = observation.observed_at
        observation.interval_standard_cost = None
        observation.interval_actual_cost = None
        observation.interval_cost_started_at = None
        observation.interval_cost_source = ""
        observation.fast_correction_standard_cost = None
        observation.fast_correction_actual_cost = None
        observation.fast_correction_started_at = None
        observation.fast_correction_request_count = None
        observation.save(
            update_fields=[
                "raw_selected_total_cost",
                "total_standard_cost",
                "total_actual_cost",
                "cost_window_started_at",
                "cost_window_ended_at",
                "interval_standard_cost",
                "interval_actual_cost",
                "interval_cost_started_at",
                "interval_cost_source",
                "fast_correction_standard_cost",
                "fast_correction_actual_cost",
                "fast_correction_started_at",
                "fast_correction_request_count",
            ]
        )
        refreshed += 1
        if observation.sample_point is not None:
            point = observation.sample_point
            point.window_started_at = started_at
            point.window_ended_at = observation.observed_at
            point.account_standard_cost = total
            point.account_actual_cost = total
            point.residual_standard_cost = total
            point.residual_actual_cost = total
            point.interval_standard_cost = None
            point.interval_actual_cost = None
            point.provenance = {
                **(point.provenance or {}),
                "source": "cpa_usage_stream",
                "cost_estimate": True,
            }
            point.save(
                update_fields=[
                    "window_started_at",
                    "window_ended_at",
                    "account_standard_cost",
                    "account_actual_cost",
                    "residual_standard_cost",
                    "residual_actual_cost",
                    "interval_standard_cost",
                    "interval_actual_cost",
                    "provenance",
                ]
            )
    return refreshed


@transaction.atomic
def refresh_cpa_history(config: AppSettings, *, rebuild: bool = True) -> int:
    from ..replay import rebuild_account

    refreshed = 0
    for account in MonitoredAccount.objects.filter(provider="cpa"):
        account_refreshed = _refresh_cpa_account_history(config, account)
        refreshed += account_refreshed
        if account_refreshed and rebuild:
            rebuild_account(account.fact_key, config)
    return refreshed
