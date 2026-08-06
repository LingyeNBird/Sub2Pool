"""系统业务设置与连接测试 API。"""

from django.utils import timezone

from rest_framework.serializers import ValidationError
from .base import AdminAPIView, error, ok
from ..models import AppSettings
from ..notifications import send_notification
from ..serializers import AppSettingsSerializer
from ..sub2api import Sub2APIClient, Sub2APIError


class SettingsView(AdminAPIView):
    def get(self, _request):
        return ok(AppSettingsSerializer(AppSettings.load()).data)

    def patch(self, request):
        config = AppSettings.load()
        serializer = AppSettingsSerializer(
            config,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error("设置字段格式无效", details=serializer.errors)
        try:
            config = serializer.save()
        except ValidationError as exc:
            return error("设置校验失败", details=exc.detail)
        return ok(AppSettingsSerializer(config).data)


class TestSub2APIView(AdminAPIView):
    def post(self, _request):
        config = AppSettings.load()
        try:
            with Sub2APIClient(config) as client:
                result = client.test_connection(
                    config.openai_account_id,
                    config.quota_query_mode,
                )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        return ok(result)


class TestEmailView(AdminAPIView):
    def post(self, _request):
        config = AppSettings.load()
        event = send_notification(
            config=config,
            event_type="test",
            dedupe_key=f"test:{timezone.now().timestamp()}",
            subject="[拼车额度] 邮件配置测试",
            body="这是一封测试邮件。收到它说明当前选择的邮件服务配置正常。",
            severity="info",
            ignore_cooldown=True,
        )
        if event is None or event.status != "sent":
            return error(event.error if event else "邮件未发送", 502)
        return ok({"event_id": event.id})
