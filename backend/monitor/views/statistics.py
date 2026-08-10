"""周限容量和参与者用量统计 API。"""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status


from .base import AuthenticatedAPIView, error, ok
from .query_params import bounded_query_int
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import AppSettings, Observation, Participant
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    capacity_series,
    capacity_summary,
    participant_usage_series,
)


class StatisticsView(AuthenticatedAPIView):
    def get(self, request):
        config = AppSettings.load()
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            config.openai_account_id,
        )
        capacity_period = request.query_params.get("capacity_period", "day")
        if capacity_period not in {"day", "month"}:
            capacity_period = "day"
        capacity_days = bounded_query_int(
            request,
            "capacity_days",
            90 if capacity_period == "day" else 365,
            730,
        )
        usage_days = bounded_query_int(request, "usage_days", 7, 90)
        usage_precision = request.query_params.get("usage_precision", "hour")
        if usage_precision not in {"raw", "hour", "day"}:
            usage_precision = "hour"
        try:
            location = ZoneInfo(config.timezone)
        except ZoneInfoNotFoundError:
            location = ZoneInfo("UTC")

        now = timezone.now()
        return ok(
            {
                "capacity_period": capacity_period,
                "capacity_series": capacity_series(
                    config=config,
                    location=location,
                    now=now,
                    capacity_days=capacity_days,
                    capacity_period=capacity_period,
                    cost_breakdowns=cost_breakdowns,
                ),
                "fast_correction_enabled": config.fast_correction_enabled,
                "capacity_summary": capacity_summary(
                    config,
                    location,
                    now,
                    cost_breakdowns,
                ),
                "usage_days": usage_days,
                "usage_precision": usage_precision,
                "sample_interval_minutes": config.local_poll_minutes,
                "participant_series": participant_usage_series(
                    user=request.user,
                    location=location,
                    now=now,
                    usage_days=usage_days,
                    usage_precision=usage_precision,
                ),
            }
        )


class ParticipantAPIUsageView(AuthenticatedAPIView):
    """按当前归属周期只读聚合一个参与者的 Sub2API API 密钥用量。"""

    def get(self, request, participant_id: int):
        participants = Participant.objects.filter(enabled=True)
        if not request.user.is_staff:
            participants = participants.filter(authorized_users=request.user)
        participant = get_object_or_404(participants, pk=participant_id)
        config = AppSettings.load()
        if not config.openai_account_id:
            return error("尚未配置 OpenAI 上游账号", status.HTTP_409_CONFLICT)

        latest = (
            Observation.objects.filter(
                account_id=config.openai_account_id,
                excluded_at__isnull=True,
                attribution_started_at__isnull=False,
            )
            .order_by("-observed_at", "-id")
            .first()
        )
        if latest is None or latest.attribution_started_at is None:
            return error("尚无当前上游周期", status.HTTP_409_CONFLICT)

        observed_to = timezone.now()
        try:
            with Sub2APIClient(config) as client:
                keys = client.list_user_api_keys(participant.sub2api_user_id)
                logs = client.usage_logs(
                    account_id=config.openai_account_id,
                    user_id=participant.sub2api_user_id,
                    started_at=latest.attribution_started_at,
                    ended_at=observed_to,
                    timezone_name=config.timezone,
                )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), status.HTTP_502_BAD_GATEWAY)

        names = {
            int(item["id"]): str(item.get("name") or "").strip()
            for item in keys
        }
        statuses = {
            int(item["id"]): str(item.get("status") or "")
            for item in keys
        }
        costs: defaultdict[int, Decimal] = defaultdict(Decimal)
        for item in logs:
            costs[item.api_key_id] += item.selected(config.cost_basis)
            if item.api_key_id and item.api_key_name:
                names.setdefault(item.api_key_id, item.api_key_name)

        participant_total = sum(costs.values(), Decimal("0"))
        weekly_total = (
            latest.selected_total_cost
            * Decimal("100")
            / latest.interval_used_percent
            if latest.interval_used_percent > 0
            else None
        )
        key_ids = sorted(
            set(names) | set(costs),
            key=lambda key_id: (
                key_id == 0,
                names.get(key_id, "").casefold(),
                key_id,
            ),
        )

        def percentage(numerator: Decimal, denominator: Decimal | None) -> float:
            if denominator is None or denominator <= 0:
                return 0.0
            return float(
                (numerator * Decimal("100") / denominator).quantize(
                    Decimal("0.0001"),
                    rounding=ROUND_HALF_UP,
                )
            )

        return ok(
            {
                "participant_id": participant.id,
                "participant_name": participant.name,
                "sub2api_user_id": participant.sub2api_user_id,
                "starts_at": latest.attribution_started_at.isoformat(),
                "observed_to": observed_to.isoformat(),
                "cost_basis": config.cost_basis,
                "participant_total_usd": float(participant_total),
                "weekly_total_estimate_usd": (
                    float(weekly_total) if weekly_total is not None else None
                ),
                "participant_weekly_percent": percentage(
                    participant_total,
                    weekly_total,
                ),
                "api_keys": [
                    {
                        "api_key_id": key_id or None,
                        "name": (
                            names.get(key_id)
                            or (
                                "未识别或已删除的 API 密钥"
                                if key_id == 0
                                else f"API 密钥 {key_id}"
                            )
                        ),
                        "status": statuses.get(key_id, ""),
                        "usage_usd": float(costs[key_id]),
                        "participant_usage_percent": percentage(
                            costs[key_id],
                            participant_total,
                        ),
                        "weekly_quota_percent": percentage(
                            costs[key_id],
                            weekly_total,
                        ),
                    }
                    for key_id in key_ids
                ],
            }
        )
