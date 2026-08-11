"""Administrator API for immutable history-maintenance plans."""
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime

from ..historical_rebuild import (
    MODE_AUDIT_REPLAY,
    REBUILD_MODES,
    HistoricalRebuildConflict,
    HistoricalRebuildError,
    apply_rebuild_plan,
    create_rebuild_plan,
    rebuild_plan_data,
    rollback_rebuild_plan,
)
from ..integrations.sub2api import Sub2APIError
from ..models import AppSettings, HistoricalRebuildRun
from .base import AdminAPIView, error, ok


def _maintenance_error(exc: Exception):
    details = exc.details if isinstance(exc, HistoricalRebuildError) else None
    if isinstance(exc, HistoricalRebuildConflict):
        return error(str(exc), 409, details)
    if isinstance(exc, HistoricalRebuildError):
        return error(str(exc), 400, details)
    return error(str(exc), 502)


def _optional_datetime(request, field: str):
    value = request.data.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise HistoricalRebuildError(f"{field} 必须为 ISO 时间字符串")
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise HistoricalRebuildError(f"{field} 必须包含时区")
    return parsed


class HistoricalRebuildPlanListView(AdminAPIView):
    """Create a persistent plan; the response is the later apply contract."""

    def post(self, request):
        mode = str(request.data.get("mode") or MODE_AUDIT_REPLAY)
        if mode not in REBUILD_MODES:
            return error("未知的历史维护模式", 400)
        try:
            plan = create_rebuild_plan(
                AppSettings.load(),
                mode,
                started_at=_optional_datetime(request, "started_at"),
                ended_at=_optional_datetime(request, "ended_at"),
            )
        except (HistoricalRebuildError, Sub2APIError, ValueError) as exc:
            return _maintenance_error(exc)
        return ok(rebuild_plan_data(plan), 201)


class HistoricalRebuildPlanDetailView(AdminAPIView):
    def get(self, _request, plan_id):
        plan = get_object_or_404(HistoricalRebuildRun, pk=plan_id)
        return ok(rebuild_plan_data(plan))


class HistoricalRebuildApplyView(AdminAPIView):
    """Apply the named digest without any upstream access."""

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


class HistoricalRebuildRollbackView(AdminAPIView):
    """Rollback only the latest applied run for the account."""

    def post(self, _request, plan_id):
        try:
            plan = rollback_rebuild_plan(plan_id)
        except HistoricalRebuildError as exc:
            return _maintenance_error(exc)
        except HistoricalRebuildRun.DoesNotExist:
            return error("历史维护计划不存在", 404)
        return ok(rebuild_plan_data(plan))
