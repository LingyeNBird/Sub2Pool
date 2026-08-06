"""首页额度总览 API。"""

from decimal import Decimal

from .base import AdminAPIView, ok
from .presenters import iso, participant_data
from ..models import AppSettings, Observation, Participant, QuotaCycle


class DashboardView(AdminAPIView):
    def get(self, _request):
        config = AppSettings.load()
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
        data = {
            "configured": bool(
                config.sub2api_admin_token_encrypted and config.openai_account_id
            ),
            "monitoring_enabled": config.monitoring_enabled,
            "last_local_check_at": iso(config.last_local_check_at),
            "last_upstream_check_at": iso(config.last_upstream_check_at),
            "last_success_at": iso(config.last_success_at),
            "last_error": config.last_error,
            "quota_query_mode": config.quota_query_mode,
            "cycle": None,
            "participants": [
                participant_data(item)
                for item in Participant.objects.filter(enabled=True)
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
            }
        return ok(data)
