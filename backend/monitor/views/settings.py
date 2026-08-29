"""系统业务设置与连接测试 API。"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.serializers import ValidationError
from rest_framework.permissions import IsAuthenticated

from ..access import HasPageAccess, visible_accounts_for
from ..api_auth import APIKeyAuthentication, generate_api_key
from ..cpa.collector_state import get_collector_status
from ..cpa.usage import refresh_cpa_history
from ..history_state import LeaseLostError, fenced_fact_write
from ..integrations.cpa import CPAClient, CPAError, CPAUsageSubscriber
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    Observation,
    PagePermission,
    Participant,
    SystemUserAPIKey,
)
from ..notifications import send_notification
from ..replay import rebuild_account
from ..serializers import (
    AppSettingsSerializer,
    CPAConnectionSerializer,
    MonitoredAccountSerializer,
    Sub2APIConnectionSerializer,
)
from .base import AdminAPIView, AuthenticatedAPIView, PageAccessAPIView, error, ok

DERIVED_RESULT_SETTINGS = frozenset(
    {
        "cost_basis",
        "initial_usd_per_percent",
        "safety_factor",
        "limit_warning_usd",
        "recommendation_change_usd",
    }
)
CPA_PRICING_SETTINGS = frozenset(
    {
        "cpa_fast_multiplier",
        "cpa_double_billing_enabled",
        "cpa_double_billing_threshold_tokens",
        "cpa_double_billing_multiplier",
        "cpa_model_pricing",
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

def _temporary_cpa_client(
    config: AppSettings,
    values: dict,
) -> CPAClient:
    return CPAClient(
        config,
        base_url=values.get("cpa_base_url", config.cpa_base_url),
        management_key=values.get("cpa_management_key") or None,
        request_timeout_seconds=values.get(
            "request_timeout_seconds",
            config.request_timeout_seconds,
        ),
        verify_tls=values.get("verify_tls", config.verify_tls),
    )

def _temporary_cpa_subscriber(
    config: AppSettings,
    values: dict,
) -> CPAUsageSubscriber:
    return CPAUsageSubscriber(
        config,
        base_url=values.get("cpa_base_url", config.cpa_base_url),
        management_key=values.get("cpa_management_key") or None,
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
        account_ids = {account.fact_key for account in MonitoredAccount.objects.all()}
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
        changed_cpa_pricing = {
            field
            for field in CPA_PRICING_SETTINGS
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
        cpa_account_ids = (
            {
                account.fact_key
                for account in MonitoredAccount.objects.filter(provider="cpa")
            }
            if changed_cpa_pricing
            else set()
        )
        affected_account_ids = (
            derived_account_ids | plan_account_ids | cpa_account_ids
        )
        try:
            with fenced_fact_write(
                affected_account_ids,
                ttl=timedelta(minutes=30),
            ) as guards:
                locked_config = AppSettings.objects.select_for_update().get(
                    pk=config.pk
                )
                if locked_config.updated_at != config.updated_at:
                    raise LeaseLostError("系统设置已被其他请求修改，请刷新后重试")
                serializer.instance = locked_config
                config = serializer.save()
                if changed_cpa_pricing:
                    refresh_cpa_history(config, rebuild=False)
                for replay_account_id in (
                    derived_account_ids | cpa_account_ids
                ):
                    rebuild_account(
                        replay_account_id,
                        config,
                        guard=guards[replay_account_id],
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

class CPAAccountListView(AdminAPIView):
    def post(self, request):
        serializer = CPAConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return error("连接参数格式无效", details=serializer.errors)
        config = AppSettings.load()
        try:
            with _temporary_cpa_client(
                config,
                serializer.validated_data,
            ) as client:
                accounts = client.list_codex_accounts()
        except (CPAError, ValueError) as exc:
            return error(str(exc), 502)
        return ok(accounts)


class CPACollectorStatusView(AdminAPIView):
    def get(self, _request):
        return ok(get_collector_status())


class TestCPAView(AdminAPIView):
    def post(self, request):
        serializer = CPAConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return error("连接参数格式无效", details=serializer.errors)
        config = AppSettings.load()
        try:
            values = serializer.validated_data
            with _temporary_cpa_client(config, values) as client:
                result = client.test_connection()
            result.update(_temporary_cpa_subscriber(config, values).probe())
        except (CPAError, ValueError) as exc:
            return error(str(exc), 502)
        return ok(result)



class MonitoredAccountListView(PageAccessAPIView):
    required_page_permissions = (
        PagePermission.OBSERVATIONS,
        PagePermission.PARTICLE_FILTER,
        PagePermission.STATISTICS,
    )

    def get(self, request):
        accounts = visible_accounts_for(
            request.user,
            MonitoredAccount.objects.order_by(
                "name",
                "provider",
                "external_account_id",
                "cpa_auth_index",
            ),
        )
        return ok(MonitoredAccountSerializer(accounts, many=True).data)

    def post(self, request):
        serializer = MonitoredAccountSerializer(data=request.data)
        if not serializer.is_valid():
            return error("监控账号校验失败", details=serializer.errors)
        provider = serializer.validated_data.get("provider", "sub2api")
        source_lock_id = serializer.validated_data.get("external_account_id")
        settings_id = AppSettings.load().pk
        if provider == "sub2api":
            with fenced_fact_write([source_lock_id]):
                AppSettings.objects.select_for_update().get(pk=settings_id)
                account = serializer.save()
                AccountParticipant.objects.bulk_create(
                    [
                        AccountParticipant(account=account, participant=participant)
                        for participant in Participant.objects.order_by("id")
                    ]
                )
        else:
            with transaction.atomic():
                AppSettings.objects.select_for_update().get(pk=settings_id)
                account = serializer.save()
        return ok(MonitoredAccountSerializer(account).data, 201)


class ReadOnlyMonitoredAccountListView(MonitoredAccountListView):
    """External API-key view exposing configured monitored accounts."""

    required_page_permissions = (PagePermission.ACCOUNT_STATUS,)

    authentication_classes = [APIKeyAuthentication]
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
        fact_key = account.fact_key
        previous_capacity_profile = account.resolved_capacity_profile
        try:
            with fenced_fact_write(
                [fact_key],
                ttl=timedelta(minutes=30),
            ) as guards:
                account = serializer.save()
                if account.resolved_capacity_profile != previous_capacity_profile:
                    rebuild_account(
                        fact_key,
                        AppSettings.load(),
                        guard=guards[fact_key],
                    )
        except ValueError as exc:
            return error(
                "账号档位未保存：历史派生结果重建失败",
                409,
                {"replay": [str(exc)]},
            )
        return ok(MonitoredAccountSerializer(account).data)



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
    """Generate, rotate, or revoke the permanent external API key."""

    def post(self, _request):
        config = AppSettings.load()
        api_key, digest, hint = generate_api_key()
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


def _system_user_api_key_data(user) -> dict:
    record = SystemUserAPIKey.objects.filter(user=user).first()
    return {
        "configured": record is not None,
        "hint": record.hint if record is not None else "",
        "created_at": (record.created_at.isoformat() if record is not None else None),
    }


class MyAPIKeyView(AuthenticatedAPIView):
    """Manage the API key bound to the current ordinary system user."""

    permission_classes = [IsAuthenticated, HasPageAccess]
    required_page_permissions = (PagePermission.SETTINGS,)

    @staticmethod
    def _reject_staff(request):
        if request.user.is_staff or request.user.is_superuser:
            return error("管理员请使用全局 API Key", 400)
        return None

    def get(self, request):
        rejected = self._reject_staff(request)
        if rejected is not None:
            return rejected
        return ok(_system_user_api_key_data(request.user))

    def post(self, request):
        rejected = self._reject_staff(request)
        if rejected is not None:
            return rejected
        api_key, digest, hint = generate_api_key()
        record, _created = SystemUserAPIKey.objects.update_or_create(
            user=request.user,
            defaults={
                "key_hash": digest,
                "hint": hint,
                "created_at": timezone.now(),
            },
        )
        return ok(
            {
                "api_key": api_key,
                "hint": record.hint,
                "created_at": record.created_at.isoformat(),
            }
        )

    def delete(self, request):
        rejected = self._reject_staff(request)
        if rejected is not None:
            return rejected
        SystemUserAPIKey.objects.filter(user=request.user).delete()
        return ok({"revoked": True})
