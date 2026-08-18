"""Administrator API for immutable local history-maintenance plans."""
from django.shortcuts import get_object_or_404

from ..historical_rebuild import (
    HistoricalRebuildConflict,
    HistoricalRebuildError,
    apply_rebuild_plan,
    create_rebuild_plan,
    rebuild_plan_data,
)
from ..models import AppSettings, HistoricalRebuildRun, MonitoredAccount
from .base import AdminAPIView, error, ok


def _maintenance_error(exc: Exception):
    details = exc.details if isinstance(exc, HistoricalRebuildError) else None
    if isinstance(exc, HistoricalRebuildConflict):
        return error(str(exc), 409, details)
    return error(str(exc), 400, details)


class HistoricalRebuildPlanListView(AdminAPIView):
    """Create a persistent local audit plan for later digest-bound apply."""

    def post(self, request):
        if set(request.data) != {"account_id"}:
            return error("本地历史维护计划必须且只能指定监控账号 account_id", 400)
        try:
            account_id = int(request.data.get("account_id"))
        except (TypeError, ValueError):
            return error("监控账号参数无效", 400)
        account = get_object_or_404(MonitoredAccount, pk=account_id)
        try:
            plan = create_rebuild_plan(AppSettings.load(), account)
        except (HistoricalRebuildError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(rebuild_plan_data(plan), 201)


class HistoricalRebuildPlanDetailView(AdminAPIView):
    def get(self, _request, plan_id):
        plan = get_object_or_404(HistoricalRebuildRun, pk=plan_id)
        return ok(rebuild_plan_data(plan))


class HistoricalRebuildApplyView(AdminAPIView):
    """Apply the named digest without upstream access."""

    def post(self, request, plan_id):
        digest = request.data.get("digest")
        if not isinstance(digest, str) or not digest:
            return error("apply 必须提交计划 digest", 400)
        try:
            plan = apply_rebuild_plan(plan_id, digest)
        except HistoricalRebuildError as exc:
            return _maintenance_error(exc)
        except HistoricalRebuildRun.DoesNotExist:
            return error("历史维护计划不存在", 404)
        return ok(rebuild_plan_data(plan))
