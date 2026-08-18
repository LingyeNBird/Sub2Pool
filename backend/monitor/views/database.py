"""管理员数据库完整导入导出 API。"""

from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from ..history_state import LeaseBusyError, LeaseGuard
from .base import AdminAPIView, error, ok
from ..database_transfer import (
    DatabaseTransferError,
    export_database_bytes,
    import_database,
    stage_database_import,
)
from ..models import HistoryMaintenanceState, MonitoredAccount, Observation


class DatabaseExportView(AdminAPIView):
    def get(self, _request):
        try:
            payload = export_database_bytes()
        except DatabaseTransferError as exc:
            return error(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)

        filename = timezone.now().strftime("pinche-backup-%Y%m%d-%H%M%S.sqlite3")
        response = HttpResponse(payload, content_type="application/vnd.sqlite3")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class DatabaseImportView(AdminAPIView):
    def post(self, request):
        uploaded = request.FILES.get("database")
        if uploaded is None:
            return error("请选择要导入的 SQLite 备份文件")

        try:
            staged = stage_database_import(uploaded, uploaded.size)
        except DatabaseTransferError as exc:
            return error(str(exc))

        with staged:
            try:
                guard = LeaseGuard.acquire(
                    0,
                    ttl=timedelta(hours=1),
                )
            except LeaseBusyError:
                return error(
                    "后台采集或历史维护正在运行，请稍后再导入数据库",
                    status.HTTP_409_CONFLICT,
                )

            try:
                guard.renew()
                recovery_name = import_database(
                    staged,
                    guard=guard,
                )
                with transaction.atomic():
                    global_state = (
                        HistoryMaintenanceState.objects.select_for_update().get(
                            account_id=0
                        )
                    )
                    guard.assert_owned(global_state)
                    account_ids = set(
                        HistoryMaintenanceState.objects.exclude(account_id=0)
                        .values_list("account_id", flat=True)
                    )
                    account_ids.update(
                        Observation.objects.order_by()
                        .values_list("account_id", flat=True)
                        .distinct()
                    )
                    account_ids.update(
                        MonitoredAccount.objects.values_list(
                            "external_account_id",
                            flat=True,
                        )
                    )
                    for account_id in sorted(account_ids):
                        state, _created = (
                            HistoryMaintenanceState.objects.get_or_create(
                                account_id=account_id
                            )
                        )
                        state = (
                            HistoryMaintenanceState.objects.select_for_update().get(
                                pk=state.pk
                            )
                        )
                        state.fact_revision += 1
                        state.save(
                            update_fields=["fact_revision", "updated_at"]
                        )
                    guard.assert_owned(global_state)
            except DatabaseTransferError as exc:
                return error(str(exc))
            finally:
                guard.release()

        response = ok(
            {
                "imported": True,
                "recovery_backup": recovery_name,
            }
        )
        # 数据库中包含管理员和 JWT 黑名单；清除旧刷新 Cookie，要求用恢复后的账号重新登录。
        response.delete_cookie(
            settings.JWT_REFRESH_COOKIE_NAME,
            path=settings.JWT_REFRESH_COOKIE_PATH,
            samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
        )
        return response
