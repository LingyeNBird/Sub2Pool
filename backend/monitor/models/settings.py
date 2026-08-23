"""Singleton application settings model."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from ..fast_correction.rules import (
    default_fast_correction_rules,
    validate_fast_correction_rules,
)
from .validators import validate_service_url


class MonitoredAccount(models.Model):
    """One quota-bearing OpenAI account exposed by the configured Sub2API channel."""

    QUERY_MODE_CHOICES = (
        ("passive", "仅读取 Sub2API 被动快照"),
        ("direct", "调用上游账号额度接口"),
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

    external_account_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    quota_query_mode = models.CharField(
        max_length=16,
        choices=QUERY_MODE_CHOICES,
        default="passive",
    )
    last_local_check_at = models.DateTimeField(null=True, blank=True)
    last_upstream_check_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_local_check_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "external_account_id"]
        verbose_name = "监控上游账号"
        verbose_name_plural = "监控上游账号"

    def save(self, *args, **kwargs):
        if self.pool_id is not None:
            return super().save(*args, **kwargs)
        from .participants import QuotaPool

        with transaction.atomic():
            self.pool = QuotaPool.for_new_account(self.name)
            return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.external_account_id})"


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
    initial_usd_per_percent = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("16"))
    safety_factor = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.95"),
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

    local_poll_minutes = models.PositiveIntegerField(default=10, validators=[MinValueValidator(2), MaxValueValidator(1440)])
    progress_threshold_percent = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0.75"),
        validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal("10"))],
    )
    active_max_calibration_hours = models.PositiveIntegerField(default=8, validators=[MinValueValidator(1), MaxValueValidator(168)])
    reset_proximity_minutes = models.PositiveIntegerField(default=30, validators=[MinValueValidator(5), MaxValueValidator(1440)])
    stale_warning_hours = models.PositiveIntegerField(default=12, validators=[MinValueValidator(1), MaxValueValidator(336)])

    limit_warning_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"), validators=[MinValueValidator(0)])
    recommendation_change_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("10"), validators=[MinValueValidator(0)])
    rate_change_alert_percent = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("5"), validators=[MinValueValidator(0)])
    notify_on_limit_exhausted = models.BooleanField(default=True)
    notify_on_recommendation_change = models.BooleanField(default=False)
    notify_on_rate_change = models.BooleanField(default=True)
    notify_on_collection_error = models.BooleanField(default=True)
    notification_cooldown_minutes = models.PositiveIntegerField(default=120, validators=[MinValueValidator(1), MaxValueValidator(10080)])
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
    # 外部只读 API Key 只保存摘要与尾号，明文仅在生成响应中返回一次。
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
