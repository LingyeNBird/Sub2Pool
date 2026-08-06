"""周限容量和参与者用量统计 API。"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .base import AdminAPIView, ok
from .presenters import bounded_query_int, iso
from ..models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantUsageSample,
    QuotaCycle,
)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


RATE_METHOD = "cumulative_cycle_v1"
LOGICAL_CYCLE_RESET_TOLERANCE = timedelta(minutes=5)


def _capacity_summary(
    config: AppSettings,
    location: ZoneInfo,
    now: datetime,
) -> dict:
    """分别给出本周期累计折算和今日已覆盖观测区间的增量折算。"""
    empty_today = {
        "estimate_usd": None,
        "minimum_usd": None,
        "maximum_usd": None,
        "cost_delta_usd": None,
        "percent_delta": None,
        "sample_count": 0,
        "observed_from": None,
        "observed_to": None,
        "min_percent_span": float(config.daily_estimate_min_percent_span),
        "sufficient": False,
        "reason": "尚无当前上游周期，无法形成今日估算",
    }
    if not config.openai_account_id:
        return {"cycle": None, "today": empty_today}

    current_cycle = QuotaCycle.objects.filter(
        active=True,
        account_id=config.openai_account_id,
    ).first()
    if current_cycle is None:
        return {"cycle": None, "today": empty_today}

    logical_cycles = list(
        QuotaCycle.objects.filter(
            account_id=current_cycle.account_id,
            resets_at__gte=(
                current_cycle.resets_at - LOGICAL_CYCLE_RESET_TOLERANCE
            ),
            resets_at__lte=(
                current_cycle.resets_at + LOGICAL_CYCLE_RESET_TOLERANCE
            ),
        )
    )
    cycle_ids = [cycle.id for cycle in logical_cycles]
    observations = Observation.objects.filter(cycle_id__in=cycle_ids)
    latest = observations.order_by("-observed_at", "-id").first()
    if latest is None:
        return {"cycle": None, "today": empty_today}

    used_percent = latest.upstream_used_percent
    raw_cycle_estimate = (
        latest.selected_total_cost * Decimal("100") / used_percent
        if used_percent > 0
        else None
    )
    if used_percent >= 50:
        confidence = "高"
    elif used_percent >= 20:
        confidence = "中"
    else:
        confidence = "低"
    cycle_summary = {
        "estimate_usd": _money(
            latest.effective_usd_per_percent * Decimal("100")
        ),
        "raw_estimate_usd": (
            _money(raw_cycle_estimate)
            if raw_cycle_estimate is not None
            else None
        ),
        "cost_usd": _money(latest.selected_total_cost),
        "used_percent": float(used_percent),
        "confidence": confidence,
        "observed_at": iso(latest.observed_at),
        "starts_at": iso(min(cycle.starts_at for cycle in logical_cycles)),
        "resets_at": iso(current_cycle.resets_at),
    }

    local_day = now.astimezone(location).date()
    day_start = datetime.combine(local_day, time.min, tzinfo=location)
    today_rows = list(
        observations.filter(observed_at__gte=day_start).order_by(
            "observed_at",
            "id",
        )
    )
    today = {**empty_today, "sample_count": len(today_rows)}
    if not today_rows:
        today["reason"] = "今天尚无额度观测"
        return {"cycle": cycle_summary, "today": today}

    first = today_rows[0]
    last = today_rows[-1]
    cost_delta = last.selected_total_cost - first.selected_total_cost
    percent_delta = last.upstream_used_percent - first.upstream_used_percent
    today.update(
        {
            "cost_delta_usd": _money(cost_delta),
            "percent_delta": float(percent_delta),
            "observed_from": iso(first.observed_at),
            "observed_to": iso(last.observed_at),
        }
    )
    if len(today_rows) < 2:
        today["reason"] = "今天只有一次观测，无法计算日内增量"
        return {"cycle": cycle_summary, "today": today}
    if cost_delta <= 0:
        today["reason"] = "今日已覆盖观测区间没有正向成本增量"
        return {"cycle": cycle_summary, "today": today}
    if percent_delta < config.daily_estimate_min_percent_span:
        today["reason"] = (
            f"今日已覆盖观测区间仅跨过 {percent_delta:.2f}% 周限，"
            f"低于设置的 {config.daily_estimate_min_percent_span:.2f}%，"
            "整数百分比误差过大"
        )
        return {"cycle": cycle_summary, "today": today}

    estimate = cost_delta * Decimal("100") / percent_delta
    # 两端都是整数百分比，真实增量可能相差约一个百分点；用上下各
    # 一个百分点给出误差区间，避免把点估值误解成官方精确额度。
    minimum = cost_delta * Decimal("100") / (percent_delta + Decimal("1"))
    maximum = (
        cost_delta * Decimal("100") / (percent_delta - Decimal("1"))
        if percent_delta > 1
        else None
    )
    today.update(
        {
            "estimate_usd": _money(estimate),
            "minimum_usd": _money(minimum),
            "maximum_usd": (
                _money(maximum) if maximum is not None else None
            ),
            "sufficient": True,
            "reason": "按今日已覆盖观测区间的成本增量与周限增量折算",
        }
    )
    return {"cycle": cycle_summary, "today": today}


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
        capacity_summary = _capacity_summary(config, location, now)
        observation_rows = Observation.objects.filter(
            cycle__account_id=config.openai_account_id,
            raw_window__rate_method=RATE_METHOD,
            observed_at__gte=now - timedelta(days=capacity_days),
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
            # 账号周期用量用于归属权益；用户余额是 Sub2API 的全局可用余额。
            usage_buckets[sample.participant_id][bucket] = {
                "observed_at": sample.observed_at.isoformat(),
                "label": label,
                "account_cycle_usage_usd": float(sample.selected_cost),
                "balance_usd": (
                    float(sample.balance_usd)
                    if sample.balance_usd is not None
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
                "capacity_summary": capacity_summary,
                "usage_days": usage_days,
                "usage_precision": usage_precision,
                "sample_interval_minutes": config.local_poll_minutes,
                "participant_series": participant_series,
            }
        )
