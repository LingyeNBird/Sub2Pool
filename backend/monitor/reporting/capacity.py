"""周限容量摘要、每日收盘与月度聚合投影。"""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..models import AppSettings, MonitoredAccount, Observation
from .common import iso
from .costs import FastCorrectionBreakdownPresenter


def money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def closing_basis(
    observation: Observation,
    rate_rows: list[Observation],
    config: AppSettings,
    cost_breakdowns: FastCorrectionBreakdownPresenter,
) -> dict:
    """给出某个每日收盘点当时可追溯的累计折算依据。"""
    used_percent = observation.interval_used_percent
    raw_rate = (
        observation.selected_total_cost / used_percent
        if used_percent > 0
        else None
    )
    raw_estimate = (
        raw_rate * Decimal("100") if raw_rate is not None else None
    )
    del rate_rows, config
    end_cost_breakdown = cost_breakdowns.for_observation(observation)
    return {
        "observed_at": iso(observation.observed_at),
        "starts_at": iso(observation.attribution_started_at),
        "start_cost_usd": 0.0,
        "start_percent": 0.0,
        "end_cost_usd": money(observation.selected_total_cost),
        "start_cost_breakdown": cost_breakdowns.zero(),
        "end_cost_breakdown": end_cost_breakdown,
        "end_percent": float(used_percent),
        "raw_estimate_usd": (
            money(raw_estimate) if raw_estimate is not None else None
        ),
        "estimate_usd": (
            money(raw_estimate) if raw_estimate is not None else None
        ),
        "effective_usd_per_percent": (
            float(raw_rate) if raw_rate is not None else None
        ),
        "calculation_model": "endpoint_ratio",
        "rate_source": "cumulative_endpoint_ratio",
        "sample_note": "按当前区间累计成本 ÷ 累计整数百分比直接折算",
    }


def daily_closing_basis(
    first: Observation,
    last: Observation,
    sample_count: int,
    config: AppSettings,
    cost_breakdowns: FastCorrectionBreakdownPresenter,
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
        "start_cost_usd": money(first.selected_total_cost),
        "start_percent": float(first.interval_used_percent),
        "end_cost_usd": money(last.selected_total_cost),
        "end_percent": float(last.interval_used_percent),
        "start_cost_breakdown": cost_breakdowns.for_observation(first),
        "end_cost_breakdown": cost_breakdowns.for_observation(last),
        "cost_delta_usd": money(cost_delta),
        "percent_delta": float(percent_delta),
        "estimate_usd": money(estimate),
        "minimum_usd": money(minimum),
        "maximum_usd": money(maximum) if maximum is not None else None,
        "sample_count": sample_count,
        "min_percent_span": float(config.daily_estimate_min_percent_span),
    }


def capacity_summary(
    config: AppSettings,
    account: MonitoredAccount,
    location: ZoneInfo,
    now: datetime,
    cost_breakdowns: FastCorrectionBreakdownPresenter,
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
        "start_cost_breakdown": None,
        "end_cost_breakdown": None,
        "cost_delta_usd": None,
        "percent_delta": None,
        "sample_count": 0,
        "observed_from": None,
        "observed_to": None,
        "min_percent_span": float(config.daily_estimate_min_percent_span),
        "sufficient": False,
        "reason": "尚无当前上游周期，无法形成今日估算",
    }
    latest = (
        Observation.objects.filter(
            account_id=account.external_account_id,
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
    raw_cycle_rate = (
        latest.selected_total_cost / used_percent
        if used_percent > 0
        else None
    )
    raw_cycle_estimate = (
        raw_cycle_rate * Decimal("100")
        if raw_cycle_rate is not None
        else None
    )
    if used_percent >= 50:
        confidence = "高"
    elif used_percent >= 20:
        confidence = "中"
    else:
        confidence = "低"
    cycle = {
        "estimate_usd": (
            money(raw_cycle_estimate)
            if raw_cycle_estimate is not None
            else None
        ),
        "raw_estimate_usd": (
            money(raw_cycle_estimate)
            if raw_cycle_estimate is not None
            else None
        ),
        "start_cost_usd": 0.0,
        "start_percent": 0.0,
        "end_cost_usd": money(latest.selected_total_cost),
        "start_cost_breakdown": cost_breakdowns.zero(),
        "end_cost_breakdown": cost_breakdowns.for_observation(latest),
        "end_percent": float(used_percent),
        "cost_usd": money(latest.selected_total_cost),
        "used_percent": float(used_percent),
        "effective_usd_per_percent": (
            float(raw_cycle_rate) if raw_cycle_rate is not None else None
        ),
        "calculation_model": "endpoint_ratio",
        "rate_calculated": raw_cycle_rate is not None,
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
        return {"cycle": cycle, "today": today}

    first = today_rows[0]
    last = today_rows[-1]
    cost_delta = last.selected_total_cost - first.selected_total_cost
    percent_delta = last.interval_used_percent - first.interval_used_percent
    today.update(
        {
            "start_cost_usd": money(first.selected_total_cost),
            "start_percent": float(first.interval_used_percent),
            "end_cost_usd": money(last.selected_total_cost),
            "end_percent": float(last.interval_used_percent),
            "start_cost_breakdown": cost_breakdowns.for_observation(first),
            "end_cost_breakdown": cost_breakdowns.for_observation(last),
            "cost_delta_usd": money(cost_delta),
            "percent_delta": float(percent_delta),
            "observed_from": iso(first.observed_at),
            "observed_to": iso(last.observed_at),
        }
    )
    if len(today_rows) < 2:
        today["reason"] = "今天只有一次观测，无法计算日内增量"
        return {"cycle": cycle, "today": today}
    if cost_delta <= 0:
        today["reason"] = "今日已覆盖观测区间没有正向成本增量"
        return {"cycle": cycle, "today": today}
    if percent_delta < config.daily_estimate_min_percent_span:
        today["reason"] = (
            f"今日已覆盖观测区间仅跨过 {percent_delta:.2f}% 周限，"
            f"低于设置的 {config.daily_estimate_min_percent_span:.2f}%，"
            "整数百分比误差过大"
        )
        return {"cycle": cycle, "today": today}

    estimate = cost_delta * Decimal("100") / percent_delta
    minimum = cost_delta * Decimal("100") / (percent_delta + Decimal("1"))
    maximum = (
        cost_delta * Decimal("100") / (percent_delta - Decimal("1"))
        if percent_delta > 1
        else None
    )
    today.update(
        {
            "estimate_usd": money(estimate),
            "minimum_usd": money(minimum),
            "maximum_usd": money(maximum) if maximum is not None else None,
            "sufficient": True,
            "reason": "按今日已覆盖观测区间的成本增量与周限增量折算",
        }
    )
    return {"cycle": cycle, "today": today}


def capacity_series(
    *,
    config: AppSettings,
    account: MonitoredAccount,
    location: ZoneInfo,
    now: datetime,
    capacity_days: int,
    capacity_period: str,
    cost_breakdowns: FastCorrectionBreakdownPresenter,
) -> list[dict]:
    """按每日收盘累计端点比值生成历史，不读取时变归属模型。"""

    capacity_start = datetime.combine(
        (now - timedelta(days=capacity_days)).astimezone(location).date(),
        time.min,
        tzinfo=location,
    )
    observation_rows = list(
        Observation.objects.filter(
            account_id=account.external_account_id,
            excluded_at__isnull=True,
            observed_at__gte=capacity_start,
        ).order_by("observed_at", "id")
    )
    daily: dict[str, dict] = {}
    for observation in observation_rows:
        used_percent = observation.interval_used_percent
        if used_percent <= 0:
            continue
        total = (
            observation.selected_total_cost
            * Decimal("100")
            / used_percent
        )
        segment_key = (
            observation.account_id,
            observation.attribution_started_at,
        )
        period = observation.observed_at.astimezone(location).date().isoformat()
        row = daily.setdefault(
            period,
            {
                "period": period,
                "weekly_total_usd": total,
                "minimum_usd": total,
                "maximum_usd": total,
                "sample_count": 0,
                "_closing_observation": observation,
                "_daily_segment_key": segment_key,
                "_daily_first_observation": observation,
                "_daily_last_observation": observation,
                "_daily_sample_count": 0,
            },
        )
        row["weekly_total_usd"] = total
        row["minimum_usd"] = min(row["minimum_usd"], total)
        row["maximum_usd"] = max(row["maximum_usd"], total)
        row["sample_count"] += 1
        row["_closing_observation"] = observation
        if row["_daily_segment_key"] != segment_key:
            row["_daily_segment_key"] = segment_key
            row["_daily_first_observation"] = observation
            row["_daily_sample_count"] = 0
        row["_daily_last_observation"] = observation
        row["_daily_sample_count"] += 1
    for row in daily.values():
        row["_daily_basis"] = daily_closing_basis(
            row["_daily_first_observation"],
            row["_daily_last_observation"],
            row["_daily_sample_count"],
            config,
            cost_breakdowns,
        )
    if capacity_period == "day":
        return [
            {
                "period": row["period"],
                "weekly_total_usd": money(row["weekly_total_usd"]),
                "minimum_usd": money(row["minimum_usd"]),
                "maximum_usd": money(row["maximum_usd"]),
                "sample_count": row["sample_count"],
                "basis": closing_basis(
                    row["_closing_observation"],
                    [],
                    config,
                    cost_breakdowns,
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

    monthly: dict[str, list[dict]] = defaultdict(list)
    for row in daily.values():
        monthly[row["period"][:7]].append(row)
    result = []
    for period, rows in monthly.items():
        closing_values = [row["weekly_total_usd"] for row in rows]
        daily_estimates = [
            row["_daily_basis"]["estimate_usd"]
            for row in rows
            if row["_daily_basis"] is not None
        ]
        result.append(
            {
                "period": period,
                "weekly_total_usd": money(
                    sum(closing_values, Decimal("0"))
                    / Decimal(len(closing_values))
                ),
                "minimum_usd": money(
                    min(row["minimum_usd"] for row in rows)
                ),
                "maximum_usd": money(
                    max(row["maximum_usd"] for row in rows)
                ),
                "sample_count": len(rows),
                "basis": None,
                "daily_total_usd": (
                    money(
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
    return result
