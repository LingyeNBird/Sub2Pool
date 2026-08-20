"""手动触发监测的动作式 API。"""

from django.utils import timezone

from .base import PageAccessAPIView, error, ok
from ..reporting import iso
from ..engine import run_monitor
from ..models import (
    AppSettings,
    HistoryMaintenanceState,
    MonitoredAccount,
    PagePermission,
)
from ..integrations.sub2api import Sub2APIError


class RunMonitorView(PageAccessAPIView):
    required_page_permissions = (PagePermission.OBSERVATIONS,)

    def get(self, _request):
        """Return global scheduler state and each account's independent lease."""
        config = AppSettings.load()
        now = timezone.now()
        active_leases = set(
            HistoryMaintenanceState.objects.filter(
                lease_expires_at__gt=now,
            ).values_list("account_id", flat=True)
        )
        accounts = [
            {
                "id": account.id,
                "external_account_id": account.external_account_id,
                "name": account.name,
                "enabled": account.enabled,
                "next_local_check_at": (
                    iso(account.next_local_check_at)
                    if config.monitoring_enabled and account.enabled
                    else None
                ),
                "run_in_progress": (
                    account.external_account_id in active_leases
                ),
            }
            for account in MonitoredAccount.objects.order_by(
                "name",
                "external_account_id",
            )
        ]
        return ok(
            {
                "monitoring_enabled": config.monitoring_enabled,
                "interval_seconds": max(2, config.local_poll_minutes) * 60,
                "next_local_check_at": (
                    iso(config.next_local_check_at)
                    if config.monitoring_enabled
                    else None
                ),
                "run_in_progress": any(
                    item["run_in_progress"] for item in accounts
                ),
                "accounts": accounts,
                "server_time": iso(now),
            }
        )

    def post(self, request):
        raw_account_id = request.data.get("account_id")
        try:
            account_id = int(raw_account_id) if raw_account_id else None
            result = run_monitor(
                account_id=account_id,
                force_upstream=True,
                source="manual",
            )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        except Exception as exc:
            # 该接口需要把后台采集错误呈现给唯一管理员；运行进程本身仍保留日志。
            return error(f"采集失败：{exc}", 500)
        return ok(result)
