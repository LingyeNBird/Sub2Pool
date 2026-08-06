"""手动触发监测的动作式 API。"""

from .base import AdminAPIView, error, ok
from ..engine import run_monitor
from ..sub2api import Sub2APIError


class RunMonitorView(AdminAPIView):
    def post(self, _request):
        try:
            result = run_monitor(force_upstream=True, source="manual")
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        except Exception as exc:
            # 该接口需要把后台采集错误呈现给唯一管理员；运行进程本身仍保留日志。
            return error(f"采集失败：{exc}", 500)
        return ok(result)
