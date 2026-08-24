"""Direct, read-only Sub2API account status endpoint."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from .base import PageAccessAPIView, ok
from ..access import visible_accounts_for
from ..api_auth import APIKeyAuthentication
from ..integrations.sub2api import Sub2APIClient, Sub2APIError, WeeklyWindow
from ..models import AppSettings, MonitoredAccount, Observation, PagePermission


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
        "external_account_id": account.external_account_id,
        "name": account.name,
        "enabled": account.enabled,
        "quota_query_mode": account.quota_query_mode,
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


class AccountStatusView(PageAccessAPIView):
    """Fetch each upstream account visible to the current principal."""

    required_page_permissions = (PagePermission.ACCOUNT_STATUS,)

    def get(self, request):
        config = AppSettings.load()
        accounts = list(
            visible_accounts_for(
                request.user,
                MonitoredAccount.objects.order_by("name", "external_account_id"),
            )
        )
        rows = [_base_account_row(account) for account in accounts]
        sampled_at = timezone.now()
        correction_totals = _fast_correction_totals(
            [account.external_account_id for account in accounts],
            cost_basis=config.cost_basis,
            observed_after=sampled_at - timedelta(days=STATS_DAYS),
            observed_before=sampled_at,
        )
        data = {
            "configured": bool(config.sub2api_admin_token_encrypted and accounts),
            "sampled_at": sampled_at.isoformat(),
            "stats_days": STATS_DAYS,
            "connection_error": None,
            "accounts": rows,
        }
        if not accounts:
            return ok(data)
        if not config.sub2api_admin_token_encrypted:
            data["connection_error"] = "尚未配置 Sub2API Admin Token"
            return ok(data)

        try:
            client = Sub2APIClient(config)
        except Sub2APIError as exc:
            data["connection_error"] = str(exc)
            return ok(data)

        with client:
            for account, row in zip(accounts, rows, strict=True):
                upstream_id = account.external_account_id
                try:
                    row["runtime"] = client.account_runtime_status(upstream_id)
                except Sub2APIError as exc:
                    row["warnings"].append(f"运行状态：{exc}")

                usage_problem: Sub2APIError | None = None
                try:
                    usage = client.account_usage_status(
                        upstream_id,
                        source="passive",
                    )
                    row["usage"] = usage
                except Sub2APIError as exc:
                    usage_problem = exc

                if not row["usage"] or not row["usage"].get("seven_day"):
                    try:
                        window = client.query_weekly_window(upstream_id, "passive")
                        row["usage"] = _fallback_usage(window, row["usage"])
                    except Sub2APIError as exc:
                        messages = [str(item) for item in (usage_problem, exc) if item]
                        row["warnings"].append(
                            f"额度状态：{'；'.join(dict.fromkeys(messages))}"
                        )
                elif row["usage"].get("error"):
                    row["warnings"].append(f"额度状态：{row['usage']['error']}")

                try:
                    stats = client.account_usage_stats(
                        upstream_id,
                        days=STATS_DAYS,
                    )
                    correction = correction_totals.get(upstream_id, Decimal("0"))
                    stats["fast_correction_usd"] = float(correction)
                    account_cost = stats.get("account_cost_usd")
                    stats["account_cost_with_fast_correction_usd"] = (
                        float(Decimal(str(account_cost)) + correction)
                        if account_cost is not None
                        else None
                    )
                    row["stats"] = stats
                except Sub2APIError as exc:
                    row["warnings"].append(f"{STATS_DAYS} 天统计：{exc}")

        return ok(data)


class ReadOnlyAccountStatusView(AccountStatusView):
    """External API-key view exposing live upstream account status."""

    authentication_classes = [APIKeyAuthentication]
    http_method_names = ["get", "head", "options"]
