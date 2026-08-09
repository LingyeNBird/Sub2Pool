"""周限容量和参与者用量统计 API。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .base import AuthenticatedAPIView, ok
from .query_params import bounded_query_int
from ..models import AppSettings
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
