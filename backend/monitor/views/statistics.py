"""周限容量和参与者用量统计 API。"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .base import AuthenticatedAPIView, ok
from .presenters import bounded_query_int, iso
from ..models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantUsageSample,
)
from ..replay import RATE_METHOD


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _closing_basis(
    observation: Observation,
    rate_rows: list[Observation],
    config: AppSettings,
) -> dict:
    """给出某个每日收盘点当时可追溯的累计折算依据。"""
    used_percent = observation.interval_used_percent
    raw_estimate = (
        observation.selected_total_cost * Decimal("100") / used_percent
        if used_percent > 0
        else None
    )
    percentile = int(
        observation.raw_window.get(
            "conservative_percentile",
            config.conservative_percentile,
        )
    )
    history_samples = int(
        observation.raw_window.get(
            "rate_history_samples",
            config.rate_history_samples,
        )
    )
    return {
        "observed_at": iso(observation.observed_at),
        "starts_at": iso(observation.attribution_started_at),
        "start_cost_usd": 0.0,
        "start_percent": 0.0,
        "end_cost_usd": _money(observation.selected_total_cost),
        "end_percent": float(used_percent),
        "raw_estimate_usd": (
            _money(raw_estimate) if raw_estimate is not None else None
        ),
        "estimate_usd": _money(
            observation.effective_usd_per_percent * Decimal("100")
        ),
        "effective_usd_per_percent": float(
            observation.effective_usd_per_percent
        ),
        "rate_source": str(observation.raw_window.get("rate_source", "")),
        "sample_note": observation.sample_note,
        "conservative_percentile": percentile,
        "rate_history_samples": history_samples,
        "rate_sample_count": len(rate_rows),
        "rate_samples": [
            {
                "observed_at": iso(row.observed_at),
                "cost_usd": _money(row.selected_total_cost),
                "used_percent": float(row.interval_used_percent),
                "usd_per_percent": float(row.sample_usd_per_percent),
            }
            for row in reversed(rate_rows)
            if row.sample_usd_per_percent is not None
        ],
    }


def _daily_closing_basis(
    first: Observation,
    last: Observation,
    sample_count: int,
    config: AppSettings,
) -> dict | None:
    """按某日同一归属区间的首末观测计算日内增量折算。"""
    if sample_count < 2:
        return None
    cost_delta = last.selected_total_cost - first.selected_total_cost
    percent_delta = last.interval_used_percent - first.interval_used_percent
    if (
        cost_delta <= 0
        or percent_delta < config.daily_estimate_min_percent_span
    ):
        return None

    estimate = cost_delta * Decimal("100") / percent_delta
    minimum = cost_delta * Decimal("100") / (percent_delta + Decimal("1"))
    maximum = (
        cost_delta * Decimal("100") / (percent_delta - Decimal("1"))
        if percent_delta > 1
        else None
    )
    return {
        "observed_from": iso(first.observed_at),
        "observed_to": iso(last.observed_at),
        "start_cost_usd": _money(first.selected_total_cost),
        "start_percent": float(first.interval_used_percent),
        "end_cost_usd": _money(last.selected_total_cost),
        "end_percent": float(last.interval_used_percent),
        "cost_delta_usd": _money(cost_delta),
        "percent_delta": float(percent_delta),
        "estimate_usd": _money(estimate),
        "minimum_usd": _money(minimum),
        "maximum_usd": _money(maximum) if maximum is not None else None,
        "sample_count": sample_count,
        "min_percent_span": float(config.daily_estimate_min_percent_span),
    }



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
        "start_cost_usd": None,
        "start_percent": None,
        "end_cost_usd": None,
        "end_percent": None,
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

    latest = (
        Observation.objects.filter(
            account_id=config.openai_account_id,
            excluded_at__isnull=True,
            attribution_started_at__isnull=False,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return {"cycle": None, "today": empty_today}

    observations = Observation.objects.filter(
        account_id=latest.account_id,
        attribution_started_at=latest.attribution_started_at,
        excluded_at__isnull=True,
    )

    used_percent = latest.interval_used_percent
    raw_cycle_estimate = (
        latest.selected_total_cost * Decimal("100") / used_percent
        if used_percent > 0
        else None
    )
    basis_percentile = int(
        latest.raw_window.get(
            "conservative_percentile",
            config.conservative_percentile,
        )
    )
    basis_history_samples = int(
        latest.raw_window.get(
            "rate_history_samples",
            config.rate_history_samples,
        )
    )
    valid_rate_rows = list(
        observations.filter(
            valid_sample=True,
            sample_usd_per_percent__isnull=False,
            raw_window__rate_method=RATE_METHOD,
        ).order_by("-observed_at", "-id")[:basis_history_samples]
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
        "start_cost_usd": 0.0,
        "start_percent": 0.0,
        "end_cost_usd": _money(latest.selected_total_cost),
        "end_percent": float(used_percent),
        "cost_usd": _money(latest.selected_total_cost),
        "used_percent": float(used_percent),
        "effective_usd_per_percent": float(
            latest.effective_usd_per_percent
        ),
        "rate_calculated": bool(valid_rate_rows),
        "conservative_percentile": basis_percentile,
        "rate_history_samples": basis_history_samples,
        "rate_sample_count": len(valid_rate_rows),
        "rate_samples": [
            {
                "observed_at": iso(row.observed_at),
                "cost_usd": _money(row.selected_total_cost),
                "used_percent": float(row.interval_used_percent),
                "usd_per_percent": float(row.sample_usd_per_percent),
            }
            for row in valid_rate_rows
        ],
        "confidence": confidence,
        "observed_at": iso(latest.observed_at),
        "starts_at": iso(latest.attribution_started_at),
        "resets_at": iso(latest.upstream_resets_at),
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
    percent_delta = last.interval_used_percent - first.interval_used_percent
    today.update(
        {
            "start_cost_usd": _money(first.selected_total_cost),
            "start_percent": float(first.interval_used_percent),
            "end_cost_usd": _money(last.selected_total_cost),
            "end_percent": float(last.interval_used_percent),
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


class StatisticsView(AuthenticatedAPIView):
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
        capacity_start = datetime.combine(
            (now - timedelta(days=capacity_days)).astimezone(location).date(),
            time.min,
            tzinfo=location,
        )
        # 每个上游窗口最长七天；多取七天只用于还原范围起点附近收盘值
        # 当时可见的有效样本，不会把额外日期输出到图表。
        observation_rows = list(
            Observation.objects.filter(
                account_id=config.openai_account_id,
                excluded_at__isnull=True,
                raw_window__rate_method=RATE_METHOD,
                observed_at__gte=capacity_start - timedelta(days=7),
            ).order_by("observed_at", "id")
        )
        daily: dict[str, dict] = {}
        rate_histories: dict[tuple[int, datetime | None], list[Observation]] = (
            defaultdict(list)
        )
        for observation in observation_rows:
            history_key = (
                observation.account_id,
                observation.attribution_started_at,
            )
            history = rate_histories[history_key]
            history_samples = int(
                observation.raw_window.get(
                    "rate_history_samples",
                    config.rate_history_samples,
                )
            )
            previous_count = max(0, history_samples - 1)
            rate_rows = history[-previous_count:]
            if (
                observation.valid_sample
                and observation.sample_usd_per_percent is not None
            ):
                rate_rows = [*rate_rows, observation]
                history.append(observation)

            if observation.observed_at < capacity_start:
                continue
            period = (
                observation.observed_at.astimezone(location).date().isoformat()
            )
            total = observation.effective_usd_per_percent * Decimal("100")
            row = daily.setdefault(
                period,
                {
                    "period": period,
                    "weekly_total_usd": total,
                    "minimum_usd": total,
                    "maximum_usd": total,
                    "sample_count": 0,
                    "_closing_observation": observation,
                    "_rate_rows": rate_rows,
                    "_daily_segment_key": history_key,
                    "_daily_first_observation": observation,
                    "_daily_last_observation": observation,
                    "_daily_sample_count": 0,
                },
            )
            # 查询集按时间升序，覆盖后的值就是当天最后一次保守估算。
            row["weekly_total_usd"] = total
            row["minimum_usd"] = min(row["minimum_usd"], total)
            row["maximum_usd"] = max(row["maximum_usd"], total)
            row["sample_count"] += 1
            row["_closing_observation"] = observation
            row["_rate_rows"] = rate_rows
            if row["_daily_segment_key"] != history_key:
                row["_daily_segment_key"] = history_key
                row["_daily_first_observation"] = observation
                row["_daily_sample_count"] = 0
            row["_daily_last_observation"] = observation
            row["_daily_sample_count"] += 1
        for row in daily.values():
            row["_daily_basis"] = _daily_closing_basis(
                row["_daily_first_observation"],
                row["_daily_last_observation"],
                row["_daily_sample_count"],
                config,
            )
        if capacity_period == "day":
            capacity_series = [
                {
                    "period": row["period"],
                    "weekly_total_usd": _money(row["weekly_total_usd"]),
                    "minimum_usd": _money(row["minimum_usd"]),
                    "maximum_usd": _money(row["maximum_usd"]),
                    "sample_count": row["sample_count"],
                    "basis": _closing_basis(
                        row["_closing_observation"],
                        row["_rate_rows"],
                        config,
                    ),
                    "daily_total_usd": (
                        row["_daily_basis"]["estimate_usd"]
                        if row["_daily_basis"] is not None
                        else None
                    ),
                    "daily_basis": row["_daily_basis"],
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
                daily_estimates = [
                    row["_daily_basis"]["estimate_usd"]
                    for row in rows
                    if row["_daily_basis"] is not None
                ]
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
                        # 月值是多日收盘均值，不存在一个可追溯到单日端点的依据。
                        "basis": None,
                        "daily_total_usd": (
                            _money(
                                sum(
                                    (Decimal(str(value)) for value in daily_estimates),
                                    Decimal("0"),
                                )
                                / Decimal(len(daily_estimates))
                            )
                            if daily_estimates
                            else None
                        ),
                        "daily_basis": None,
                    }
                )

        participants = Participant.objects.all()
        if not request.user.is_staff:
            participants = participants.filter(authorized_users=request.user)

        sample_rows = (
            ParticipantUsageSample.objects.filter(
                observed_at__gte=now - timedelta(days=usage_days),
                participant__in=participants,
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
            for participant in participants
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
