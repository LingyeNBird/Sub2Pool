"""进度触发的测算引擎。

本服务不定时高频查询 OpenAI 周限。后台只做较轻的 Sub2API 本地用量探测；只有累计美元变化足以覆盖
配置的百分比阈值、有人触顶、活跃期最长校准间隔到达、临近重置或管理员手动要求时，才查询上游百分比。
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
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
    QuotaCycle,
)
from .notifications import notify_collection_error, send_notification
from .sub2api import Sub2APIClient, Sub2APIError, UsageStats, UserBalance, WeeklyWindow

ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")
RESET_ROLLBACK_TOLERANCE = Decimal("0.1")
RATE_METHOD = "cumulative_cycle_v1"
RESET_TIME_TOLERANCE_SECONDS = 300


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


def _epoch_datetime(value: int) -> datetime:
    # 某些代理会把 Unix 秒错误地转成毫秒；兼容两种格式但最终统一为 UTC。
    if value > 10_000_000_000:
        value //= 1000
    return datetime.fromtimestamp(value, tz=dt_timezone.utc)


def _weighted_percentile(samples: list[tuple[Decimal, Decimal]], percentile: int) -> Decimal:
    """按消耗百分比加权的较低分位数，防止一次偏高样本造成超分配。"""
    samples = [(rate, max(weight, Decimal("0.0001"))) for rate, weight in samples if rate > 0]
    if not samples:
        raise ValueError("no samples")
    samples.sort(key=lambda item: item[0])
    target = sum((weight for _, weight in samples), ZERO) * Decimal(percentile) / Decimal(100)
    cumulative = ZERO
    for rate, weight in samples:
        cumulative += weight
        if cumulative >= target:
            return rate
    return samples[-1][0]


def _cycle_dates(cycle: QuotaCycle, timezone_name: str, now: datetime) -> tuple[datetime.date, datetime.date]:
    location = ZoneInfo(timezone_name)
    return cycle.starts_at.astimezone(location).date(), now.astimezone(location).date()


def _fetch_local(
    client: Sub2APIClient,
    config: AppSettings,
    cycle: QuotaCycle,
    participants: list[Participant],
    now: datetime,
) -> LocalBundle:
    start_date, end_date = _cycle_dates(cycle, config.timezone, now)
    total = client.usage_stats(
        account_id=cycle.account_id,
        start_date=start_date,
        end_date=end_date,
        timezone_name=config.timezone,
    )
    rows: list[LocalParticipantData] = []
    for participant in participants:
        stats = client.usage_stats(
            account_id=cycle.account_id,
            user_id=participant.sub2api_user_id,
            start_date=start_date,
            end_date=end_date,
            timezone_name=config.timezone,
        )
        balance = client.user_balance(participant.sub2api_user_id)
        rows.append(
            LocalParticipantData(
                participant=participant,
                stats=stats,
                balance=balance,
            )
        )

    # 展示字段更新不参与账本计算；即使本次没有访问上游，首页也能看到较新的余额状态。
    for row in rows:
        row.participant.latest_balance_usd = row.balance.balance
        row.participant.latest_selected_cost = row.selected_cost(config.cost_basis)
        row.participant.last_checked_at = now
    Participant.objects.bulk_update(
        [row.participant for row in rows],
        ["latest_balance_usd", "latest_selected_cost", "last_checked_at"],
    )
    ParticipantUsageSample.objects.bulk_create(
        [
            ParticipantUsageSample(
                participant=row.participant,
                cycle=cycle,
                observed_at=now,
                balance_usd=row.balance.balance,
                selected_cost=row.selected_cost(config.cost_basis),
            )
            for row in rows
        ],
        ignore_conflicts=True,
    )
    return LocalBundle(total=total, participants=rows, checked_at=now)


def _ensure_cycle(
    config: AppSettings,
    window: WeeklyWindow,
    current: QuotaCycle | None,
    *,
    force_new: bool = False,
    starts_at_override: datetime | None = None,
) -> tuple[QuotaCycle, bool]:
    reset_at = _epoch_datetime(window.reset_at)
    starts_at = starts_at_override or (
        reset_at - timedelta(seconds=window.window_seconds)
    )
    same = (
        not force_new
        and current is not None
        and current.account_id == config.openai_account_id
        and abs((current.resets_at - reset_at).total_seconds())
        <= RESET_TIME_TOLERANCE_SECONDS
    )
    if same:
        return current, False

    QuotaCycle.objects.filter(active=True).update(active=False)
    cycle_seconds = max(1, int((reset_at - starts_at).total_seconds()))
    cycle, _ = QuotaCycle.objects.get_or_create(
        account_id=config.openai_account_id,
        resets_at=reset_at,
        starts_at=starts_at,
        defaults={"window_seconds": cycle_seconds, "active": True},
    )
    if not cycle.active:
        cycle.active = True
        cycle.window_seconds = cycle_seconds
        cycle.save(update_fields=["active", "window_seconds"])
    return cycle, True


def _effective_rate(
    config: AppSettings,
    cycle: QuotaCycle,
    current_rate: Decimal | None,
    current_weight: Decimal | None,
) -> tuple[Decimal, str]:
    # 上游百分比快照通常只保留整数。若直接用相邻两次观测的增量相除，
    # 百分比从 16% 跳到 17% 前累积在“16% 平台”里的消费会被漏掉，
    # 一个很短的尾段就可能被错误当成完整 1% 的成本。
    history_limit = config.rate_history_samples - (
        1 if current_rate is not None else 0
    )
    history = list(
        Observation.objects.filter(
            cycle=cycle,
            valid_sample=True,
            sample_usd_per_percent__isnull=False,
            raw_window__rate_method=RATE_METHOD,
        )
        .order_by("-observed_at")
        .values_list("sample_usd_per_percent", "upstream_used_percent")[
            :history_limit
        ]
    )
    samples = [
        (Decimal(str(rate)), Decimal(str(weight or 0)))
        for rate, weight in history
    ]
    if current_rate is not None and current_weight is not None:
        samples.append((current_rate, current_weight))
    if samples:
        return (
            _weighted_percentile(samples, config.conservative_percentile),
            "current_cycle_samples",
        )

    # 正常换周不代表账号容量失效。新周期产生自己的有效样本前，沿用该
    # OpenAI 账号最近一次由真实样本形成的保守估值；只有一个从未测算过
    # 的账号才使用“无样本时美元 / 1%”。
    previous_rate = (
        Observation.objects.filter(
            cycle__account_id=cycle.account_id,
            valid_sample=True,
            sample_usd_per_percent__isnull=False,
            raw_window__rate_method=RATE_METHOD,
        )
        .exclude(cycle=cycle)
        .order_by("-observed_at", "-id")
        .values_list("effective_usd_per_percent", flat=True)
        .first()
    )
    if previous_rate is not None:
        return Decimal(str(previous_rate)), "previous_cycle_history"
    return config.initial_usd_per_percent, "initial_fallback"


def _is_limit_exhausted(config: AppSettings, row: LocalParticipantData, previous: ParticipantSnapshot | None) -> bool:
    if previous is None or previous.remaining_share_percent <= 0:
        return False
    return row.balance.balance <= config.limit_warning_usd


def _collect_observation(
    *,
    config: AppSettings,
    cycle: QuotaCycle,
    window: WeeklyWindow,
    local: LocalBundle,
    source: str,
) -> Observation:
    previous = Observation.objects.filter(cycle=cycle).order_by("-observed_at").first()
    selected_total = local.total.selected(config.cost_basis)
    used_percent = window.used_percent
    has_cumulative_sample = Observation.objects.filter(
        cycle=cycle,
        valid_sample=True,
        sample_usd_per_percent__isnull=False,
        raw_window__rate_method=RATE_METHOD,
    ).exists()

    delta_percent: Decimal | None = None
    delta_cost: Decimal | None = None
    sample_rate: Decimal | None = None
    valid_sample = False
    note = "首次观测，当前没有足够数据形成累计口径样本"
    if previous is not None:
        delta_percent = used_percent - previous.upstream_used_percent
        delta_cost = selected_total - previous.selected_total_cost

    # 美元/1% 使用“本周期累计成本 ÷ 当前已用百分比”。这与用户实际关心的
    # 总周限容量口径一致，也不会被整数百分比快照的跳变边界放大。
    if selected_total > 0 and used_percent > 0 and (
        previous is None or not has_cumulative_sample
    ):
        sample_rate = selected_total / used_percent
        valid_sample = True
        note = "本周期累计口径初始化样本"
    elif previous is not None:
        if delta_percent > 0 and delta_cost > 0:
            sample_rate = selected_total / used_percent
            valid_sample = True
            note = "有效累计口径样本"
        elif delta_percent == 0:
            note = "上游百分比未变化，本次不更新美元/百分比"
        elif delta_percent < 0:
            note = "上游百分比回退，本次不倒扣参与者账本"
        else:
            note = "成本没有正向变化，本次样本无效"

    effective_rate, rate_source = _effective_rate(
        config,
        cycle,
        sample_rate,
        used_percent if valid_sample else None,
    )
    if rate_source == "previous_cycle_history":
        note = "本周期尚无有效样本，暂沿用上一周期有效估值"
    previous_rate = previous.effective_usd_per_percent if previous else None

    previous_snapshots: dict[int, ParticipantSnapshot] = {}
    if previous:
        previous_snapshots = {item.participant_id: item for item in previous.participant_snapshots.all()}

    participant_deltas: dict[int, Decimal] = {}
    for row in local.participants:
        old = previous_snapshots.get(row.participant.id)
        participant_deltas[row.participant.id] = (
            row.selected_cost(config.cost_basis) - old.selected_cost if old is not None else row.selected_cost(config.cost_basis)
        )
    positive_participant_total = sum((max(ZERO, value) for value in participant_deltas.values()), ZERO)
    attribution_total = selected_total if previous is None else max(ZERO, delta_cost or ZERO)
    # 个别用量之和偶尔会因统计边界或延迟略高于账号总量；放大分母可确保分配百分比守恒。
    attribution_denominator = max(attribution_total, positive_participant_total)

    with transaction.atomic():
        observation = Observation.objects.create(
            cycle=cycle,
            source=source,
            observed_at=local.checked_at,
            upstream_used_percent=used_percent,
            selected_total_cost=selected_total,
            total_standard_cost=local.total.total_cost,
            total_actual_cost=local.total.total_actual_cost,
            delta_percent=delta_percent,
            delta_cost=delta_cost,
            sample_usd_per_percent=sample_rate,
            effective_usd_per_percent=effective_rate,
            valid_sample=valid_sample,
            sample_note=note,
            raw_window={
                "slot": window.slot,
                "window_seconds": window.window_seconds,
                "reset_after_seconds": window.reset_after_seconds,
                "reset_at": window.reset_at,
                "query_mode": config.quota_query_mode,
                "sampled_at": window.sampled_at,
                "rate_method": RATE_METHOD,
                "conservative_percentile": config.conservative_percentile,
                "rate_history_samples": config.rate_history_samples,
                "rate_source": rate_source,
            },
        )

        snapshots: list[ParticipantSnapshot] = []
        for row in local.participants:
            participant = row.participant
            old = previous_snapshots.get(participant.id)
            positive_delta = max(ZERO, participant_deltas[participant.id])
            charged_delta = ZERO
            if attribution_denominator > 0:
                if previous is None:
                    charged_delta = used_percent * positive_delta / attribution_denominator
                elif valid_sample and delta_percent is not None:
                    charged_delta = delta_percent * positive_delta / attribution_denominator
            charged = max(ZERO, (old.charged_cycle_percent if old else ZERO) + charged_delta)
            remaining = max(ZERO, participant.share_percent - charged)
            recommended = (
                remaining * effective_rate * config.safety_factor
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            difference = (
                recommended - row.balance.balance
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            exhausted = row.balance.balance <= config.limit_warning_usd
            needs_update = (
                abs(difference) >= config.recommendation_change_usd
                or (exhausted and remaining > 0)
            )
            if remaining <= 0:
                reason = "本上游周期的百分比权益已用尽"
            elif exhausted:
                reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
            elif needs_update:
                reason = "当前用户余额与最新测算建议差异较大"
            else:
                reason = "当前用户余额无需调整"
            snapshots.append(
                ParticipantSnapshot(
                    observation=observation,
                    participant=participant,
                    selected_cost=row.selected_cost(config.cost_basis),
                    delta_cost=None if old is None else participant_deltas[participant.id],
                    charged_delta_percent=charged_delta.quantize(PCT_PRECISION, rounding=ROUND_HALF_UP),
                    charged_cycle_percent=charged.quantize(PCT_PRECISION, rounding=ROUND_HALF_UP),
                    remaining_share_percent=remaining.quantize(PCT_PRECISION, rounding=ROUND_HALF_UP),
                    current_balance_usd=row.balance.balance,
                    recommended_balance_usd=recommended,
                    balance_difference_usd=difference,
                    needs_manual_update=needs_update,
                    reason=reason,
                )
            )
        ParticipantSnapshot.objects.bulk_create(snapshots)

    config.last_local_check_at = local.checked_at
    config.last_upstream_check_at = local.checked_at
    config.last_success_at = local.checked_at
    config.last_error = ""
    config.save(update_fields=["last_local_check_at", "last_upstream_check_at", "last_success_at", "last_error", "updated_at"])

    # 通知在数据库事务提交后发送；邮件失败不能回滚已经完成的测算。
    for snapshot in observation.participant_snapshots.select_related("participant"):
        exhausted = (
            snapshot.current_balance_usd is not None
            and snapshot.current_balance_usd <= config.limit_warning_usd
        )
        if exhausted and snapshot.remaining_share_percent > 0 and config.notify_on_limit_exhausted:
            send_notification(
                config=config,
                event_type="limit_exhausted",
                dedupe_key=f"balance-exhausted:{cycle.id}:{snapshot.participant_id}:{snapshot.recommended_balance_usd}",
                participant=snapshot.participant,
                subject=f"[拼车额度] {snapshot.participant.name} 需要手动补充余额",
                body=(
                    f"{snapshot.participant.name} 的 Sub2API 用户余额已接近耗尽。\n\n"
                    f"当前用户余额：${snapshot.current_balance_usd}\n"
                    f"剩余百分比权益：{snapshot.remaining_share_percent}%\n"
                    f"建议手动把用户余额设置为：${snapshot.recommended_balance_usd}\n\n"
                    "本服务不会自动修改 Sub2API。请核对后在 Sub2API 管理台手动操作。"
                ),
                severity="error",
            )
        elif snapshot.needs_manual_update and config.notify_on_recommendation_change:
            send_notification(
                config=config,
                event_type="recommendation_changed",
                dedupe_key=f"balance-recommendation:{cycle.id}:{snapshot.participant_id}:{snapshot.recommended_balance_usd}",
                participant=snapshot.participant,
                subject=f"[拼车额度] {snapshot.participant.name} 的余额建议已变化",
                body=f"建议用户余额：${snapshot.recommended_balance_usd}\n原因：{snapshot.reason}\n请登录服务查看测算依据。",
            )

    if previous_rate and previous_rate > 0 and config.notify_on_rate_change:
        change = abs(effective_rate - previous_rate) / previous_rate * Decimal(100)
        if change >= config.rate_change_alert_percent:
            send_notification(
                config=config,
                event_type="rate_changed",
                dedupe_key=f"rate-change:{cycle.id}",
                subject="[拼车额度] 美元/百分比估算发生明显变化",
                body=f"原估算：${previous_rate}/%\n新保守估算：${effective_rate}/%\n变化：{change.quantize(CENT)}%",
            )
    return observation


def _run_monitor_locked(config: AppSettings, *, force_upstream: bool, requested_source: str) -> dict:
    if not config.monitoring_enabled and not force_upstream:
        return {"status": "disabled", "message": "监控已停用"}
    if not config.openai_account_id:
        raise Sub2APIError("尚未配置 OpenAI 账号 ID")
    participants = list(Participant.objects.filter(enabled=True))
    if not participants:
        raise Sub2APIError("尚未添加启用的拼车参与者")
    if sum((item.share_percent for item in participants), ZERO) > Decimal(100):
        raise Sub2APIError("启用参与者的权益比例合计不能超过 100%")

    now = timezone.now()
    current = QuotaCycle.objects.filter(active=True, account_id=config.openai_account_id).first()
    with Sub2APIClient(config) as client:
        if current is None:
            window = client.query_weekly_window(config.openai_account_id, config.quota_query_mode)
            cycle, _ = _ensure_cycle(config, window, None)
            local = _fetch_local(client, config, cycle, participants, now)
            observation = _collect_observation(config=config, cycle=cycle, window=window, local=local, source=requested_source)
            return {"status": "calibrated", "observation_id": observation.id, "reason": "首次观测"}

        local = _fetch_local(client, config, current, participants, now)
        previous = Observation.objects.filter(cycle=current).order_by("-observed_at").first()
        previous_snapshots = {item.participant_id: item for item in previous.participant_snapshots.all()} if previous else {}
        selected_total = local.total.selected(config.cost_basis)
        cost_rolled_back = bool(
            previous
            and selected_total + CENT < previous.selected_total_cost
        )
        cost_progress = (
            max(ZERO, selected_total - previous.selected_total_cost)
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
                previous_snapshots.get(row.participant.id),
            )
            for row in local.participants
        )
        active_too_long = bool(
            previous
            and cost_progress > 0
            and now - previous.observed_at
            >= timedelta(hours=config.active_max_calibration_hours)
        )
        reset_near = (
            now
            >= current.resets_at
            - timedelta(minutes=config.reset_proximity_minutes)
        )
        due = (
            force_upstream
            or previous is None
            or cost_progress >= threshold_cost
            or cost_rolled_back
            or exhausted
            or active_too_long
            or reset_near
        )

        if not due:
            AppSettings.objects.filter(pk=1).update(last_local_check_at=now, last_success_at=now, last_error="")
            return {
                "status": "local_only",
                "reason": "累计进度尚未达到额度快照采样阈值",
                "cost_progress": float(cost_progress),
                "threshold_cost": float(threshold_cost),
            }

        window = client.query_weekly_window(
            config.openai_account_id,
            config.quota_query_mode,
        )
        same_reset = (
            abs(
                (
                    _epoch_datetime(window.reset_at) - current.resets_at
                ).total_seconds()
            )
            <= RESET_TIME_TOLERANCE_SECONDS
        )
        manual_refresh = bool(
            same_reset
            and previous
            and window.used_percent + RESET_ROLLBACK_TOLERANCE
            < previous.upstream_used_percent
        )
        cycle, changed = _ensure_cycle(
            config,
            window,
            current,
            force_new=manual_refresh,
            starts_at_override=now if manual_refresh else None,
        )
        if changed:
            local = _fetch_local(client, config, cycle, participants, now)
        source = requested_source
        if manual_refresh:
            source = "reset"
        elif exhausted and not force_upstream:
            source = "exhausted"
        elif reset_near and not force_upstream:
            source = "reset"
        observation = _collect_observation(
            config=config,
            cycle=cycle,
            window=window,
            local=local,
            source=source,
        )
        return {
            "status": "calibrated",
            "observation_id": observation.id,
            "reason": (
                "检测到官方手动刷新"
                if manual_refresh
                else "上游周期已变化"
                if changed
                else "达到进度触发条件"
            ),
        }


def run_monitor(*, force_upstream: bool = False, source: str = "scheduled") -> dict:
    """执行一次探测。跨进程租约防止后台任务和手动按钮同时采集。"""
    config = AppSettings.load()
    now = timezone.now()
    lease_until = now + timedelta(minutes=10)
    acquired = AppSettings.objects.filter(pk=1).filter(Q(run_lease_until__isnull=True) | Q(run_lease_until__lt=now)).update(run_lease_until=lease_until)
    if not acquired:
        return {"status": "busy", "message": "已有采集任务正在执行"}
    try:
        return _run_monitor_locked(config, force_upstream=force_upstream, requested_source=source)
    except Exception as exc:
        message = str(exc)[:1000]
        AppSettings.objects.filter(pk=1).update(last_local_check_at=timezone.now(), last_error=message)
        notify_collection_error(config, message)
        raise
    finally:
        # 只释放自己持有的租约，避免超时后另一个进程接管时被旧任务误清除。
        AppSettings.objects.filter(pk=1, run_lease_until=lease_until).update(run_lease_until=None)
