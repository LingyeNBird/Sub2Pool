"""进度触发的额度采样引擎。

后台高频部分只读取 Sub2API 本地用量；达到阈值、额度耗尽、最长间隔或重置临近时才读取
上游百分比。采样只保存原始事实，所有区间识别、额度折算和参与者归属统一交给 replay
模块从全部历史数据重算，避免一次误采样留下不可逆的周期状态。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
)
from .notifications import notify_collection_error, send_notification
from .replay import (
    RATE_METHOD,
    RESET_ROLLBACK_TOLERANCE,
    RESET_TIME_TOLERANCE,
    rebuild_account,
)
from .sub2api import (
    Sub2APIClient,
    Sub2APIError,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass
class LocalParticipantData:
    participant: Participant
    stats: UsageStats
    balance: UserBalance

    def selected_cost(self, basis: str) -> Decimal:
        return self.stats.selected(basis)


@dataclass
class LocalBundle:
    total: UsageStats
    participants: list[LocalParticipantData]
    checked_at: datetime


@dataclass(frozen=True)
class WindowReference:
    account_id: int
    reset_at: datetime
    window_seconds: int


def _epoch_datetime(value: int) -> datetime:
    """兼容被代理误转成毫秒的 Unix 时间，内部统一为 UTC。"""

    if value > 10_000_000_000:
        value //= 1000
    return datetime.fromtimestamp(value, tz=dt_timezone.utc)


def _window_reference(account_id: int, window: WeeklyWindow) -> WindowReference:
    return WindowReference(
        account_id=account_id,
        reset_at=_epoch_datetime(window.reset_at),
        window_seconds=window.window_seconds,
    )


def _observation_reference(observation: Observation) -> WindowReference:
    return WindowReference(
        account_id=observation.account_id,
        reset_at=observation.upstream_resets_at,
        window_seconds=observation.window_seconds,
    )


def _same_official_reset(left: datetime, right: datetime) -> bool:
    return abs(left - right) <= RESET_TIME_TOLERANCE


def _fetch_local(
    client: Sub2APIClient,
    config: AppSettings,
    reference: WindowReference,
    participants: list[Participant],
    now: datetime,
) -> LocalBundle:
    """只读取 Sub2API；数据库写入在最终确认查询窗口后统一执行一次。"""

    location = ZoneInfo(config.timezone)
    start_date = (
        reference.reset_at - timedelta(seconds=reference.window_seconds)
    ).astimezone(location).date()
    end_date = now.astimezone(location).date()
    total = client.usage_stats(
        account_id=reference.account_id,
        start_date=start_date,
        end_date=end_date,
        timezone_name=config.timezone,
    )
    rows: list[LocalParticipantData] = []
    for participant in participants:
        stats = client.usage_stats(
            account_id=reference.account_id,
            user_id=participant.sub2api_user_id,
            start_date=start_date,
            end_date=end_date,
            timezone_name=config.timezone,
        )
        rows.append(
            LocalParticipantData(
                participant=participant,
                stats=stats,
                balance=client.user_balance(participant.sub2api_user_id),
            )
        )
    return LocalBundle(total=total, participants=rows, checked_at=now)


def _participant_baselines(
    latest: Observation | None,
) -> dict[int, Decimal]:
    if latest is None:
        return {}
    return {
        snapshot.participant_id: max(
            ZERO,
            snapshot.raw_selected_cost - snapshot.selected_cost,
        )
        for snapshot in latest.participant_snapshots.all()
    }


def _save_local_bundle(
    config: AppSettings,
    reference: WindowReference,
    local: LocalBundle,
    latest: Observation | None,
) -> None:
    """保存一次本地趋势点；raw 字段永远保留 Sub2API 返回的累计值。"""

    baselines = _participant_baselines(latest)
    participants: list[Participant] = []
    usage_samples: list[ParticipantUsageSample] = []
    for row in local.participants:
        raw_cost = row.selected_cost(config.cost_basis)
        selected_cost = max(
            ZERO,
            raw_cost - baselines.get(row.participant.pk, ZERO),
        )
        row.participant.latest_balance_usd = row.balance.balance
        row.participant.latest_selected_cost = selected_cost
        row.participant.last_checked_at = local.checked_at
        participants.append(row.participant)
        usage_samples.append(
            ParticipantUsageSample(
                participant=row.participant,
                account_id=reference.account_id,
                attribution_started_at=(
                    latest.attribution_started_at if latest is not None else None
                ),
                observed_at=local.checked_at,
                balance_usd=row.balance.balance,
                selected_cost=selected_cost,
                raw_selected_cost=raw_cost,
            )
        )
    if participants:
        Participant.objects.bulk_update(
            participants,
            ["latest_balance_usd", "latest_selected_cost", "last_checked_at"],
        )
        ParticipantUsageSample.objects.bulk_create(
            usage_samples,
            ignore_conflicts=True,
        )


def _latest_included(account_id: int) -> Observation | None:
    return (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
        )
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )


def _latest_raw(account_id: int) -> Observation | None:
    return (
        Observation.objects.filter(account_id=account_id)
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )


def _has_pending_rollback(account_id: int) -> bool:
    return Observation.objects.filter(
        account_id=account_id,
        exclusion_source="automatic",
        exclusion_reason__contains="等待后续采样确认",
    ).exists()


def _is_limit_exhausted(
    config: AppSettings,
    row: LocalParticipantData,
    previous: ParticipantSnapshot | None,
) -> bool:
    if previous is None or previous.remaining_share_percent <= 0:
        return False
    return row.balance.balance <= config.limit_warning_usd


@transaction.atomic
def _create_raw_observation(
    *,
    config: AppSettings,
    reference: WindowReference,
    window: WeeklyWindow,
    local: LocalBundle,
    source: str,
) -> Observation:
    """持久化不可变采样事实；派生字段先给安全初值，随后由重放器覆盖。"""

    selected_total = local.total.selected(config.cost_basis)
    observation = Observation.objects.create(
        account_id=reference.account_id,
        source=source,
        observed_at=local.checked_at,
        window_seconds=reference.window_seconds,
        upstream_resets_at=reference.reset_at,
        upstream_used_percent=window.used_percent,
        raw_selected_total_cost=selected_total,
        selected_total_cost=selected_total,
        total_standard_cost=local.total.total_cost,
        total_actual_cost=local.total.total_actual_cost,
        effective_usd_per_percent=config.initial_usd_per_percent,
        sample_note="等待全量重放",
        raw_window={
            "slot": window.slot,
            "window_seconds": window.window_seconds,
            "reset_after_seconds": window.reset_after_seconds,
            "reset_at": window.reset_at,
            "query_mode": config.quota_query_mode,
            "sampled_at": window.sampled_at,
            "rate_method": RATE_METHOD,
        },
    )
    ParticipantSnapshot.objects.bulk_create(
        [
            ParticipantSnapshot(
                observation=observation,
                participant=row.participant,
                raw_selected_cost=row.selected_cost(config.cost_basis),
                selected_cost=row.selected_cost(config.cost_basis),
                current_balance_usd=row.balance.balance,
                remaining_share_percent=row.participant.share_percent,
            )
            for row in local.participants
        ]
    )
    return observation


def _send_observation_notifications(
    config: AppSettings,
    observation: Observation,
    previous_rate: Decimal | None,
) -> None:
    """仅对这次最终被纳入重放结果的观测发送通知。"""

    interval_key = (
        observation.attribution_started_at.isoformat()
        if observation.attribution_started_at
        else str(observation.pk)
    )
    for snapshot in observation.participant_snapshots.select_related(
        "participant"
    ):
        exhausted = bool(
            snapshot.current_balance_usd is not None
            and snapshot.current_balance_usd <= config.limit_warning_usd
        )
        if (
            exhausted
            and snapshot.remaining_share_percent > 0
            and config.notify_on_limit_exhausted
        ):
            send_notification(
                config=config,
                event_type="limit_exhausted",
                dedupe_key=(
                    f"balance-exhausted:{interval_key}:"
                    f"{snapshot.participant_id}:{snapshot.recommended_balance_usd}"
                ),
                participant=snapshot.participant,
                subject=(
                    f"[拼车额度] {snapshot.participant.name} 需要手动补充余额"
                ),
                body=(
                    f"{snapshot.participant.name} 的 Sub2API 用户余额已接近耗尽。\n\n"
                    f"当前用户余额：${snapshot.current_balance_usd}\n"
                    f"剩余百分比权益：{snapshot.remaining_share_percent}%\n"
                    "建议手动把用户余额设置为："
                    f"${snapshot.recommended_balance_usd}\n\n"
                    "请核对后在 Sub2API 管理台手动操作。"
                ),
                severity="error",
            )
        elif (
            snapshot.needs_manual_update
            and config.notify_on_recommendation_change
        ):
            send_notification(
                config=config,
                event_type="recommendation_changed",
                dedupe_key=(
                    f"balance-recommendation:{interval_key}:"
                    f"{snapshot.participant_id}:{snapshot.recommended_balance_usd}"
                ),
                participant=snapshot.participant,
                subject=(
                    f"[拼车额度] {snapshot.participant.name} 的余额建议已变化"
                ),
                body=(
                    f"建议用户余额：${snapshot.recommended_balance_usd}\n"
                    f"原因：{snapshot.reason}\n请登录服务查看测算依据。"
                ),
            )

    effective_rate = observation.effective_usd_per_percent
    if previous_rate and previous_rate > 0 and config.notify_on_rate_change:
        change = (
            abs(effective_rate - previous_rate)
            / previous_rate
            * Decimal(100)
        )
        if change >= config.rate_change_alert_percent:
            send_notification(
                config=config,
                event_type="rate_changed",
                dedupe_key=f"rate-change:{interval_key}:{observation.pk}",
                subject="[拼车额度] 美元/百分比估算发生明显变化",
                body=(
                    f"原估算：${previous_rate}/%\n"
                    f"新保守估算：${effective_rate}/%\n"
                    f"变化：{change.quantize(CENT)}%"
                ),
            )


def _finish_success(config: AppSettings, checked_at: datetime) -> None:
    AppSettings.objects.filter(pk=config.pk).update(
        last_local_check_at=checked_at,
        last_upstream_check_at=checked_at,
        last_success_at=checked_at,
        last_error="",
    )


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
            _save_local_bundle(config, reference, local, None)
            observation = _create_raw_observation(
                config=config,
                reference=reference,
                window=window,
                local=local,
                source=requested_source,
            )
            rebuild_account(account_id, config)
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
        previous_snapshots = (
            {
                item.participant_id: item
                for item in previous.participant_snapshots.all()
            }
            if previous
            else {}
        )
        selected_total = local.total.selected(config.cost_basis)
        cost_rolled_back = bool(
            previous
            and selected_total + CENT < previous.raw_selected_total_cost
        )
        cost_progress = (
            max(ZERO, selected_total - previous.raw_selected_total_cost)
            if previous
            else selected_total
        )
        effective_rate = (
            previous.effective_usd_per_percent
            if previous
            else config.initial_usd_per_percent
        )
        threshold_cost = effective_rate * config.progress_threshold_percent
        exhausted = any(
            _is_limit_exhausted(
                config,
                row,
                previous_snapshots.get(row.participant.pk),
            )
            for row in local.participants
        )
        active_too_long = bool(
            previous
            and cost_progress > 0
            and now - previous.observed_at
            >= timedelta(hours=config.active_max_calibration_hours)
        )
        reset_near = now >= (
            latest_raw.upstream_resets_at
            - timedelta(minutes=config.reset_proximity_minutes)
        )
        due = bool(
            force_upstream
            or previous is None
            or cost_progress >= threshold_cost
            or cost_rolled_back
            or exhausted
            or active_too_long
            or reset_near
            or _has_pending_rollback(account_id)
        )

        if not due:
            _save_local_bundle(
                config,
                current_reference,
                local,
                previous,
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

        _save_local_bundle(config, reference, local, previous)
        observation = _create_raw_observation(
            config=config,
            reference=reference,
            window=window,
            local=local,
            source=source,
        )
        rebuild_account(account_id, config)
        observation.refresh_from_db()
        _finish_success(config, local.checked_at)

        if observation.excluded_at is not None:
            return {
                "status": "reset_pending",
                "observation_id": observation.pk,
                "reason": observation.exclusion_reason,
            }

        _send_observation_notifications(config, observation, previous_rate)
        segment_reason = observation.raw_window.get("replay_segment_reason")
        if segment_reason == "confirmed_manual_refresh":
            reason = "完整历史中已有两份独立快照确认官方手动刷新"
        elif official_window_changed:
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
