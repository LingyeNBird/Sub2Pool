"""管理员数据库完整导入导出 API。"""

from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status

from .base import AdminAPIView, error, ok
from ..database_transfer import (
    DatabaseTransferError,
    export_database_bytes,
    import_database,
)
from ..models import AppSettings


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

        # 单例设置可能尚未创建，例如新部署后第一次操作就是导入备份。
        AppSettings.load()
        now = timezone.now()
        lease_until = now + timedelta(minutes=15)
        acquired = (
            AppSettings.objects.filter(
                pk=1,
                run_lease_until__isnull=True,
            ).update(run_lease_until=lease_until)
            == 1
        ) or (
            AppSettings.objects.filter(
                pk=1,
                run_lease_until__lt=now,
            ).update(run_lease_until=lease_until)
            == 1
        )
        if not acquired:
            return error(
                "后台采集正在运行，请稍后再导入数据库",
                status.HTTP_409_CONFLICT,
            )

        imported = False
        try:
            recovery_name = import_database(uploaded, uploaded.size)
            imported = True
        except DatabaseTransferError as exc:
            return error(str(exc))
        finally:
            # 成功导入后数据库已被替换，直接清除备份中可能残留的租约；失败时只释放自己的租约。
            if imported:
                AppSettings.objects.filter(pk=1).update(run_lease_until=None)
            else:
                AppSettings.objects.filter(
                    pk=1,
                    run_lease_until=lease_until,
                ).update(run_lease_until=None)

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
