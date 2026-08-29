"""周限容量和参与者用量统计 API。"""


from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated


from .base import PageAccessAPIView, error, ok
from .query_params import bounded_query_int, monitored_account_query
from ..access import visible_participants_for
from ..api_auth import APIKeyAuthentication
from ..api_usage import (
    api_usage_snapshot_data,
    get_participant_api_usage,
    latest_cycle_observation,
)
from ..integrations.sub2api import Sub2APIError
from ..models import AppSettings, PagePermission, Participant
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    capacity_series,
    cpa_api_key_usage_series,
    capacity_summary,
    participant_usage_series,
)


class StatisticsView(PageAccessAPIView):
    required_page_permissions = (PagePermission.STATISTICS,)

    def get(self, request):
        config = AppSettings.load()
        try:
            account = monitored_account_query(request)
        except ValueError as exc:
            return error(str(exc), status.HTTP_400_BAD_REQUEST)
        if account is None:
            return error("尚未配置启用的监控账号", status.HTTP_409_CONFLICT)
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            account.fact_key,
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
                "account": {
                    "id": account.id,
                    "provider": account.provider,
                    "source_account_id": account.source_account_id,
                    "external_account_id": account.external_account_id,
                    "name": account.name,
                },
                "capacity_period": capacity_period,
                "capacity_series": capacity_series(
                    config=config,
                    account=account,
                    location=location,
                    now=now,
                    capacity_days=capacity_days,
                    capacity_period=capacity_period,
                    cost_breakdowns=cost_breakdowns,
                ),
                "fast_correction_enabled": bool(
                    account.provider == "sub2api"
                    and config.fast_correction_enabled
                ),
                "capacity_summary": capacity_summary(
                    config,
                    account,
                    location,
                    now,
                    cost_breakdowns,
                ),
                "usage_days": usage_days,
                "usage_precision": usage_precision,
                "sample_interval_minutes": config.local_poll_minutes,
                "participant_series": (
                    participant_usage_series(
                        user=request.user,
                        account=account,
                        location=location,
                        now=now,
                        usage_days=usage_days,
                        usage_precision=usage_precision,
                    )
                    if account.provider == "sub2api"
                    else []
                ),
                "cpa_api_key_series": (
                    cpa_api_key_usage_series(
                        config=config,
                        account=account,
                        location=location,
                        now=now,
                        usage_days=usage_days,
                        usage_precision=usage_precision,
                    )
                    if account.provider == "cpa"
                    else []
                ),
            }
        )


class ReadOnlyStatisticsView(StatisticsView):
    """External API-key view exposing the statistics-page payload."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        if not request.query_params.get("account_id"):
            return error(
                "只读统计 API 必须指定 account_id",
                status.HTTP_400_BAD_REQUEST,
            )
        return super().get(request)


class ParticipantAPIUsageView(PageAccessAPIView):
    """按当前归属周期只读聚合一个参与者的 Sub2API API 密钥用量。"""

    required_page_permissions = (PagePermission.STATISTICS,)

    def get(self, request, participant_id: int):
        participants = visible_participants_for(
            request.user,
            Participant.objects.filter(enabled=True),
        )
        participant = get_object_or_404(participants, pk=participant_id)
        config = AppSettings.load()
        try:
            account = monitored_account_query(request, enabled_only=True)
        except ValueError as exc:
            return error(str(exc), status.HTTP_400_BAD_REQUEST)
        if account is None:
            return error("尚未配置启用的监控账号", status.HTTP_409_CONFLICT)
        if account.provider != "sub2api":
            return error("CPA 账号不使用参与者 API 用量接口", 400)

        observation = latest_cycle_observation(account)
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

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request, participant_id: int):
        if not request.query_params.get("account_id"):
            return error(
                "API 用量接口必须指定 account_id",
                status.HTTP_400_BAD_REQUEST,
            )
        return super().get(request, participant_id)
