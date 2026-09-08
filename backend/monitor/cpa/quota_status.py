"""Read-only quota cards. Incomplete observations never become projections."""

from datetime import UTC, datetime, timedelta
from math import isfinite


from ..models import CPAUsageEvent, Observation
from .participants import coverage_data
from .request_summary import request_summary


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def reset_credits(body):
    credit = body.get("rate_limit_reset_credits") if isinstance(body, dict) else None
    value = number(credit.get("available_count")) if isinstance(credit, dict) else None
    return (
        int(value) if value is not None and value >= 0 and value.is_integer() else None
    )


def metrics(account, config, start, end):
    result = request_summary(
        CPAUsageEvent.objects.filter(
            account=account, occurred_at__gte=start, occurred_at__lt=end
        ),
        config,
    )
    count = result["request_count"]
    return {
        "request_count": count,
        "token_count": result["total_tokens"],
        "usage_usd": result["usage_usd"],
        "success_rate": (count - result["failed_count"]) / count * 100
        if count
        else None,
        "unpriced_request_count": result["unpriced_request_count"],
    }


def period(account, config, start, end):
    return {
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "metrics": metrics(account, config, start, end),
        "coverage_complete": coverage_data(account, start, end)["complete"],
    }


def quota_detail(account, config, now, body=None, weekly=None):
    body = body if isinstance(body, dict) else {}
    earliest = (
        CPAUsageEvent.objects.filter(account=account, occurred_at__lte=now)
        .order_by("occurred_at")
        .values_list("occurred_at", flat=True)
        .first()
    )
    start = max(now - timedelta(days=30), earliest or now - timedelta(days=30))
    result = {
        "totals": period(account, config, start, now),
        "windows": [],
        "reset": {
            "available_count": reset_credits(body),
            "can_reset": False,
            "history": [],
        },
    }
    groups = [
        ("standard", "", body.get("rate_limit"), True),
        ("review", "代码审查", body.get("code_review_rate_limit"), False),
    ]
    additional = body.get("additional_rate_limits")
    separate_model_limits = bool(additional) or bool(body.get("code_review_rate_limit"))
    if isinstance(additional, list):
        groups.extend(
            (
                f"additional-{index}",
                str(
                    item.get("limit_name") or item.get("metered_feature") or "附加限额"
                ),
                item.get("rate_limit"),
                False,
            )
            for index, item in enumerate(additional)
            if isinstance(item, dict)
        )
    if not isinstance(body.get("rate_limit"), dict) and weekly is not None:
        groups[0] = (
            "standard",
            "",
            {
                weekly.slot: {
                    "used_percent": float(weekly.used_percent),
                    "limit_window_seconds": weekly.window_seconds,
                    "reset_at": weekly.reset_at,
                }
            },
            True,
        )
    for group_id, label, limits, standard in groups:
        if not isinstance(limits, dict):
            continue
        for slot in ("primary_window", "secondary_window"):
            raw = limits.get(slot)
            if not isinstance(raw, dict):
                continue
            seconds = number(raw.get("limit_window_seconds"))
            used = number(raw.get("used_percent"))
            used = used if used is not None and 0 <= used <= 100 else None
            reset = number(raw.get("reset_at"))
            try:
                end = (
                    datetime.fromtimestamp(reset, UTC) if reset and reset > 0 else None
                )
            except (ValueError, OverflowError, OSError):
                end = None
            duration = (
                "周限额"
                if seconds == 604800
                else "5 小时限额"
                if seconds == 18000
                else f"{seconds / 3600:g} 小时限额"
                if seconds and seconds > 0
                else "额度窗口"
            )
            row = {
                "id": f"{group_id}-{slot}",
                "label": f"{label} {duration}".strip(),
                "used_percent": used,
                "reset_at": end.isoformat() if end else None,
                "updated_at": weekly.sampled_at if weekly else now.isoformat(),
                "source": "主动 API 查询",
                "boundary": "unknown",
                "current": None,
                "previous": None,
                "prediction": None,
                "notice": None,
            }
            window_start = (
                end - timedelta(seconds=seconds)
                if end and seconds and 0 < seconds <= 366 * 86400
                else None
            )
            if window_start is not None:
                row["boundary"] = "provider"
            if not standard:
                row["notice"] = "上游未提供完整模型范围，未计算该窗口用量。"
            elif window_start is None or end <= now or window_start > now:
                row["notice"] = "缺少有效窗口边界，等待新的额度观测。"
            else:
                row["current"] = period(account, config, window_start, now)
                row["current"]["ended_at"] = end.isoformat()
                current = row["current"]
                if seconds == 604800:
                    previous = (
                        Observation.objects.filter(
                            account_id=account.fact_key,
                            excluded_at__isnull=True,
                            observed_at__lt=window_start,
                        )
                        .order_by("-observed_at", "-id")
                        .first()
                    )
                    if previous:
                        prev_start = previous.upstream_resets_at - timedelta(
                            seconds=previous.window_seconds
                        )
                        prev_end = min(previous.upstream_resets_at, window_start)
                        if prev_start < prev_end:
                            row["previous"] = period(
                                account, config, prev_start, prev_end
                            )
                            if previous.upstream_resets_at > window_start:
                                row["previous"]["notice"] = (
                                    "上个窗口提前重置，仅统计已观测区间。"
                                )
                if separate_model_limits:
                    row["notice"] = "用量为该时间段的账号合计；附加限额的模型范围不明确，暂不预测。"
                elif not current["coverage_complete"]:
                    row["notice"] = "当前窗口采集不完整，仅展示已采集用量，暂不预测。"
                elif current["metrics"]["unpriced_request_count"]:
                    row["notice"] = "当前窗口存在未计价请求，暂不预测。"
                elif (
                    used is None
                    or used <= 0
                    or current["metrics"]["request_count"] == 0
                ):
                    row["notice"] = "用量观测不足，暂不预测。"
                else:
                    row["prediction"] = {
                        key: current["metrics"][key] * 100 / used
                        for key in ("request_count", "token_count", "usage_usd")
                    }
            result["windows"].append(row)
    # Put the weekly account quota first, then other standard/model limits.
    result["windows"].sort(key=lambda row: 0 if row["label"] == "周限额" else 1)
    return result
