"""CPA quota capture backed by locally persisted usage-stream costs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..accounting.boundaries import same_official_reset
from ..fact_utils import expected_user_digest
from ..history_state import LeaseBusyError, LeaseGuard
from ..integrations.cpa import CPAClient
from ..integrations.sub2api import WeeklyWindow
from ..models import (
    AppSettings,
    CPAAccountCollectionInterval,
    HistoryMaintenanceState,
    MonitoredAccount,
    Observation,
    UsageSamplePoint,
)
from ..quota_profiles import normalize_detected_plan_type
from ..replay import rebuild_account
from ..sampling.notifications import finish_success
from ..sampling.types import window_reference
from .usage import cpa_window_cost

ZERO = Decimal("0")


@transaction.atomic
def _persist_cpa_capture(
    *,
    config: AppSettings,
    account: MonitoredAccount,
    guard: LeaseGuard,
    window,
    observed_at,
    total_cost: Decimal,
    cost_started_at,
    source: str,
    latest_raw: Observation | None,
    raw_metadata: dict | None = None,
) -> Observation:
    guard.renew()
    state = HistoryMaintenanceState.objects.select_for_update().get(
        account_id=account.fact_key
    )
    guard.assert_owned(state)
    reference = window_reference(account.fact_key, window)
    same_window = bool(
        latest_raw
        and same_official_reset(
            latest_raw.upstream_resets_at,
            reference.reset_at,
        )
    )
    if same_window:
        interval_started_at = latest_raw.observed_at
        interval_cost = max(ZERO, total_cost - latest_raw.raw_selected_total_cost)
    else:
        interval_started_at = cost_started_at
        interval_cost = total_cost

    point = UsageSamplePoint.objects.create(
        account_id=account.fact_key,
        observed_at=observed_at,
        window_started_at=cost_started_at,
        window_ended_at=observed_at,
        window_resets_at=reference.reset_at,
        capture_started_at=observed_at,
        capture_finished_at=timezone.now(),
        account_standard_cost=total_cost,
        account_actual_cost=total_cost,
        interval_started_at=interval_started_at,
        interval_standard_cost=interval_cost,
        interval_actual_cost=interval_cost,
        residual_standard_cost=total_cost,
        residual_actual_cost=total_cost,
        expected_user_count=0,
        expected_user_digest=expected_user_digest([]),
        write_status="complete",
        reconciliation_status="residual" if total_cost > ZERO else "reconciled",
        provenance={
            "source": "cpa_usage_stream",
            "cost_estimate": True,
            "participants": False,
        },
    )
    observation = Observation.objects.create(
        account_id=account.fact_key,
        sample_point=point,
        source=source,
        observed_at=observed_at,
        window_seconds=reference.window_seconds,
        upstream_resets_at=reference.reset_at,
        upstream_used_percent=window.used_percent,
        raw_selected_total_cost=total_cost,
        selected_total_cost=total_cost,
        total_standard_cost=total_cost,
        total_actual_cost=total_cost,
        cost_window_started_at=cost_started_at,
        cost_window_ended_at=observed_at,
        interval_cost_started_at=interval_started_at,
        interval_standard_cost=interval_cost,
        interval_actual_cost=interval_cost,
        interval_cost_source="cpa_usage_stream",
        effective_usd_per_percent=config.initial_usd_per_percent,
        sample_note="等待派生计算",
        raw_window={
            "provider": "cpa",
            "slot": window.slot,
            "window_seconds": window.window_seconds,
            "reset_after_seconds": window.reset_after_seconds,
            "reset_at": window.reset_at,
            "query_mode": "direct",
            "sampled_at": window.sampled_at,
            "cost_window_started_at": cost_started_at.isoformat(),
            "cost_window_ended_at": observed_at.isoformat(),
            "interval_cost_source": "cpa_usage_stream",
            "cost_estimate": True,
            **(raw_metadata or {}),
        },
    )
    state.fact_revision += 1
    state.save(update_fields=["fact_revision", "updated_at"])
    point.fact_revision = state.fact_revision
    point.save(update_fields=["fact_revision"])
    return observation


def _capture_cpa_window(
    config: AppSettings,
    account: MonitoredAccount,
    source: str,
    guard: LeaseGuard,
    *,
    window,
    observed_at,
    raw_metadata: dict | None = None,
) -> dict:
    latest_raw = (
        Observation.objects.filter(
            account_id=account.fact_key,
            observed_at__lte=observed_at,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    reference = window_reference(account.fact_key, window)
    official_start = reference.reset_at - timedelta(seconds=reference.window_seconds)
    cost_started_at = max(official_start, account.created_at)
    total_cost = cpa_window_cost(account, cost_started_at, observed_at, config)
    capture_source = source
    if (
        source
        not in {
            "cpa_subscription_opened",
            "cpa_subscription_closed",
        }
        and latest_raw
        and not same_official_reset(
            latest_raw.upstream_resets_at,
            reference.reset_at,
        )
    ):
        capture_source = "reset"
    observation = _persist_cpa_capture(
        config=config,
        account=account,
        guard=guard,
        window=window,
        observed_at=observed_at,
        total_cost=total_cost,
        cost_started_at=cost_started_at,
        source=capture_source,
        latest_raw=latest_raw,
        raw_metadata=raw_metadata,
    )
    detected_plan_type = normalize_detected_plan_type(window.plan_type)
    if detected_plan_type and detected_plan_type != account.detected_plan_type:
        account.detected_plan_type = detected_plan_type
        account.save(update_fields=["detected_plan_type", "updated_at"])
    rebuild_account(account.fact_key, config, guard=guard)
    observation.refresh_from_db()
    finish_success(config, account, observed_at)
    if source == "cpa_subscription_opened":
        reason = "CPA 订阅后的百分比观测"
    elif source == "cpa_subscription_closed":
        reason = "CPA 订阅关闭前的百分比观测"
    elif observation.excluded_at is not None:
        reason = observation.exclusion_reason
    elif latest_raw is None:
        reason = "首次 CPA 百分比观测"
    else:
        reason = "CPA 额度与本地估算成本已采样"
    return {
        "status": (
            "calibrated" if observation.excluded_at is None else "reset_pending"
        ),
        "observation_id": observation.pk,
        "reason": reason,
    }


def run_cpa_monitor(
    config: AppSettings,
    account: MonitoredAccount,
    source: str,
    guard: LeaseGuard,
) -> dict:
    with CPAClient(config) as client:
        window = client.query_weekly_window(account.cpa_auth_index or "")
    observed_at = timezone.now()
    return _capture_cpa_window(
        config,
        account,
        source,
        guard,
        window=window,
        observed_at=observed_at,
    )



def _collection_busy(account: MonitoredAccount) -> dict:
    return {
        "status": "busy",
        "account_id": account.id,
        "message": "该账号已有采集或历史维护任务正在执行",
    }


def _locked_collection_interval(
    account: MonitoredAccount,
    *,
    session_key: str,
    connected_at,
    validate_connected_at: bool = True,
) -> tuple[CPAAccountCollectionInterval | None, bool]:
    interval = (
        CPAAccountCollectionInterval.objects.select_for_update()
        .filter(account=account, session_key=session_key)
        .first()
    )
    if interval is not None:
        if (
            validate_connected_at
            and connected_at is not None
            and interval.connected_at != connected_at
        ):
            raise ValueError("同一 CPA 采集 session 的连接时间不一致")
        return interval, False
    if connected_at is None:
        return None, False
    return (
        CPAAccountCollectionInterval.objects.create(
            account=account,
            session_key=session_key,
            connected_at=connected_at,
        ),
        True,
    )


@transaction.atomic
def _persist_cpa_collection_connected(
    config: AppSettings,
    account: MonitoredAccount,
    guard: LeaseGuard,
    *,
    session_key: str,
    connected_at,
) -> dict:
    guard.renew()
    state = HistoryMaintenanceState.objects.select_for_update().get(
        account_id=account.fact_key
    )
    guard.assert_owned(state)
    existing_interval = (
        CPAAccountCollectionInterval.objects.select_for_update()
        .filter(account=account, session_key=session_key)
        .first()
    )
    if existing_interval is not None:
        if existing_interval.connected_at != connected_at:
            raise ValueError("同一 CPA 采集 session 的连接时间不一致")
        return {
            "account_id": account.id,
            "status": "duplicate",
            "collection_interval_id": existing_interval.id,
        }
    stale_interval = (
        CPAAccountCollectionInterval.objects.select_for_update()
        .filter(account=account, disconnected_at__isnull=True)
        .exclude(session_key=session_key)
        .first()
    )
    if stale_interval is not None:
        if connected_at < stale_interval.connected_at:
            raise ValueError("新的 CPA 连接时间早于遗留采集区间")
        stale_interval.disconnected_at = connected_at
        stale_interval.end_reliable = False
        stale_interval.save(
            update_fields=["disconnected_at", "end_reliable", "updated_at"]
        )
    interval, created = _locked_collection_interval(
        account,
        session_key=session_key,
        connected_at=connected_at,
    )
    if interval is None:
        raise RuntimeError("CPA connected 事件未创建采集区间")
    if not created:
        raise RuntimeError("CPA connected 事件未创建新的采集区间")
    state.fact_revision += 1
    state.save(update_fields=["fact_revision", "updated_at"])
    replay = rebuild_account(account.fact_key, config, guard=guard)
    return {
        "account_id": account.id,
        "status": "created",
        "collection_interval_id": interval.id,
        **replay.as_dict(),
    }


def persist_cpa_collection_connected(
    config: AppSettings,
    account: MonitoredAccount,
    *,
    session_key: str,
    connected_at,
) -> dict:
    try:
        guard = LeaseGuard.acquire(account.fact_key)
    except LeaseBusyError:
        return _collection_busy(account)
    try:
        return _persist_cpa_collection_connected(
            config,
            account,
            guard,
            session_key=session_key,
            connected_at=connected_at,
        )
    finally:
        guard.release()


@transaction.atomic
def _persist_cpa_collection_opening_sample(
    config: AppSettings,
    account: MonitoredAccount,
    guard: LeaseGuard,
    *,
    session_key: str,
    connected_at,
    window: WeeklyWindow,
    observed_at,
) -> dict:
    interval, _created = _locked_collection_interval(
        account,
        session_key=session_key,
        connected_at=connected_at,
    )
    if interval is None:
        raise RuntimeError("CPA opening sample 未创建采集区间")
    if interval.opening_observation_id is not None:
        return {
            "account_id": account.id,
            "status": "duplicate",
            "collection_interval_id": interval.id,
            "observation_id": interval.opening_observation_id,
        }
    result = _capture_cpa_window(
        config,
        account,
        "cpa_subscription_opened",
        guard,
        window=window,
        observed_at=observed_at,
        raw_metadata={
            "collection_session_key": session_key,
            "collection_sample_kind": "opening",
        },
    )
    interval.opening_observation_id = result["observation_id"]
    interval.save(update_fields=["opening_observation", "updated_at"])
    return {
        "account_id": account.id,
        "collection_interval_id": interval.id,
        **result,
    }


def persist_cpa_collection_opening_sample(
    config: AppSettings,
    account: MonitoredAccount,
    *,
    session_key: str,
    connected_at,
    window: WeeklyWindow,
    observed_at,
) -> dict:
    try:
        guard = LeaseGuard.acquire(account.fact_key)
    except LeaseBusyError:
        return _collection_busy(account)
    try:
        return _persist_cpa_collection_opening_sample(
            config,
            account,
            guard,
            session_key=session_key,
            connected_at=connected_at,
            window=window,
            observed_at=observed_at,
        )
    finally:
        guard.release()


@transaction.atomic
def _persist_cpa_collection_disconnected(
    config: AppSettings,
    account: MonitoredAccount,
    guard: LeaseGuard,
    *,
    session_key: str,
    connected_at,
    disconnected_at,
    end_reliable: bool,
    window: WeeklyWindow | None,
    sample_observed_at,
) -> dict:
    guard.renew()
    interval, _created = _locked_collection_interval(
        account,
        session_key=session_key,
        connected_at=connected_at,
        validate_connected_at=False,
    )
    if interval is None:
        return {
            "account_id": account.id,
            "status": "orphaned",
            "collection_interval_id": None,
            "observation_id": None,
        }
    if interval.disconnected_at is not None:
        return {
            "account_id": account.id,
            "status": "duplicate",
            "collection_interval_id": interval.id,
            "observation_id": interval.closing_observation_id,
        }
    interval.disconnected_at = disconnected_at
    interval.end_reliable = end_reliable
    interval.save(
        update_fields=["disconnected_at", "end_reliable", "updated_at"]
    )
    if window is not None and sample_observed_at is not None:
        result = _capture_cpa_window(
            config,
            account,
            "cpa_subscription_closed",
            guard,
            window=window,
            observed_at=sample_observed_at,
            raw_metadata={
                "collection_session_key": session_key,
                "collection_sample_kind": "closing",
            },
        )
        interval.closing_observation_id = result["observation_id"]
        interval.save(update_fields=["closing_observation", "updated_at"])
        return {
            "account_id": account.id,
            "collection_interval_id": interval.id,
            **result,
        }

    state = HistoryMaintenanceState.objects.select_for_update().get(
        account_id=account.fact_key
    )
    guard.assert_owned(state)
    state.fact_revision += 1
    state.save(update_fields=["fact_revision", "updated_at"])
    replay = rebuild_account(account.fact_key, config, guard=guard)
    return {
        "account_id": account.id,
        "status": "created",
        "collection_interval_id": interval.id,
        **replay.as_dict(),
    }


def persist_cpa_collection_disconnected(
    config: AppSettings,
    account: MonitoredAccount,
    *,
    session_key: str,
    connected_at,
    disconnected_at,
    end_reliable: bool,
    window: WeeklyWindow | None = None,
    sample_observed_at=None,
) -> dict:
    try:
        guard = LeaseGuard.acquire(account.fact_key)
    except LeaseBusyError:
        return _collection_busy(account)
    try:
        return _persist_cpa_collection_disconnected(
            config,
            account,
            guard,
            session_key=session_key,
            connected_at=connected_at,
            disconnected_at=disconnected_at,
            end_reliable=end_reliable,
            window=window,
            sample_observed_at=sample_observed_at,
        )
    finally:
        guard.release()
