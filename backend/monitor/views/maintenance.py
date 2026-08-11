"""管理员历史数据全量重建 API。"""

from ..historical_rebuild import (
    HistoricalRebuildError,
    inspect_historical_rebuild,
    rebuild_historical_data,
)
from ..integrations.sub2api import Sub2APIError
from ..models import AppSettings
from .base import AdminAPIView, error, ok


def _maintenance_error(exc: Exception, *, conflict: bool = False):
    details = exc.details if isinstance(exc, HistoricalRebuildError) else None
    return error(str(exc), 409 if conflict else 502, details)


class HistoricalRebuildPreviewView(AdminAPIView):
    """只读检查请求日志可重建的全部历史事实。"""

    def post(self, _request):
        try:
            plan = inspect_historical_rebuild(AppSettings.load())
        except HistoricalRebuildError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(plan.public_data())


class HistoricalRebuildView(AdminAPIView):
    """重取全部可恢复事实，并从第一条百分比观测全量重放。"""

    def post(self, _request):
        try:
            result = rebuild_historical_data(AppSettings.load())
        except HistoricalRebuildError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(result)
