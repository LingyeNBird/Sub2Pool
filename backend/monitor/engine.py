"""进度触发的额度采样引擎。

后台高频部分只读取 Sub2API 本地用量；达到阈值、额度耗尽、最长间隔或重置临近时才读取
上游百分比。采样只保存原始事实，所有区间识别、额度折算和参与者归属统一交给 replay
模块从最早受影响的边界向后重算。
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .accounting.boundaries import same_official_reset as _same_official_reset
from .integrations.sub2api import Sub2APIClient, Sub2APIError
from .models import AppSettings, Participant
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




def _run_monitor_locked(
    config: AppSettings,
    *,
    force_upstream: bool,
    requested_source: str,
) -> dict:
    if not config.monitoring_enabled and not force_upstream:
        return {"status": "disabled", "message": "监控已停用"}
    if not config.openai_account_id:
        raise Sub2APIError("尚未配置 OpenAI 账号 ID")
    participants = list(Participant.objects.filter(enabled=True))
    if not participants:
        raise Sub2APIError("尚未添加启用的拼车参与者")
    if sum((item.share_percent for item in participants), ZERO) > Decimal(100):
        raise Sub2APIError("启用参与者的权益比例合计不能超过 100%")

    account_id = config.openai_account_id
    now = timezone.now()
    latest_raw = _latest_raw(account_id)
    previous = _latest_included(account_id)
    previous_rate = previous.effective_usd_per_percent if previous else None

    with Sub2APIClient(config) as client:
        if latest_raw is None:
            window = client.query_weekly_window(
                account_id,
                config.quota_query_mode,
            )
            reference = _window_reference(account_id, window)
            local = _fetch_local(client, config, reference, participants, now)
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
            _save_local_bundle(
                config,
                reference,
                local,
                None,
                interval_logs=interval_logs,
            )
            observation = _create_raw_observation(
                config=config,
                reference=reference,
                window=window,
                local=local,
                source=requested_source,
                latest_raw=None,
                interval_logs=interval_logs,
                fast_interval=fast_interval,
                fast_error=fast_error,
            )
            rebuild_observation_suffix(observation, config)
            observation.refresh_from_db()
            _finish_success(config, local.checked_at)
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
            participants,
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
            _save_local_bundle(
                config,
                current_reference,
                local,
                previous,
                interval_logs=interval_logs,
            )
            AppSettings.objects.filter(pk=config.pk).update(
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
            config.quota_query_mode,
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
                participants,
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
        _save_local_bundle(
            config,
            reference,
            local,
            previous,
            interval_logs=interval_logs,
        )
        observation = _create_raw_observation(
            config=config,
            reference=reference,
            window=window,
            local=local,
            source=source,
            latest_raw=latest_raw,
            interval_logs=interval_logs,
            fast_interval=fast_interval,
            fast_error=fast_error,
        )
        rebuild_observation_suffix(observation, config)
        observation.refresh_from_db()
        _finish_success(config, local.checked_at)

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


def run_monitor(
    *,
    force_upstream: bool = False,
    source: str = "scheduled",
) -> dict:
    """执行一次探测。跨进程租约防止后台任务和手动按钮同时采集。"""

    config = AppSettings.load()
    now = timezone.now()
    lease_until = now + timedelta(minutes=10)
    acquired = (
        AppSettings.objects.filter(pk=1)
        .filter(
            Q(run_lease_until__isnull=True)
            | Q(run_lease_until__lt=now)
        )
        .update(run_lease_until=lease_until)
    )
    if not acquired:
        return {"status": "busy", "message": "已有采集任务正在执行"}
    try:
        return _run_monitor_locked(
            config,
            force_upstream=force_upstream,
            requested_source=source,
        )
    except Exception as exc:
        message = str(exc)[:1000]
        AppSettings.objects.filter(pk=1).update(
            last_local_check_at=timezone.now(),
            last_error=message,
        )
        notify_collection_error(config, message)
        raise
    finally:
        # 只释放自己持有的租约，避免超时后另一个进程接管时被旧任务误清除。
        AppSettings.objects.filter(
            pk=1,
            run_lease_until=lease_until,
        ).update(run_lease_until=None)
