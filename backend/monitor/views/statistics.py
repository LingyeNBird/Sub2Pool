"""周限容量和参与者用量统计 API。"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .base import AdminAPIView, ok
from .presenters import bounded_query_int
from ..models import AppSettings, Observation, Participant, ParticipantUsageSample


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


class StatisticsView(AdminAPIView):
    def get(self, request):
        config = AppSettings.load()
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
        observation_rows = Observation.objects.filter(
            observed_at__gte=now - timedelta(days=capacity_days)
        ).order_by("observed_at", "id")
        daily: dict[str, dict] = {}
        for observation in observation_rows:
            period = observation.observed_at.astimezone(location).date().isoformat()
            total = observation.effective_usd_per_percent * Decimal("100")
            row = daily.setdefault(
                period,
                {
                    "period": period,
                    "weekly_total_usd": total,
                    "minimum_usd": total,
                    "maximum_usd": total,
                    "sample_count": 0,
                },
            )
            # 查询集按时间升序，覆盖后的值就是当天最后一次保守估算。
            row["weekly_total_usd"] = total
            row["minimum_usd"] = min(row["minimum_usd"], total)
            row["maximum_usd"] = max(row["maximum_usd"], total)
            row["sample_count"] += 1

        if capacity_period == "day":
            capacity_series = [
                {
                    **row,
                    "weekly_total_usd": _money(row["weekly_total_usd"]),
                    "minimum_usd": _money(row["minimum_usd"]),
                    "maximum_usd": _money(row["maximum_usd"]),
                }
                for row in daily.values()
            ]
        else:
            monthly: dict[str, list[dict]] = defaultdict(list)
            for row in daily.values():
                monthly[row["period"][:7]].append(row)
            capacity_series = []
            for period, rows in monthly.items():
                closing_values = [row["weekly_total_usd"] for row in rows]
                capacity_series.append(
                    {
                        "period": period,
                        # 月值使用每日收盘值的平均数，避免活跃日采样多造成偏置。
                        "weekly_total_usd": _money(
                            sum(closing_values, Decimal("0"))
                            / Decimal(len(closing_values))
                        ),
                        "minimum_usd": _money(
                            min(row["minimum_usd"] for row in rows)
                        ),
                        "maximum_usd": _money(
                            max(row["maximum_usd"] for row in rows)
                        ),
                        "sample_count": len(rows),
                    }
                )

        sample_rows = (
            ParticipantUsageSample.objects.filter(
                observed_at__gte=now - timedelta(days=usage_days)
            )
            .select_related("participant")
            .order_by("participant_id", "observed_at", "id")
        )
        usage_buckets: dict[int, dict[str, dict]] = defaultdict(dict)
        for sample in sample_rows:
            local = sample.observed_at.astimezone(location)
            if usage_precision == "raw":
                bucket = sample.observed_at.isoformat()
                label = local.strftime("%m-%d %H:%M")
            elif usage_precision == "hour":
                bucket = local.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                ).isoformat()
                label = local.strftime("%m-%d %H:00")
            else:
                bucket = local.date().isoformat()
                label = local.strftime("%m-%d")
            # 同一显示桶保留最后一次探测，表达该时点可见的 Sub2API 周用量。
            usage_buckets[sample.participant_id][bucket] = {
                "observed_at": sample.observed_at.isoformat(),
                "label": label,
                "weekly_usage_usd": float(sample.weekly_usage_usd),
                "weekly_limit_usd": (
                    float(sample.weekly_limit_usd)
                    if sample.weekly_limit_usd is not None
                    else None
                ),
            }

        participant_series = [
            {
                "participant_id": participant.id,
                "participant_name": participant.name,
                "sub2api_user_id": participant.sub2api_user_id,
                "points": list(usage_buckets[participant.id].values()),
            }
            for participant in Participant.objects.all()
        ]
        return ok(
            {
                "capacity_period": capacity_period,
                "capacity_series": capacity_series,
                "usage_days": usage_days,
                "usage_precision": usage_precision,
                "sample_interval_minutes": config.local_poll_minutes,
                "participant_series": participant_series,
            }
        )
