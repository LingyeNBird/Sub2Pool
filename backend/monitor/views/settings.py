"""系统业务设置与连接测试 API。"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from ..api_auth import ReadOnlyAPIKeyAuthentication, generate_readonly_api_key

from rest_framework.serializers import ValidationError
from .base import AdminAPIView, PageAccessAPIView, error, ok
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..history_state import LeaseLostError, fenced_fact_write
from ..models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    Observation,
    PagePermission,
    Participant,
)
from ..notifications import send_notification
from ..replay import rebuild_account
from ..serializers import (
    AppSettingsSerializer,
    MonitoredAccountSerializer,
    Sub2APIConnectionSerializer,
)

DERIVED_RESULT_SETTINGS = frozenset(
    {
        "cost_basis",
        "initial_usd_per_percent",
        "safety_factor",
        "limit_warning_usd",
        "recommendation_change_usd",
    }
)
PLAN_RELEVANT_SETTINGS = frozenset(
    {
        "sub2api_base_url",
        "timezone",
        "cost_basis",
        "weekly_quota_model",
        "fast_correction_enabled",
        "initial_usd_per_percent",
        "safety_factor",
        "daily_estimate_min_percent_span",
    }
)

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


class SettingsView(PageAccessAPIView):
    required_page_permissions = (PagePermission.SETTINGS,)

    def get(self, _request):
        return ok(AppSettingsSerializer(AppSettings.load()).data)

    def patch(self, request):
        config = AppSettings.load()
        account_ids = set(
            MonitoredAccount.objects.values_list(
                "external_account_id",
                flat=True,
            )
        )
        serializer = AppSettingsSerializer(
            config,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error("设置字段格式无效", details=serializer.errors)
        changed_derived_settings = {
            field
            for field in DERIVED_RESULT_SETTINGS
            if field in serializer.validated_data
            and getattr(config, field) != serializer.validated_data[field]
        }
        changed_plan_settings = {
            field
            for field in PLAN_RELEVANT_SETTINGS
            if field in serializer.validated_data
            and getattr(config, field) != serializer.validated_data[field]
        }
        derived_account_ids = (
            set(
                Observation.objects.order_by()
                .values_list("account_id", flat=True)
                .distinct()
            )
            if changed_derived_settings
            else set()
        )
        plan_account_ids = account_ids if changed_plan_settings else set()
        affected_account_ids = derived_account_ids | plan_account_ids
        try:
            with fenced_fact_write(
                affected_account_ids,
                ttl=timedelta(minutes=30),
            ) as guards:
                locked_config = AppSettings.objects.select_for_update().get(
                    pk=config.pk
                )
                if locked_config.updated_at != config.updated_at:
                    raise LeaseLostError(
                        "系统设置已被其他请求修改，请刷新后重试"
                    )
                serializer.instance = locked_config
                config = serializer.save()
                for account_id in derived_account_ids:
                    rebuild_account(
                        account_id,
                        config,
                        guard=guards[account_id],
                    )
        except ValidationError as exc:
            return error("设置校验失败", details=exc.detail)
        except ValueError as exc:
            return error(
                "设置未保存：历史派生结果重建失败",
                409,
                {"replay": [str(exc)]},
            )
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
                selected_account = MonitoredAccount.objects.filter(
                    pk=values.get("openai_account_id")
                ).first()
                external_account_id = (
                    selected_account.external_account_id
                    if selected_account is not None
                    else values.get("openai_account_id")
                )
                result = client.test_connection(
                    external_account_id,
                    values.get(
                        "quota_query_mode",
                        selected_account.quota_query_mode
                        if selected_account is not None
                        else "passive",
                    ),
                )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), 502)
        return ok(result)


class MonitoredAccountListView(PageAccessAPIView):
    required_page_permissions = (
        PagePermission.OBSERVATIONS,
        PagePermission.PARTICLE_FILTER,
        PagePermission.SETTINGS,
        PagePermission.STATISTICS,
    )

    def get(self, _request):
        accounts = MonitoredAccount.objects.order_by("name", "external_account_id")
        return ok(MonitoredAccountSerializer(accounts, many=True).data)

    def post(self, request):
        serializer = MonitoredAccountSerializer(data=request.data)
        if not serializer.is_valid():
            return error("监控账号校验失败", details=serializer.errors)
        external_account_id = serializer.validated_data["external_account_id"]
        with fenced_fact_write([external_account_id]):
            account = serializer.save()
            AccountParticipant.objects.bulk_create(
                [
                    AccountParticipant(account=account, participant=participant)
                    for participant in Participant.objects.order_by("id")
                ]
            )
        return ok(MonitoredAccountSerializer(account).data, 201)


class ReadOnlyMonitoredAccountListView(MonitoredAccountListView):
    """External API-key view exposing configured monitored accounts."""

    authentication_classes = [ReadOnlyAPIKeyAuthentication]
    http_method_names = ["get", "head", "options"]


class MonitoredAccountDetailView(AdminAPIView):
    def put(self, request, account_id: int):
        try:
            account = MonitoredAccount.objects.get(pk=account_id)
        except MonitoredAccount.DoesNotExist:
            return error("监控账号不存在", 404)
        serializer = MonitoredAccountSerializer(
            account,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error("监控账号校验失败", details=serializer.errors)
        with fenced_fact_write([account.external_account_id]):
            account = serializer.save()
        return ok(MonitoredAccountSerializer(account).data)

    def delete(self, _request, account_id: int):
        try:
            account = MonitoredAccount.objects.get(pk=account_id)
        except MonitoredAccount.DoesNotExist:
            return error("监控账号不存在", 404)
        if Observation.objects.filter(
            account_id=account.external_account_id
        ).exists():
            return error("该账号已有历史事实，不能删除；请改为停用", 409)
        with fenced_fact_write([account.external_account_id]):
            account.delete()
        return ok({"deleted": True})

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


class ReadOnlyAPIKeyView(AdminAPIView):
    """Generate, rotate, or revoke the permanent external read-only API key."""

    def post(self, _request):
        config = AppSettings.load()
        api_key, digest, hint = generate_readonly_api_key()
        config.readonly_api_key_hash = digest
        config.readonly_api_key_hint = hint
        config.readonly_api_key_created_at = timezone.now()
        config.save(
            update_fields=[
                "readonly_api_key_hash",
                "readonly_api_key_hint",
                "readonly_api_key_created_at",
                "updated_at",
            ]
        )
        return ok(
            {
                "api_key": api_key,
                "hint": hint,
                "created_at": config.readonly_api_key_created_at.isoformat(),
            }
        )

    def delete(self, _request):
        config = AppSettings.load()
        config.readonly_api_key_hash = ""
        config.readonly_api_key_hint = ""
        config.readonly_api_key_created_at = None
        config.save(
            update_fields=[
                "readonly_api_key_hash",
                "readonly_api_key_hint",
                "readonly_api_key_created_at",
                "updated_at",
            ]
        )
        return ok({"revoked": True})
