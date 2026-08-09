"""手动触发监测的动作式 API。"""

from django.utils import timezone

from .base import AdminAPIView, error, ok
from .presenters import iso
from ..engine import run_monitor
from ..models import AppSettings
from ..integrations.sub2api import Sub2APIError


class RunMonitorView(AdminAPIView):
    def get(self, _request):
        """返回全局后台轮询器状态；一次轮询会探测所有启用参与者。"""
        config = AppSettings.load()
        now = timezone.now()
        return ok(
            {
                "monitoring_enabled": config.monitoring_enabled,
                "interval_seconds": max(2, config.local_poll_minutes) * 60,
                "next_local_check_at": (
                    iso(config.next_local_check_at)
                    if config.monitoring_enabled
                    else None
                ),
                "run_in_progress": bool(
                    config.run_lease_until and config.run_lease_until > now
                ),
                "server_time": iso(now),
            }
        )

    def post(self, _request):
        try:
            result = run_monitor(force_upstream=True, source="manual")
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        except Exception as exc:
            # 该接口需要把后台采集错误呈现给唯一管理员；运行进程本身仍保留日志。
            return error(f"采集失败：{exc}", 500)
        return ok(result)
