"""Application settings and temporary Sub2API connection serializers."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from ..fast_correction.rebuild import missing_current_cycle_intervals
from ..models import AppSettings, validate_service_url
from ..secrets import encrypt_secret


class Sub2APIConnectionSerializer(serializers.Serializer):
    """设置页临时连接参数；校验后仅用于本次请求，不会写入数据库。"""

    sub2api_base_url = serializers.CharField(
        required=False,
        max_length=500,
        validators=[validate_service_url],
    )
    sub2api_admin_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    openai_account_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    quota_query_mode = serializers.ChoiceField(
        required=False,
        choices=("passive", "direct"),
    )
    request_timeout_seconds = serializers.IntegerField(
        required=False,
        min_value=1,
    )
    verify_tls = serializers.BooleanField(required=False)


SETTINGS_FIELDS = (
    "monitoring_enabled",
    "sub2api_base_url",
    "openai_account_id",
    "quota_query_mode",
    "request_timeout_seconds",
    "verify_tls",
    "timezone",
    "cost_basis",
    "weekly_quota_model",
    "fast_correction_enabled",
    "initial_usd_per_percent",
    "safety_factor",
    "daily_estimate_min_percent_span",
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
    fast_correction_rebuild_recommended = serializers.SerializerMethodField()
    fast_correction_missing_intervals = serializers.SerializerMethodField()

    readonly_api_key_configured = serializers.SerializerMethodField()
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
            "fast_correction_rebuild_recommended",
            "fast_correction_missing_intervals",
            "readonly_api_key_configured",
            "readonly_api_key_hint",
            "readonly_api_key_created_at",
            "last_local_check_at",
            "last_upstream_check_at",
            "last_success_at",
            "last_error",
        )
        read_only_fields = (
            "fast_correction_rebuild_recommended",
            "fast_correction_missing_intervals",
            "readonly_api_key_configured",
            "readonly_api_key_hint",
            "readonly_api_key_created_at",
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

    def get_readonly_api_key_configured(self, obj) -> bool:
        return bool(obj.readonly_api_key_hash)

    @staticmethod
    def _fast_missing_count(obj: AppSettings) -> int:
        cached = getattr(obj, "_fast_missing_interval_count", None)
        if cached is None:
            cached = missing_current_cycle_intervals(obj)
            obj._fast_missing_interval_count = cached
        return cached

    def get_fast_correction_rebuild_recommended(self, obj) -> bool:
        return bool(
            obj.fast_correction_enabled and self._fast_missing_count(obj) > 0
        )

    def get_fast_correction_missing_intervals(self, obj) -> int:
        return self._fast_missing_count(obj)

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise serializers.ValidationError(
                "请输入有效的 IANA 时区，例如 Asia/Shanghai"
            ) from exc
        return value

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
