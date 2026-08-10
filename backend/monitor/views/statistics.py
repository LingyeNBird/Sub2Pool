"""周限容量和参与者用量统计 API。"""


from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated


from .base import AuthenticatedAPIView, error, ok
from .query_params import bounded_query_int
from ..api_auth import ReadOnlyAPIKeyAuthentication
from ..api_usage import (
    api_usage_snapshot_data,
    get_participant_api_usage,
    latest_cycle_observation,
)
from ..integrations.sub2api import Sub2APIError
from ..models import AppSettings, Participant
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


class ReadOnlyStatisticsView(StatisticsView):
    """External API-key view exposing the statistics-page payload."""

    authentication_classes = [ReadOnlyAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]


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

        observation = latest_cycle_observation(config)
        if observation is None or observation.attribution_started_at is None:
            return error("尚无当前上游周期", status.HTTP_409_CONFLICT)

        try:
            snapshot = get_participant_api_usage(
                participant=participant,
                observation=observation,
                config=config,
            )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), status.HTTP_502_BAD_GATEWAY)
        return ok(api_usage_snapshot_data(snapshot))


class ReadOnlyParticipantAPIUsageView(ParticipantAPIUsageView):
    """External API-key view exposing one participant's API usage breakdown."""

    authentication_classes = [ReadOnlyAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]
