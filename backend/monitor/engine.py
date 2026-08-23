"""进度触发的额度采样引擎。

后台高频部分只读取 Sub2API 本地用量；达到阈值、额度耗尽、最长间隔或重置临近时才读取
上游百分比。采样只保存原始事实，所有区间识别、额度折算和参与者归属统一交给 replay
模块从最早受影响的边界向后重算。
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .accounting.boundaries import same_official_reset as _same_official_reset
from .integrations.sub2api import Sub2APIClient, Sub2APIError
from .history_state import LeaseBusyError, LeaseGuard
from .models import (
    AccountParticipant,
    AppSettings,
    HistoryMaintenanceState,
    MonitoredAccount,
    PoolParticipant,
)
from .notifications import notify_collection_error
from .replay import RESET_ROLLBACK_TOLERANCE, rebuild_observation_suffix
from .sampling.local_usage import (
    fetch_interval_bridge_logs as _fetch_interval_bridge_logs,
    fetch_local as _fetch_local,
    save_local_bundle as _save_local_bundle,
)
from .sampling.notifications import (
    finish_success as _finish_success,
    send_observation_notifications as _send_observation_notifications,
)
from .sampling.observations import (
    create_raw_observation as _create_raw_observation,
    fetch_fast_correction as _fetch_fast_correction,
)
from .sampling.selectors import (
    has_pending_rollback as _has_pending_rollback,
    latest_included as _latest_included,
    latest_raw as _latest_raw,
)
from .sampling.triggers import evaluate_sampling_trigger
from .sampling.types import (
    observation_reference as _observation_reference,
    window_reference as _window_reference,
)

ZERO = Decimal("0")


@transaction.atomic
def _persist_capture(
    config,
    account,
    reference,
    local,
    previous,
    *,
    latest_raw,
    guard: LeaseGuard,
    capture_started_at,
    interval_logs,
    window=None,
    source="scheduled",
    fast_interval=None,
    fast_error="",
):
    guard.renew()
    state = HistoryMaintenanceState.objects.select_for_update().get(
        account_id=reference.account_id
    )
    guard.assert_owned(state)
    point = _save_local_bundle(
        config,
        reference,
        local,
        previous,
        latest_raw=latest_raw,
        interval_logs=interval_logs,
        capture_started_at=capture_started_at,
        capture_finished_at=timezone.now(),
    )
    observation = None
    if window is not None:
        observation = _create_raw_observation(
            config=config,
            reference=reference,
            quota_query_mode=account.quota_query_mode,
            window=window,
            local=local,
            source=source,
            sample_point=point,
            latest_raw=latest_raw,
            interval_logs=interval_logs,
            fast_interval=fast_interval,
            fast_error=fast_error,
        )
    state.fact_revision += 1
    state.save(update_fields=["fact_revision", "updated_at"])
    point.fact_revision = state.fact_revision
    point.save(update_fields=["fact_revision"])
    return observation




def _run_monitor_locked(
    config: AppSettings,
    account: MonitoredAccount,
    force_upstream: bool,
    requested_source: str,
    guard: LeaseGuard,
) -> dict:
    if not config.monitoring_enabled and not force_upstream:
        return {"status": "disabled", "message": "监控已停用"}
    allocations = list(
        PoolParticipant.objects.select_related("participant", "pool")
        .filter(
            pool_id=account.pool_id,
            participant__enabled=True,
            share_percent__gt=ZERO,
        )
        .order_by("-participant__is_owner", "participant_id")
    )
    if not allocations:
        raise Sub2APIError("该额度池尚未分配给任何启用的拼车参与者")
    if sum((item.share_percent for item in allocations), ZERO) > Decimal(100):
        raise Sub2APIError("该额度池的启用参与者权益比例合计不能超过 100%")
    participants = [allocation.participant for allocation in allocations]
    allocations_by_participant_id = {
        allocation.participant_id: allocation for allocation in allocations
    }
    existing_participant_ids = set(
        AccountParticipant.objects.filter(account=account).values_list(
            "participant_id",
            flat=True,
        )
    )
    AccountParticipant.objects.bulk_create(
        [
            AccountParticipant(account=account, participant=participant)
            for participant in participants
            if participant.id not in existing_participant_ids
        ],
        ignore_conflicts=True,
    )
    memberships_by_participant = {
        item.participant_id: item
        for item in AccountParticipant.objects.select_related(
            "participant"
        ).filter(
            account=account,
            participant_id__in=[item.id for item in participants],
        )
    }
    memberships = [
        memberships_by_participant[participant.id] for participant in participants
    ]

    account_id = account.external_account_id
    now = timezone.now()
    latest_raw = _latest_raw(account_id)
    previous = _latest_included(account_id)
    previous_rate = previous.effective_usd_per_percent if previous else None

    with Sub2APIClient(config) as client:
        if latest_raw is None:
            window = client.query_weekly_window(
                account_id,
                account.quota_query_mode,
            )
            reference = _window_reference(account_id, window)
            local = _fetch_local(
                client,
                config,
                reference,
                memberships,
                allocations_by_participant_id,
                now,
            )
            interval_logs = _fetch_interval_bridge_logs(
                client,
                config,
                reference,
                local,
                None,
            )
            fast_interval, fast_error = _fetch_fast_correction(
                client,
                config,
                reference,
                None,
                local.checked_at,
            )
            observation = _persist_capture(
                config,
                account,
                reference,
                local,
                None,
                latest_raw=None,
                guard=guard,
                capture_started_at=now,
                interval_logs=interval_logs,
                window=window,
                source=requested_source,
                fast_interval=fast_interval,
                fast_error=fast_error,
            )
            rebuild_observation_suffix(observation, config, guard=guard)
            observation.refresh_from_db()
            _finish_success(config, account, local.checked_at)
            if observation.excluded_at is None:
                _send_observation_notifications(
                    config,
                    observation,
                    previous_rate,
                )
            return {
                "status": (
                    "calibrated"
                    if observation.excluded_at is None
                    else "reset_pending"
                ),
                "observation_id": observation.pk,
                "reason": (
                    "首次观测"
                    if observation.excluded_at is None
                    else observation.exclusion_reason
                ),
            }

        current_reference = _observation_reference(latest_raw)
        local = _fetch_local(
            client,
            config,
            current_reference,
            memberships,
            allocations_by_participant_id,
            now,
        )
        trigger = evaluate_sampling_trigger(
            config=config,
            local=local,
            latest_raw=latest_raw,
            previous=previous,
            now=now,
            force_upstream=force_upstream,
            has_pending_rollback=_has_pending_rollback(account_id),
        )
        cost_progress = trigger.cost_progress
        threshold_cost = trigger.threshold_cost
        exhausted = trigger.exhausted
        reset_near = trigger.reset_near
        due = trigger.due

        if not due:
            interval_logs = _fetch_interval_bridge_logs(
                client,
                config,
                current_reference,
                local,
                latest_raw,
            )
            _persist_capture(
                config,
                account,
                current_reference,
                local,
                previous,
                latest_raw=latest_raw,
                guard=guard,
                capture_started_at=now,
                interval_logs=interval_logs,
            )
            MonitoredAccount.objects.filter(pk=account.pk).update(
                last_local_check_at=now,
                last_success_at=now,
                last_error="",
            )
            return {
                "status": "local_only",
                "reason": "累计进度尚未达到额度快照采样阈值",
                "cost_progress": float(cost_progress),
                "threshold_cost": float(threshold_cost),
            }

        window = client.query_weekly_window(
            account_id,
            account.quota_query_mode,
        )
        reference = _window_reference(account_id, window)
        official_window_changed = not _same_official_reset(
            reference.reset_at,
            latest_raw.upstream_resets_at,
        )
        if official_window_changed:
            # 原先的本地用量查询使用旧窗口日期；重置后必须按新窗口重读。
            local = _fetch_local(
                client,
                config,
                reference,
                memberships,
                allocations_by_participant_id,
                now,
            )

        rollback = bool(
            not official_window_changed
            and previous
            and window.used_percent + RESET_ROLLBACK_TOLERANCE
            < previous.upstream_used_percent
        )
        source = requested_source
        if official_window_changed or rollback:
            source = "reset"
        elif exhausted and not force_upstream:
            source = "exhausted"
        elif reset_near and not force_upstream:
            source = "reset"

        interval_logs = _fetch_interval_bridge_logs(
            client,
            config,
            reference,
            local,
            latest_raw,
        )
        fast_interval, fast_error = _fetch_fast_correction(
            client,
            config,
            reference,
            latest_raw,
            local.checked_at,
        )
        observation = _persist_capture(
            config,
            account,
            reference,
            local,
            previous,
            latest_raw=latest_raw,
            guard=guard,
            capture_started_at=now,
            interval_logs=interval_logs,
            window=window,
            source=source,
            fast_interval=fast_interval,
            fast_error=fast_error,
        )
        rebuild_observation_suffix(observation, config, guard=guard)
        observation.refresh_from_db()
        _finish_success(config, account, local.checked_at)

        if observation.excluded_at is not None:
            return {
                "status": "reset_pending",
                "observation_id": observation.pk,
                "reason": observation.exclusion_reason,
            }

        _send_observation_notifications(config, observation, previous_rate)
        if official_window_changed:
            reason = "上游官方窗口已变化"
        else:
            reason = "达到进度触发条件"
        return {
            "status": "calibrated",
            "observation_id": observation.pk,
            "reason": reason,
        }


def _run_account_monitor(
    config: AppSettings,
    account: MonitoredAccount,
    *,
    force_upstream: bool,
    source: str,
) -> dict:
    try:
        guard = LeaseGuard.acquire(account.external_account_id)
    except LeaseBusyError:
        return {
            "status": "busy",
            "account_id": account.id,
            "message": "该账号已有采集或历史维护任务正在执行",
        }
    try:
        result = _run_monitor_locked(
            config,
            account,
            force_upstream=force_upstream,
            requested_source=source,
            guard=guard,
        )
        return {"account_id": account.id, **result}
    except Exception as exc:
        message = str(exc)[:1000]
        MonitoredAccount.objects.filter(pk=account.pk).update(
            last_local_check_at=timezone.now(),
            last_error=message,
        )
        notify_collection_error(
            config,
            f"{account.name}（{account.external_account_id}）：{message}",
        )
        raise
    finally:
        guard.release()


def run_monitor(
    *,
    account_id: int | None = None,
    force_upstream: bool = False,
    source: str = "scheduled",
) -> dict:
    """Run one or all enabled monitored accounts under account-scoped fences."""

    config = AppSettings.load()
    accounts = MonitoredAccount.objects.filter(enabled=True)
    if account_id is not None:
        accounts = accounts.filter(pk=account_id)
    account_rows = list(accounts.order_by("id"))
    if not account_rows:
        raise Sub2APIError("尚未配置启用的 OpenAI 上游账号")
    if account_id is not None:
        return _run_account_monitor(
            config,
            account_rows[0],
            force_upstream=force_upstream,
            source=source,
        )

    results = []
    for account in account_rows:
        try:
            results.append(
                _run_account_monitor(
                    config,
                    account,
                    force_upstream=force_upstream,
                    source=source,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "account_id": account.id,
                    "status": "error",
                    "message": str(exc)[:1000],
                }
            )
    return {
        "status": "completed",
        "accounts": results,
        "error_count": sum(item["status"] == "error" for item in results),
    }
