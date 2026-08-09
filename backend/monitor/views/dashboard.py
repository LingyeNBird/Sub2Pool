"""首页额度总览 API。"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404

from django.utils import timezone

from .base import AdminAPIView, error, ok
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    display_cycle_rates,
    display_recommendation,
    iso,
    participant_data,
)
from ..models import AppSettings, Observation, Participant
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..replay import RATE_METHOD


def _admin_url(value: str) -> str:
    """只把已配置地址的来源暴露给浏览器，不附加任何管理后台路径。"""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = (
        f"{hostname}:{parsed.port}"
        if parsed.port is not None
        else hostname
    )
    return f"{parsed.scheme}://{authority}"


class DashboardView(AdminAPIView):
    def get(self, _request):
        config = AppSettings.load()
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            config.openai_account_id,
        )
        snapshot_stale = bool(
            config.last_upstream_check_at
            and timezone.now() - config.last_upstream_check_at
            >= timedelta(hours=config.stale_warning_hours)
        )
        observation = (
            Observation.objects.filter(
                account_id=config.openai_account_id,
                excluded_at__isnull=True,
                attribution_started_at__isnull=False,
            )
            .prefetch_related("participant_snapshots__participant")
            .order_by("-observed_at", "-id")
            .first()
            if config.openai_account_id
            else None
        )
        total_charged = Decimal("0")
        display_rate, raw_rate = (
            display_cycle_rates(observation, config)
            if observation
            else (None, None)
        )
        participant_rows = [
            participant_data(item, config)
            for item in Participant.objects.filter(enabled=True)
        ]
        total_charged = sum(
            (
                Decimal(str(item["snapshot"]["charged_cycle_percent"]))
                for item in participant_rows
                if item["snapshot"]
            ),
            Decimal("0"),
        )
        unattributed_used_percent = Decimal("0")
        if observation is not None:
            residual_attribution = observation.model_diagnostics.get(
                "residual_attributed_percent"
            )
            if (
                config.weekly_quota_model == "time_varying"
                and residual_attribution is not None
            ):
                unattributed_used_percent = Decimal(
                    str(residual_attribution)
                )
            else:
                unattributed_used_percent = max(
                    Decimal("0"),
                    observation.estimated_used_percent - total_charged,
                )
        data = {
            "configured": bool(
                config.sub2api_admin_token_encrypted and config.openai_account_id
            ),
            "monitoring_enabled": config.monitoring_enabled,
            "last_local_check_at": iso(config.last_local_check_at),
            "last_upstream_check_at": iso(config.last_upstream_check_at),
            "snapshot_stale": snapshot_stale,
            "last_success_at": iso(config.last_success_at),
            "last_error": config.last_error,
            "quota_query_mode": config.quota_query_mode,
            "sub2api_admin_url": _admin_url(config.sub2api_base_url),
            "fast_correction_enabled": config.fast_correction_enabled,
            "weekly_quota_model": config.weekly_quota_model,
            "cycle": None,
            "participants": [
                item
                for item in participant_rows
                if item["snapshot"]
                and item["snapshot"]["needs_manual_update"]
                and not item["snapshot"]["recommendation_applied"]
            ],
            "needs_manual_update_count": sum(
                1
                for item in participant_rows
                if item["snapshot"]
                and item["snapshot"]["needs_manual_update"]
            ),
        }
        if observation:
            data["cycle"] = {
                "id": observation.id,
                "observed_at": iso(observation.observed_at),
                "starts_at": iso(observation.attribution_started_at),
                "resets_at": iso(observation.upstream_resets_at),
                "upstream_used_percent": (
                    float(observation.upstream_used_percent) if observation else None
                ),
                "interval_used_percent": float(
                    observation.interval_used_percent
                ),
                "effective_usd_per_percent": (
                    float(display_rate) if display_rate is not None else None
                ),
                "selected_total_cost": (
                    float(observation.selected_total_cost) if observation else None
                ),
                "selected_total_cost_breakdown": (
                    cost_breakdowns.for_observation(observation)
                ),
                "start_cost_breakdown": cost_breakdowns.zero(),
                "unattributed_used_percent": float(
                    unattributed_used_percent
                ),
                "sample_note": observation.sample_note if observation else "",
                "snapshot_sampled_at": (
                    observation.raw_window.get("sampled_at") if observation else None
                ),
                "rate_calculated": (
                    raw_rate is not None
                    if config.weekly_quota_model == "constant_average"
                    else bool(observation.model_diagnostics)
                ),
                "estimated_used_percent": float(
                    observation.estimated_used_percent
                ),
                "capacity_lower_usd": (
                    float(observation.capacity_lower_usd)
                    if observation.capacity_lower_usd is not None
                    else None
                ),
                "capacity_upper_usd": (
                    float(observation.capacity_upper_usd)
                    if observation.capacity_upper_usd is not None
                    else None
                ),
                "model_diagnostics": observation.model_diagnostics,
            }
        return ok(data)


class ApplyParticipantRecommendationView(AdminAPIView):
    """仅响应管理员的显式点击，不会被后台监控或其他自动任务调用。"""

    def post(self, _request, participant_id: int):
        participant = get_object_or_404(
            Participant,
            pk=participant_id,
            enabled=True,
        )
        config = AppSettings.load()
        snapshot, recommended = display_recommendation(participant, config)
        if snapshot is None or recommended is None:
            return error("该参与者尚无可应用的额度建议", 409)
        if snapshot.recommendation_applied:
            return error("该条额度建议已经应用", 409)

        if recommended <= 0:
            return error(
                "Sub2API 原生余额调整接口不允许把余额设为 0，请前往管理后台手动处理",
                409,
            )
        try:
            with Sub2APIClient(config) as client:
                confirmed = client.set_user_balance_from_recommendation(
                    participant.sub2api_user_id,
                    recommended,
                )
        except Sub2APIError as exc:
            return error(str(exc), 502)

        snapshot.current_balance_usd = confirmed
        snapshot.balance_difference_usd = Decimal("0")
        snapshot.needs_manual_update = False
        snapshot.recommendation_applied = True
        snapshot.reason = "已一键应用建议余额"
        snapshot.save(
            update_fields=[
                "current_balance_usd",
                "balance_difference_usd",
                "needs_manual_update",
                "recommendation_applied",
                "reason",
            ]
        )

        now = timezone.now()
        participant.latest_balance_usd = confirmed
        participant.last_checked_at = now
        participant.updated_at = now
        participant.save(
            update_fields=[
                "latest_balance_usd",
                "last_checked_at",
                "updated_at",
            ]
        )
        return ok(
            {
                "participant_id": participant.id,
                "sub2api_user_id": participant.sub2api_user_id,
                "applied_balance_usd": float(confirmed),
            }
        )
