"""管理员历史用量补全与全量粒子重建 API。"""

from ..cost_history import (
    CostHistoryRepairError,
    inspect_cost_history,
    repair_cost_history,
)
from ..integrations.sub2api import Sub2APIError
from ..models import AppSettings
from ..usage_history import (
    HistoricalUsageBackfillError,
    backfill_historical_user_usage,
    inspect_historical_user_usage,
    rebuild_all_particle_results,
)
from .base import AdminAPIView, error, ok


def _maintenance_error(exc: Exception, *, conflict: bool = False):
    details = (
        exc.details
        if isinstance(
            exc,
            (HistoricalUsageBackfillError, CostHistoryRepairError),
        )
        else None
    )
    return error(str(exc), 409 if conflict else 502, details)


class HistoricalUsagePreviewView(AdminAPIView):
    """只读检查历史请求日志能否补全旧版用户用量事实。"""

    def post(self, _request):
        try:
            plan = inspect_historical_user_usage(AppSettings.load())
        except HistoricalUsageBackfillError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(plan.public_data())


class HistoricalUsageBackfillView(AdminAPIView):
    """补全全部兼容历史事实，并在同一事务中全量重放。"""

    def post(self, _request):
        try:
            result = backfill_historical_user_usage(AppSettings.load())
        except HistoricalUsageBackfillError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(result)


class CostHistoryPreviewView(AdminAPIView):
    """只读检查历史请求日志可重建的成本区间。"""

    def post(self, _request):
        try:
            plan = inspect_cost_history(AppSettings.load())
        except CostHistoryRepairError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(plan.public_data())


class CostHistoryRepairView(AdminAPIView):
    """重取区间增量并全量重建，不覆盖原始累计快照。"""

    def post(self, _request):
        try:
            result = repair_cost_history(AppSettings.load())
        except CostHistoryRepairError as exc:
            return _maintenance_error(exc, conflict=True)
        except (Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(result)


class ParticleReplayAllView(AdminAPIView):
    """显式从第一条原始观测重建当前账号的全部派生结果。"""

    def post(self, _request):
        try:
            result = rebuild_all_particle_results(AppSettings.load())
        except HistoricalUsageBackfillError as exc:
            return _maintenance_error(exc, conflict=True)
        except ValueError as exc:
            return _maintenance_error(exc, conflict=True)
        return ok(result)
