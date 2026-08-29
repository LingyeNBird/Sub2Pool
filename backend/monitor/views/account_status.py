"""Direct, read-only Sub2API account status endpoint."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from .base import PageAccessAPIView, ok
from ..access import visible_accounts_for
from ..api_auth import APIKeyAuthentication
from ..cpa.usage import cpa_events_cost
from ..integrations.cpa import CPAClient, CPAError
from ..integrations.sub2api import Sub2APIClient, Sub2APIError, WeeklyWindow
from ..models import (
    AppSettings,
    CPAUsageEvent,
    MonitoredAccount,
    Observation,
    PagePermission,
)
from ..particle_trajectory import cycle_usage_history


STATS_DAYS = 30


def _fast_correction_totals(
    account_ids: list[int],
    *,
    cost_basis: str,
    observed_after: datetime,
    observed_before: datetime,
) -> dict[int, Decimal]:
    field = (
        "fast_correction_actual_cost"
        if cost_basis == "actual"
        else "fast_correction_standard_cost"
    )
    totals = (
        Observation.objects.filter(
            account_id__in=account_ids,
            observed_at__range=(observed_after, observed_before),
        )
        .values("account_id")
        .annotate(total=Sum(field))
    )
    return {
        int(row["account_id"]): row["total"] or Decimal("0")
        for row in totals
    }


def _base_account_row(account: MonitoredAccount) -> dict[str, Any]:
    return {
        "id": account.id,
        "provider": account.provider,
        "source_account_id": account.source_account_id,
        "external_account_id": account.external_account_id,
        "name": account.name,
        "enabled": account.enabled,
        "quota_query_mode": account.quota_query_mode,
        "cycles": cycle_usage_history(account.fact_key),
        "runtime": None,
        "usage": None,
        "stats": None,
        "warnings": [],
    }


def _fallback_usage(
    window: WeeklyWindow,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not window.used_percent.is_finite():
        raise Sub2APIError("Sub2API 被动快照包含无效的已用百分比")
    try:
        reset_at = datetime.fromtimestamp(window.reset_at, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError) as exc:
        raise Sub2APIError("Sub2API 被动快照包含无效的重置时间") from exc
    return {
        "source": "passive_snapshot",
        "updated_at": window.sampled_at,
        "five_hour": existing.get("five_hour") if existing else None,
        "seven_day": {
            "used_percent": float(window.used_percent),
            "reset_at": reset_at,
            "remaining_seconds": window.reset_after_seconds,
            "request_count": None,
            "token_count": None,
            "account_cost_usd": None,
            "standard_cost_usd": None,
            "user_cost_usd": None,
        },
        "needs_verify": existing.get("needs_verify") if existing else None,
        "is_banned": existing.get("is_banned") if existing else None,
        "needs_reauth": existing.get("needs_reauth") if existing else None,
        "error_code": existing.get("error_code") if existing else None,
        "error": None,
    }


def _cpa_event_totals(
    config: AppSettings,
    events,
) -> dict[str, Any]:
    totals = events.aggregate(
        request_count=Count("id"),
        token_count=Sum("total_tokens"),
        avg_duration_ms=Avg("latency_ms"),
    )
    account_cost, _unknown_count = cpa_events_cost(
        events.iterator(chunk_size=1000),
        config,
    )
    return {
        "request_count": int(totals["request_count"] or 0),
        "token_count": int(totals["token_count"] or 0),
        "account_cost_usd": float(account_cost),
        "avg_duration_ms": (
            float(totals["avg_duration_ms"])
            if totals["avg_duration_ms"] is not None
            else None
        ),
    }


def _cpa_status_rows(
    config: AppSettings,
    accounts: list[MonitoredAccount],
    rows_by_id: dict[int, dict[str, Any]],
    sampled_at: datetime,
) -> str | None:
    if not accounts:
        return None
    if not config.cpa_management_key_encrypted:
        for account in accounts:
            rows_by_id[account.id]["warnings"].append(
                "CPA：尚未配置 Management Key"
            )
        return "尚未配置 CPA Management Key"
    try:
        client = CPAClient(config)
    except CPAError as exc:
        return str(exc)
    with client:
        try:
            upstream_by_index = {
                item["auth_index"]: item for item in client.list_codex_accounts()
            }
        except CPAError as exc:
            return str(exc)
        for account in accounts:
            row = rows_by_id[account.id]
            upstream = upstream_by_index.get(account.cpa_auth_index or "")
            if upstream is None:
                row["warnings"].append("运行状态：CPA 中未找到该 Codex 账号")
            else:
                status = (
                    "disabled"
                    if upstream["disabled"]
                    else (
                        "error"
                        if upstream["unavailable"]
                        else upstream["status"] or "active"
                    )
                )
                row["runtime"] = {
                    "name": upstream["name"],
                    "account_type": upstream["plan_type"] or "Codex",
                    "status": status,
                    "schedulable": not upstream["disabled"]
                    and not upstream["unavailable"],
                    "current_concurrency": None,
                    "concurrency_limit": None,
                    "last_used_at": None,
                    "rate_limited_at": None,
                    "rate_limit_reset_at": None,
                    "overload_until": None,
                    "temp_unschedulable_until": None,
                    "temp_unschedulable_reason": None,
                    "error_message": upstream["status_message"] or None,
                }
            try:
                window = client.query_weekly_window(account.cpa_auth_index or "")
                reset_at = datetime.fromtimestamp(
                    window.reset_at,
                    tz=UTC,
                )
                cycle_start = reset_at - timedelta(seconds=window.window_seconds)
                cycle_events = CPAUsageEvent.objects.filter(
                    account=account,
                    occurred_at__gte=max(cycle_start, account.created_at),
                    occurred_at__lte=sampled_at,
                )
                totals = _cpa_event_totals(config, cycle_events)
                row["usage"] = {
                    "source": "cpa_direct",
                    "updated_at": window.sampled_at,
                    "five_hour": None,
                    "seven_day": {
                        "used_percent": float(window.used_percent),
                        "reset_at": reset_at.isoformat(),
                        "remaining_seconds": window.reset_after_seconds,
                        "request_count": totals["request_count"],
                        "token_count": totals["token_count"],
                        "account_cost_usd": totals["account_cost_usd"],
                        "standard_cost_usd": None,
                        "user_cost_usd": None,
                    },
                    "needs_verify": None,
                    "is_banned": None,
                    "needs_reauth": None,
                    "error_code": None,
                    "error": None,
                }
            except CPAError as exc:
                row["warnings"].append(f"额度状态：{exc}")

            started_at = sampled_at - timedelta(days=STATS_DAYS)
            events = CPAUsageEvent.objects.filter(
                account=account,
                occurred_at__gte=started_at,
                occurred_at__lte=sampled_at,
            )
            totals = _cpa_event_totals(config, events)
            actual_days = events.dates("occurred_at", "day").count()
            today_events = events.filter(occurred_at__date=sampled_at.date())
            today = _cpa_event_totals(config, today_events)
            divisor = max(1, actual_days)
            row["stats"] = {
                "days": STATS_DAYS,
                "actual_days_used": actual_days,
                "account_cost_usd": totals["account_cost_usd"],
                "fast_correction_usd": None,
                "account_cost_with_fast_correction_usd": None,
                "standard_cost_usd": None,
                "user_cost_usd": None,
                "request_count": totals["request_count"],
                "token_count": totals["token_count"],
                "avg_daily_cost_usd": totals["account_cost_usd"] / divisor,
                "avg_daily_request_count": totals["request_count"] / divisor,
                "avg_daily_token_count": totals["token_count"] / divisor,
                "avg_duration_ms": totals["avg_duration_ms"],
                "today": {
                    "date": sampled_at.date().isoformat(),
                    "account_cost_usd": today["account_cost_usd"],
                    "user_cost_usd": None,
                    "request_count": today["request_count"],
                    "token_count": today["token_count"],
                },
            }
    return None


class AccountStatusView(PageAccessAPIView):
    """Fetch each upstream account visible to the current principal."""

    required_page_permissions = (PagePermission.ACCOUNT_STATUS,)

    def get(self, request):
        config = AppSettings.load()
        accounts = list(
            visible_accounts_for(
                request.user,
                MonitoredAccount.objects.order_by(
                    "name",
                    "provider",
                    "external_account_id",
                    "cpa_auth_index",
                ),
            )
        )
        rows = [_base_account_row(account) for account in accounts]
        rows_by_id = {account.id: row for account, row in zip(accounts, rows)}
        sampled_at = timezone.now()
        sub2api_accounts = [
            account for account in accounts if account.provider == "sub2api"
        ]
        cpa_accounts = [
            account for account in accounts if account.provider == "cpa"
        ]
        correction_totals = _fast_correction_totals(
            [account.fact_key for account in sub2api_accounts],
            cost_basis=config.cost_basis,
            observed_after=sampled_at - timedelta(days=STATS_DAYS),
            observed_before=sampled_at,
        )
        errors: list[str] = []
        data = {
            "configured": bool(
                accounts
                and (
                    (sub2api_accounts and config.sub2api_admin_token_encrypted)
                    or (cpa_accounts and config.cpa_management_key_encrypted)
                )
            ),
            "sampled_at": sampled_at.isoformat(),
            "stats_days": STATS_DAYS,
            "connection_error": None,
            "accounts": rows,
        }
        if not accounts:
            return ok(data)

        if sub2api_accounts and not config.sub2api_admin_token_encrypted:
            errors.append("尚未配置 Sub2API Admin Token")
            for account in sub2api_accounts:
                rows_by_id[account.id]["warnings"].append(errors[-1])
        elif sub2api_accounts:
            try:
                client = Sub2APIClient(config)
            except Sub2APIError as exc:
                errors.append(str(exc))
            else:
                with client:
                    for account in sub2api_accounts:
                        row = rows_by_id[account.id]
                        upstream_id = account.external_account_id
                        try:
                            row["runtime"] = client.account_runtime_status(
                                upstream_id
                            )
                        except Sub2APIError as exc:
                            row["warnings"].append(f"运行状态：{exc}")

                        usage_problem: Sub2APIError | None = None
                        try:
                            row["usage"] = client.account_usage_status(
                                upstream_id,
                                source="passive",
                            )
                        except Sub2APIError as exc:
                            usage_problem = exc

                        if not row["usage"] or not row["usage"].get("seven_day"):
                            try:
                                window = client.query_weekly_window(
                                    upstream_id,
                                    "passive",
                                )
                                row["usage"] = _fallback_usage(
                                    window,
                                    row["usage"],
                                )
                            except Sub2APIError as exc:
                                messages = [
                                    str(item)
                                    for item in (usage_problem, exc)
                                    if item
                                ]
                                row["warnings"].append(
                                    "额度状态："
                                    + "；".join(dict.fromkeys(messages))
                                )
                        elif row["usage"].get("error"):
                            row["warnings"].append(
                                f"额度状态：{row['usage']['error']}"
                            )

                        try:
                            stats = client.account_usage_stats(
                                upstream_id,
                                days=STATS_DAYS,
                            )
                            correction = correction_totals.get(
                                account.fact_key,
                                Decimal("0"),
                            )
                            stats["fast_correction_usd"] = float(correction)
                            account_cost = stats.get("account_cost_usd")
                            stats[
                                "account_cost_with_fast_correction_usd"
                            ] = (
                                float(Decimal(str(account_cost)) + correction)
                                if account_cost is not None
                                else None
                            )
                            row["stats"] = stats
                        except Sub2APIError as exc:
                            row["warnings"].append(
                                f"{STATS_DAYS} 天统计：{exc}"
                            )

        cpa_error = _cpa_status_rows(
            config,
            cpa_accounts,
            rows_by_id,
            sampled_at,
        )
        if cpa_error:
            errors.append(cpa_error)
        data["connection_error"] = "；".join(dict.fromkeys(errors)) or None
        return ok(data)


class ReadOnlyAccountStatusView(AccountStatusView):
    """External API-key view exposing live upstream account status."""

    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]
