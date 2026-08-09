"""一次有效观测完成后的通知投影。"""

from datetime import datetime
from decimal import Decimal

from ..models import AppSettings, Observation
from ..notifications import send_notification

CENT = Decimal("0.01")


def send_observation_notifications(
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
                    f"新模型估算：${effective_rate}/%\n"
                    f"变化：{change.quantize(CENT)}%"
                ),
            )


def finish_success(config: AppSettings, checked_at: datetime) -> None:
    AppSettings.objects.filter(pk=config.pk).update(
        last_local_check_at=checked_at,
        last_upstream_check_at=checked_at,
        last_success_at=checked_at,
        last_error="",
    )
