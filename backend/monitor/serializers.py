"""DRF 输入输出序列化器。

复杂统计响应仍由专用 presenter 生成；这里集中处理有明确字段契约的写入请求，
避免每个 View 重复做字符串、数字和布尔值转换。
"""

from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .fast_correction import missing_current_cycle_intervals
from .models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    Participant,
    validate_service_url,
)
from .participant_history import sync_participant_history

from .secrets import encrypt_secret

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    client_network = serializers.DictField(required=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)


class BlockedIPAddressSerializer(serializers.ModelSerializer):
    """IP 封禁写入契约；地址由 GenericIPAddressField 统一规范化。"""

    login_event_id = serializers.PrimaryKeyRelatedField(
        source="login_event",
        queryset=LoginEvent.objects.all(),
        required=False,
        allow_null=True,
    )
    source_label = serializers.CharField(
        source="get_source_type_display",
        read_only=True,
    )

    class Meta:
        model = BlockedIPAddress
        fields = (
            "id",
            "address",
            "source_type",
            "source_label",
            "notes",
            "login_event_id",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


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



class ParticipantWriteSerializer(serializers.ModelSerializer):
    """参与者写入契约，并维护启用参与者权益总和不超过 100%。"""

    class Meta:
        model = Participant
        fields = (
            "name",
            "email",
            "sub2api_user_id",
            "sub2api_username",
            "sub2api_email",
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

    @transaction.atomic
    def create(self, validated_data):
        participant = super().create(validated_data)
        sync_participant_history(participant)
        return participant

    @transaction.atomic
    def update(self, instance, validated_data):
        previous_user_id = instance.sub2api_user_id
        needs_history_sync = any(
            field in validated_data
            for field in ("sub2api_user_id", "share_percent", "enabled")
        )
        participant = super().update(instance, validated_data)
        if needs_history_sync:
            sync_participant_history(
                participant,
                previous_user_id=previous_user_id,
            )
        return participant


class SystemUserWriteSerializer(serializers.Serializer):
    """普通系统用户写入契约；管理员账号不通过该接口管理。"""

    username = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        validators=User._meta.get_field("username").validators,
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    is_active = serializers.BooleanField(default=True)
    participant_ids = serializers.PrimaryKeyRelatedField(
        source="participants",
        many=True,
        allow_empty=False,
        queryset=Participant.objects.all(),
    )

    def validate_username(self, value: str) -> str:
        queryset = User.objects.filter(username__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("该用户名已存在")
        return value

    def validate(self, attrs):
        password = attrs.get("password", "")
        if self.instance is None and not password:
            raise serializers.ValidationError({"password": "添加用户时必须设置密码"})
        if password:
            candidate = User(
                username=attrs.get(
                    "username",
                    self.instance.username if self.instance else "",
                ),
                email=attrs.get(
                    "email",
                    self.instance.email if self.instance else "",
                ),
            )
            try:
                validate_password(password, candidate)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"password": list(exc.messages)}
                ) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        participants = validated_data.pop("participants")
        password = validated_data.pop("password")
        user = User(
            **validated_data,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(password)
        user.save()
        user.quota_participants.set(participants)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        participants = validated_data.pop("participants", None)
        password = validated_data.pop("password", "")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        if participants is not None:
            instance.quota_participants.set(participants)

        return instance

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
    "conservative_percentile",
    "rate_history_samples",
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
            "last_local_check_at",
            "last_upstream_check_at",
            "last_success_at",
            "last_error",
        )
        read_only_fields = (
            "fast_correction_rebuild_recommended",
            "fast_correction_missing_intervals",
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
