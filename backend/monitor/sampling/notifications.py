"""一次有效观测完成后的通知投影。"""

from datetime import datetime
from decimal import Decimal

from ..models import AppSettings, MonitoredAccount, Observation
from ..notifications import send_notification
from ..reporting import aggregate_recommendation

CENT = Decimal("0.01")


def send_observation_notifications(
    config: AppSettings,
    observation: Observation,
    previous_rate: Decimal | None,
) -> None:
    """Send account-aware alerts using the participant's global recommendation."""

    account = MonitoredAccount.objects.filter(
        external_account_id=observation.account_id
    ).first()
    account_label = account.name if account is not None else str(observation.account_id)
    interval_key = (
        observation.attribution_started_at.isoformat()
        if observation.attribution_started_at
        else str(observation.pk)
    )
    for snapshot in observation.participant_snapshots.select_related(
        "participant"
    ):
        aggregate, _sources = aggregate_recommendation(
            snapshot.participant,
            config,
        )
        if aggregate is None or not aggregate["recommendation_complete"]:
            continue
        recommended = aggregate["recommended_balance_usd"]
        exhausted = bool(
            snapshot.participant.latest_balance_usd is not None
            and snapshot.participant.latest_balance_usd
            <= config.limit_warning_usd
        )
        if (
            exhausted
            and recommended is not None
            and recommended > 0
            and config.notify_on_limit_exhausted
        ):
            send_notification(
                config=config,
                event_type="limit_exhausted",
                dedupe_key=(
                    f"balance-exhausted:{observation.account_id}:{interval_key}:"
                    f"{snapshot.participant_id}:{recommended}"
                ),
                participant=snapshot.participant,
                subject=(
                    f"[拼车额度] {snapshot.participant.name} 需要补充全局余额"
                ),
                body=(
                    f"账号 {account_label} 的新观测触发了全局余额检查。\n\n"
                    f"当前 Sub2API 用户余额："
                    f"${snapshot.participant.latest_balance_usd}\n"
                    f"跨账号净额化后的混池建议余额：${recommended}\n"
                    f"参与混池账号数：{aggregate['account_count']}\n\n"
                    "请核对后在 Sub2Pool 中应用混池建议。"
                ),
                severity="error",
            )
        elif (
            aggregate["needs_manual_update"]
            and config.notify_on_recommendation_change
        ):
            send_notification(
                config=config,
                event_type="recommendation_changed",
                dedupe_key=(
                    f"balance-recommendation:{observation.account_id}:"
                    f"{interval_key}:{snapshot.participant_id}:{recommended}"
                ),
                participant=snapshot.participant,
                subject=(
                    f"[拼车额度] {snapshot.participant.name} 的混池余额建议已变化"
                ),
                body=(
                    f"触发账号：{account_label}\n"
                    f"跨账号净额化后的混池建议余额：${recommended}\n"
                    f"原因：{aggregate['reason']}\n"
                    "请登录服务查看各账号净权益与混池贡献。"
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
                    f"新模型估算：${effective_rate}/%\n"
                    f"变化：{change.quantize(CENT)}%"
                ),
            )


def finish_success(
    config: AppSettings,
    account: MonitoredAccount,
    checked_at: datetime,
) -> None:
    MonitoredAccount.objects.filter(pk=account.pk).update(
        last_local_check_at=checked_at,
        last_upstream_check_at=checked_at,
        last_success_at=checked_at,
        last_error="",
    )
    AppSettings.objects.filter(pk=config.pk).update(
        last_local_check_at=checked_at,
        last_upstream_check_at=checked_at,
        last_success_at=checked_at,
        last_error="",
    )
