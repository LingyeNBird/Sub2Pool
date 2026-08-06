"""DRF 输入输出序列化器。

复杂统计响应仍由专用 presenter 生成；这里集中处理有明确字段契约的写入请求，
避免每个 View 重复做字符串、数字和布尔值转换。
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import AppSettings, Participant
from .secrets import encrypt_secret


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    client_network = serializers.DictField(required=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)


class ParticipantWriteSerializer(serializers.ModelSerializer):
    """参与者写入契约，并维护启用参与者权益总和不超过 100%。"""

    class Meta:
        model = Participant
        fields = (
            "name",
            "email",
            "sub2api_user_id",
            "share_percent",
            "is_owner",
            "enabled",
            "notes",
        )

    def validate(self, attrs):
        instance = self.instance
        enabled = attrs.get("enabled", instance.enabled if instance else True)
        share = attrs.get(
            "share_percent",
            instance.share_percent if instance else Decimal("0"),
        )
        queryset = Participant.objects.filter(enabled=True)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        total = sum((item.share_percent for item in queryset), Decimal("0"))
        if enabled and total + share > Decimal("100"):
            raise serializers.ValidationError(
                {
                    "share_percent": (
                        f"启用参与者的权益合计将达到 {total + share}%，"
                        "不能超过 100%"
                    )
                }
            )
        return attrs


SETTINGS_FIELDS = (
    "monitoring_enabled",
    "sub2api_base_url",
    "openai_account_id",
    "quota_platform",
    "quota_query_mode",
    "request_timeout_seconds",
    "verify_tls",
    "timezone",
    "cost_basis",
    "initial_usd_per_percent",
    "safety_factor",
    "conservative_percentile",
    "rate_history_samples",
    "local_poll_minutes",
    "progress_threshold_percent",
    "active_max_calibration_hours",
    "reset_proximity_minutes",
    "stale_warning_hours",
    "limit_warning_usd",
    "recommendation_change_usd",
    "rate_change_alert_percent",
    "notify_on_limit_exhausted",
    "notify_on_recommendation_change",
    "notify_on_rate_change",
    "notify_on_collection_error",
    "notification_cooldown_minutes",
    "email_provider",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_use_tls",
    "smtp_use_ssl",
    "smtp_from_email",
    "notification_email",
    "resend_from_email",
)


class AppSettingsSerializer(serializers.ModelSerializer):
    """业务设置序列化器；密钥只允许写入，响应只暴露是否已配置。"""

    sub2api_admin_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    smtp_password = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    resend_api_key = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    clear_sub2api_admin_token = serializers.BooleanField(
        required=False,
        write_only=True,
    )
    clear_smtp_password = serializers.BooleanField(required=False, write_only=True)
    clear_resend_api_key = serializers.BooleanField(required=False, write_only=True)
    sub2api_token_configured = serializers.SerializerMethodField()
    smtp_password_configured = serializers.SerializerMethodField()
    resend_api_key_configured = serializers.SerializerMethodField()

    class Meta:
        model = AppSettings
        fields = (
            *SETTINGS_FIELDS,
            "sub2api_admin_token",
            "smtp_password",
            "resend_api_key",
            "clear_sub2api_admin_token",
            "clear_smtp_password",
            "clear_resend_api_key",
            "sub2api_token_configured",
            "smtp_password_configured",
            "resend_api_key_configured",
            "last_local_check_at",
            "last_upstream_check_at",
            "last_success_at",
            "last_error",
        )
        read_only_fields = (
            "last_local_check_at",
            "last_upstream_check_at",
            "last_success_at",
            "last_error",
        )

    def get_sub2api_token_configured(self, obj) -> bool:
        return bool(obj.sub2api_admin_token_encrypted)

    def get_smtp_password_configured(self, obj) -> bool:
        return bool(obj.smtp_password_encrypted)

    def get_resend_api_key_configured(self, obj) -> bool:
        return bool(obj.resend_api_key_encrypted)

    def validate(self, attrs):
        instance = self.instance
        provider = attrs.get(
            "email_provider",
            instance.email_provider if instance else "smtp",
        )
        use_tls = attrs.get(
            "smtp_use_tls",
            instance.smtp_use_tls if instance else True,
        )
        use_ssl = attrs.get(
            "smtp_use_ssl",
            instance.smtp_use_ssl if instance else False,
        )
        if provider == "smtp" and use_tls and use_ssl:
            raise serializers.ValidationError(
                {"smtp_use_ssl": "SMTP SSL 与 STARTTLS 不能同时启用"}
            )
        return attrs

    def update(self, instance: AppSettings, validated_data):
        token = validated_data.pop("sub2api_admin_token", "")
        smtp_password = validated_data.pop("smtp_password", "")
        resend_api_key = validated_data.pop("resend_api_key", "")
        clear_token = validated_data.pop("clear_sub2api_admin_token", False)
        clear_smtp = validated_data.pop("clear_smtp_password", False)
        clear_resend = validated_data.pop("clear_resend_api_key", False)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        if token:
            instance.sub2api_admin_token_encrypted = encrypt_secret(token)
        if clear_token:
            instance.sub2api_admin_token_encrypted = ""
        if smtp_password:
            instance.smtp_password_encrypted = encrypt_secret(smtp_password)
        if clear_smtp:
            instance.smtp_password_encrypted = ""
        if resend_api_key:
            instance.resend_api_key_encrypted = encrypt_secret(resend_api_key)
        if clear_resend:
            instance.resend_api_key_encrypted = ""

        # ModelSerializer 不会自动调用 full_clean；这里保留模型层全部校验器。
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            details = getattr(exc, "message_dict", {"non_field_errors": exc.messages})
            raise serializers.ValidationError(details) from exc
        instance.save()
        return instance
