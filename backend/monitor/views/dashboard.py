"""首页额度总览 API。"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404

from django.utils import timezone

from .base import AdminAPIView, error, ok
from .presenters import iso, latest_snapshot, participant_data
from ..models import AppSettings, Observation, Participant, QuotaCycle
from ..sub2api import Sub2APIClient, Sub2APIError


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
        snapshot_stale = bool(
            config.last_upstream_check_at
            and timezone.now() - config.last_upstream_check_at
            >= timedelta(hours=config.stale_warning_hours)
        )
        cycle = QuotaCycle.objects.filter(active=True).first()
        observation = (
            Observation.objects.filter(cycle=cycle)
            .prefetch_related("participant_snapshots__participant")
            .first()
            if cycle
            else None
        )
        snapshots = (
            list(
                observation.participant_snapshots.select_related("participant")
            )
            if observation
            else []
        )
        total_charged = sum(
            (item.charged_cycle_percent for item in snapshots),
            Decimal("0"),
        )
        basis_percentile = int(
            observation.raw_window.get(
                "conservative_percentile",
                config.conservative_percentile,
            )
            if observation
            else config.conservative_percentile
        )
        basis_history_samples = int(
            observation.raw_window.get(
                "rate_history_samples",
                config.rate_history_samples,
            )
            if observation
            else config.rate_history_samples
        )
        rate_rows = (
            list(
                Observation.objects.filter(
                    cycle=cycle,
                    valid_sample=True,
                    sample_usd_per_percent__isnull=False,
                    raw_window__rate_method="cumulative_cycle_v1",
                ).order_by("-observed_at", "-id")[:basis_history_samples]
            )
            if cycle
            else []
        )
        participant_rows = [
            participant_data(item)
            for item in Participant.objects.filter(enabled=True)
        ]
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
            "cycle": None,
            "participants": [
                item
                for item in participant_rows
                if not (
                    item["snapshot"]
                    and item["snapshot"]["recommendation_applied"]
                )
            ],
            "needs_manual_update_count": sum(
                1 for item in snapshots if item.needs_manual_update
            ),
        }
        if cycle:
            data["cycle"] = {
                "id": cycle.id,
                "starts_at": iso(cycle.starts_at),
                "resets_at": iso(cycle.resets_at),
                "upstream_used_percent": (
                    float(observation.upstream_used_percent) if observation else None
                ),
                "effective_usd_per_percent": (
                    float(observation.effective_usd_per_percent)
                    if observation
                    else None
                ),
                "selected_total_cost": (
                    float(observation.selected_total_cost) if observation else None
                ),
                "unattributed_used_percent": (
                    float(
                        max(
                            Decimal("0"),
                            observation.upstream_used_percent - total_charged,
                        )
                    )
                    if observation
                    else None
                ),
                "sample_note": observation.sample_note if observation else "",
                "snapshot_sampled_at": (
                    observation.raw_window.get("sampled_at") if observation else None
                ),
                "rate_calculated": bool(rate_rows),
                "conservative_percentile": basis_percentile,
                "rate_history_samples": basis_history_samples,
                "rate_sample_count": len(rate_rows),
                "rate_samples": [
                    {
                        "observed_at": iso(row.observed_at),
                        "cost_usd": float(row.selected_total_cost),
                        "used_percent": float(row.upstream_used_percent),
                        "usd_per_percent": float(row.sample_usd_per_percent),
                    }
                    for row in rate_rows
                ],
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
        snapshot = latest_snapshot(participant)
        if snapshot is None or snapshot.recommended_balance_usd is None:
            return error("该参与者尚无可应用的额度建议", 409)
        if snapshot.recommendation_applied:
            return error("该条额度建议已经应用", 409)

        recommended = snapshot.recommended_balance_usd
        if recommended <= 0:
            return error(
                "Sub2API 原生余额调整接口不允许把余额设为 0，请前往管理后台手动处理",
                409,
            )

        config = AppSettings.load()
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
