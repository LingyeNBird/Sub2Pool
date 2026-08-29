"""Singleton application settings model."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from ..fast_correction.rules import (
    default_fast_correction_rules,
    validate_fast_correction_rules,
)
from ..quota_profiles import (
    QUOTA_PROFILE_CHOICES,
    CapacityRangeProfile,
    capacity_range_profile,
    effective_quota_profile,
)
from .validators import validate_service_url
from ..cpa.pricing import (
    default_cpa_model_pricing,
    validate_cpa_model_pricing,
)


class MonitoredAccount(models.Model):
    """One quota-bearing OpenAI account exposed by Sub2API or CPA."""

    PROVIDER_CHOICES = (
        ("sub2api", "Sub2API"),
        ("cpa", "CPA"),
    )
    QUERY_MODE_CHOICES = (
        ("passive", "仅读取 Sub2API 被动快照"),
        ("direct", "调用上游账号额度接口"),
    )
    provider = models.CharField(
        max_length=16,
        choices=PROVIDER_CHOICES,
        default="sub2api",
    )
    pool = models.ForeignKey(
        "QuotaPool",
        on_delete=models.PROTECT,
        related_name="accounts",
    )
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="visible_monitored_accounts",
        blank=True,
    )

    external_account_id = models.BigIntegerField(
        unique=True,
        null=True,
        blank=True,
    )
    cpa_auth_index = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    quota_query_mode = models.CharField(
        max_length=16,
        choices=QUERY_MODE_CHOICES,
        default="passive",
    )
    quota_profile = models.CharField(
        max_length=16,
        choices=QUOTA_PROFILE_CHOICES,
        default="auto",
    )
    detected_plan_type = models.CharField(max_length=16, blank=True)
    capacity_min_usd_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("1")),
            MaxValueValidator(Decimal("50000")),
        ],
    )
    capacity_max_usd_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal("1")),
            MaxValueValidator(Decimal("50000")),
        ],
    )
    last_local_check_at = models.DateTimeField(null=True, blank=True)
    last_upstream_check_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_local_check_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "provider", "external_account_id", "cpa_auth_index"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        capacity_min_usd_override__isnull=True,
                        capacity_max_usd_override__isnull=True,
                    )
                    | models.Q(
                        capacity_min_usd_override__isnull=False,
                        capacity_max_usd_override__isnull=False,
                        capacity_min_usd_override__gte=Decimal("1"),
                        capacity_max_usd_override__lte=Decimal("50000"),
                        capacity_min_usd_override__lt=models.F(
                            "capacity_max_usd_override"
                        ),
                    )
                ),
                name="account_capacity_range_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        provider="sub2api",
                        external_account_id__isnull=False,
                        cpa_auth_index__isnull=True,
                    )
                    | models.Q(
                        provider="cpa",
                        external_account_id__isnull=True,
                        cpa_auth_index__isnull=False,
                    )
                ),
                name="account_provider_identity_valid",
            ),
        ]
        verbose_name = "监控上游账号"
        verbose_name_plural = "监控上游账号"

    def save(self, *args, **kwargs):
        if self.provider == "sub2api":
            self.cpa_auth_index = None
        elif self.provider == "cpa":
            self.external_account_id = None
            self.quota_query_mode = "direct"
        if self.pool_id is not None:
            return super().save(*args, **kwargs)
        from .participants import QuotaPool

        with transaction.atomic():
            self.pool = QuotaPool.for_new_account(self.name)
            return super().save(*args, **kwargs)

    @classmethod
    def for_fact_key(cls, account_id: int) -> "MonitoredAccount | None":
        if account_id < 0:
            return cls.objects.filter(pk=-account_id, provider="cpa").first()
        return cls.objects.filter(
            external_account_id=account_id,
            provider="sub2api",
        ).first()

    @property
    def fact_key(self) -> int:
        if self.provider == "sub2api":
            if self.external_account_id is None:
                raise ValueError("Sub2API 账号缺少上游账号 ID")
            return self.external_account_id
        if self.pk is None:
            raise ValueError("CPA 账号必须先保存后才能生成事实键")
        return -self.pk

    @property
    def source_account_id(self) -> str:
        if self.provider == "cpa":
            return self.cpa_auth_index or ""
        return str(self.external_account_id or "")

    @property
    def effective_quota_profile(self) -> str:
        return effective_quota_profile(
            self.quota_profile,
            self.detected_plan_type,
        )

    @property
    def resolved_capacity_profile(self) -> CapacityRangeProfile:
        return capacity_range_profile(
            self.quota_profile,
            self.detected_plan_type,
            self.capacity_min_usd_override,
            self.capacity_max_usd_override,
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.provider}:{self.source_account_id})"


class AppSettings(models.Model):
    """单例业务配置。

    Admin Token 与 SMTP 密码只保存密文。站点密钥、Cookie 安全策略等部署级参数故意不放到页面，
    避免一个已登录会话把整个站点的安全边界改坏。
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    monitoring_enabled = models.BooleanField(default=True)
    sub2api_base_url = models.CharField(
        max_length=500,
        default="http://host.docker.internal:8080",
        validators=[validate_service_url],
    )
    sub2api_admin_token_encrypted = models.TextField(blank=True)
    cpa_base_url = models.CharField(
        max_length=500,
        default="http://host.docker.internal:8317",
        validators=[validate_service_url],
    )
    cpa_management_key_encrypted = models.TextField(blank=True)
    cpa_fast_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("2.5"),
        validators=[
            MinValueValidator(Decimal("1")),
            MaxValueValidator(Decimal("100")),
        ],
    )
    cpa_double_billing_enabled = models.BooleanField(default=False)
    cpa_double_billing_threshold_tokens = models.PositiveIntegerField(
        default=272000,
        validators=[MinValueValidator(1)],
    )
    cpa_double_billing_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("2"),
        validators=[
            MinValueValidator(Decimal("1")),
            MaxValueValidator(Decimal("100")),
        ],
    )
    cpa_model_pricing = models.JSONField(
        default=default_cpa_model_pricing,
        validators=[validate_cpa_model_pricing],
    )

    request_timeout_seconds = models.PositiveIntegerField(default=20)
    verify_tls = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")

    cost_basis = models.CharField(
        max_length=16,
        choices=(("actual", "实际扣费"), ("standard", "标准计费")),
        default="actual",
    )
    weekly_quota_model = models.CharField(
        max_length=24,
        choices=(
            ("time_varying", "时变额度"),
            ("constant_average", "平均恒定"),
        ),
        default="time_varying",
    )
    # 开关和规则只控制新采样；已落库的历史修正事实永久保留并参与重放。
    fast_correction_enabled = models.BooleanField(default=True)
    fast_correction_rules = models.JSONField(
        default=default_fast_correction_rules,
        validators=[validate_fast_correction_rules],
    )
    initial_usd_per_percent = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("16")
    )
    safety_factor = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.95"),
        validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal("1"))],
    )
    daily_estimate_min_percent_span = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("5"),
        validators=[
            MinValueValidator(Decimal("1")),
            MaxValueValidator(Decimal("100")),
        ],
    )

    local_poll_minutes = models.PositiveIntegerField(
        default=10, validators=[MinValueValidator(2), MaxValueValidator(1440)]
    )
    progress_threshold_percent = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("0.75"),
        validators=[
            MinValueValidator(Decimal("0.1")),
            MaxValueValidator(Decimal("10")),
        ],
    )
    active_max_calibration_hours = models.PositiveIntegerField(
        default=8, validators=[MinValueValidator(1), MaxValueValidator(168)]
    )
    reset_proximity_minutes = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(5), MaxValueValidator(1440)]
    )
    stale_warning_hours = models.PositiveIntegerField(
        default=12, validators=[MinValueValidator(1), MaxValueValidator(336)]
    )

    limit_warning_usd = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1"),
        validators=[MinValueValidator(0)],
    )
    recommendation_change_usd = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("10"),
        validators=[MinValueValidator(0)],
    )
    rate_change_alert_percent = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        default=Decimal("5"),
        validators=[MinValueValidator(0)],
    )
    notify_on_limit_exhausted = models.BooleanField(default=True)
    notify_on_recommendation_change = models.BooleanField(default=False)
    notify_on_rate_change = models.BooleanField(default=True)
    notify_on_collection_error = models.BooleanField(default=True)
    notification_cooldown_minutes = models.PositiveIntegerField(
        default=120, validators=[MinValueValidator(1), MaxValueValidator(10080)]
    )
    email_provider = models.CharField(
        max_length=16,
        choices=(("smtp", "SMTP"), ("resend", "Resend")),
        default="smtp",
    )

    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password_encrypted = models.TextField(blank=True)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_use_ssl = models.BooleanField(default=False)
    smtp_from_email = models.EmailField(blank=True)
    notification_email = models.EmailField(blank=True)
    resend_api_key_encrypted = models.TextField(blank=True)
    resend_from_email = models.CharField(max_length=320, blank=True)
    # 外部 API Key 只保存摘要与尾号，明文仅在生成响应中返回一次。
    readonly_api_key_hash = models.CharField(max_length=64, blank=True)
    readonly_api_key_hint = models.CharField(max_length=4, blank=True)
    readonly_api_key_created_at = models.DateTimeField(null=True, blank=True)

    last_local_check_at = models.DateTimeField(null=True, blank=True)
    # 由全局后台轮询进程记录；手动测算不会改变自动轮询的计划时间。
    next_local_check_at = models.DateTimeField(null=True, blank=True)
    last_upstream_check_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # 条件更新该字段可在 Web 进程和后台轮询进程之间形成一个轻量租约。
    run_lease_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "AppSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "服务设置"
        verbose_name_plural = "服务设置"
