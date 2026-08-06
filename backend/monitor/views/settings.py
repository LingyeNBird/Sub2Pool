"""系统业务设置与连接测试 API。"""

from django.utils import timezone

from rest_framework.serializers import ValidationError
from .base import AdminAPIView, error, ok
from ..models import AppSettings
from ..notifications import send_notification
from ..serializers import AppSettingsSerializer, Sub2APIConnectionSerializer
from ..sub2api import Sub2APIClient, Sub2APIError

def _temporary_sub2api_client(
    config: AppSettings,
    values: dict,
) -> Sub2APIClient:
    """用表单当前值创建客户端；未填写的密钥仍可回退到数据库中的已保存密钥。"""
    return Sub2APIClient(
        config,
        base_url=values.get("sub2api_base_url", config.sub2api_base_url),
        admin_token=values.get("sub2api_admin_token") or None,
        request_timeout_seconds=values.get(
            "request_timeout_seconds",
            config.request_timeout_seconds,
        ),
        verify_tls=values.get("verify_tls", config.verify_tls),
    )


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


class OpenAIAccountListView(AdminAPIView):
    def post(self, request):
        serializer = Sub2APIConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return error("连接参数格式无效", details=serializer.errors)
        config = AppSettings.load()
        try:
            with _temporary_sub2api_client(
                config,
                serializer.validated_data,
            ) as client:
                accounts = client.list_openai_accounts()
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        return ok(accounts)


class TestSub2APIView(AdminAPIView):
    def post(self, request):
        serializer = Sub2APIConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return error("连接参数格式无效", details=serializer.errors)
        config = AppSettings.load()
        values = serializer.validated_data
        try:
            with _temporary_sub2api_client(config, values) as client:
                result = client.test_connection(
                    values.get("openai_account_id", config.openai_account_id),
                    values.get("quota_query_mode", config.quota_query_mode),
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
