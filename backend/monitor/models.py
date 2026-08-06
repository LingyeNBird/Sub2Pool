"""数据库模型：配置、参与者、上游周期、测算观测和通知记录。"""
from decimal import Decimal
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


PERCENT_VALIDATORS = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))]

def validate_service_url(value: str) -> None:
    """允许 Docker 服务名等内网主机名，同时仍要求明确的 HTTP(S) 地址。"""
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError("请输入有效的 HTTP(S) 地址。") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("请输入有效的 HTTP(S) 地址。")


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
    openai_account_id = models.BigIntegerField(null=True, blank=True)
    quota_query_mode = models.CharField(
        max_length=16,
        choices=(("passive", "仅读取 Sub2API 被动快照"), ("direct", "调用上游账号额度接口")),
        default="passive",
    )
    request_timeout_seconds = models.PositiveIntegerField(default=20)
    verify_tls = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")

    cost_basis = models.CharField(
        max_length=16,
        choices=(("actual", "实际扣费"), ("standard", "标准计费")),
        default="actual",
    )
    initial_usd_per_percent = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("16"))
    safety_factor = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.95"),
        validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal("1"))],
    )
    conservative_percentile = models.PositiveSmallIntegerField(default=25, validators=[MinValueValidator(1), MaxValueValidator(50)])
    rate_history_samples = models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(1), MaxValueValidator(100)])

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

    last_local_check_at = models.DateTimeField(null=True, blank=True)
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


class Participant(models.Model):
    """一个 Sub2API 用户及其在上游周限中的百分比权益。"""

    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    sub2api_user_id = models.BigIntegerField(unique=True)
    share_percent = models.DecimalField(max_digits=7, decimal_places=3, validators=PERCENT_VALIDATORS)
    is_owner = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    # 最近一次本地探测值用于展示；它们不是账本，真正的分配账本在 ParticipantSnapshot。
    latest_weekly_usage_usd = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    latest_weekly_limit_usd = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    latest_selected_cost = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_owner", "id"]
        verbose_name = "拼车参与者"
        verbose_name_plural = "拼车参与者"

    def __str__(self) -> str:
        return self.name


class QuotaCycle(models.Model):
    """OpenAI 上游七天窗口；按上游 reset_at 分段，不使用 Sub2API 的周一窗口。"""

    account_id = models.BigIntegerField()
    window_seconds = models.PositiveIntegerField(default=604800)
    starts_at = models.DateTimeField()
    resets_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-resets_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "resets_at", "starts_at"],
                name="unique_account_reset_cycle",
            )
        ]


class Observation(models.Model):
    """一次真正查询上游百分比后的校准观测。"""

    SOURCE_CHOICES = (("scheduled", "定时"), ("manual", "手动"), ("exhausted", "额度耗尽触发"), ("reset", "重置临近"))
    cycle = models.ForeignKey(QuotaCycle, on_delete=models.CASCADE, related_name="observations")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="scheduled")
    observed_at = models.DateTimeField()
    upstream_used_percent = models.DecimalField(max_digits=8, decimal_places=4, validators=PERCENT_VALIDATORS)
    selected_total_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    delta_percent = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    delta_cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    sample_usd_per_percent = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    effective_usd_per_percent = models.DecimalField(max_digits=18, decimal_places=6)
    valid_sample = models.BooleanField(default=False)
    sample_note = models.CharField(max_length=255, blank=True)
    raw_window = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at"]
        indexes = [models.Index(fields=["cycle", "-observed_at"])]


class ParticipantSnapshot(models.Model):
    """参与者在某个观测点的百分比账本和人工调整建议。"""

    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="participant_snapshots")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="snapshots")
    selected_cost = models.DecimalField(max_digits=18, decimal_places=6)
    delta_cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    charged_delta_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    charged_cycle_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    remaining_share_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    platform_weekly_usage_usd = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    platform_weekly_limit_usd = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    recommended_weekly_limit_usd = models.DecimalField(max_digits=18, decimal_places=4)
    recommendation_difference_usd = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    needs_manual_update = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["participant_id"]
        constraints = [models.UniqueConstraint(fields=["observation", "participant"], name="unique_observation_participant")]


class ParticipantUsageSample(models.Model):
    """每次本地探测保存的参与者 Sub2API 周用量，用于历史趋势图。"""

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="usage_samples",
    )
    cycle = models.ForeignKey(
        QuotaCycle,
        on_delete=models.CASCADE,
        related_name="usage_samples",
    )
    observed_at = models.DateTimeField()
    weekly_usage_usd = models.DecimalField(max_digits=18, decimal_places=6)
    weekly_limit_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    selected_cost = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        ordering = ["observed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "cycle", "observed_at"],
                name="unique_participant_cycle_sample",
            )
        ]
        indexes = [
            models.Index(
                fields=["participant", "observed_at"],
                name="participant_usage_time",
            )
        ]


class NotificationEvent(models.Model):
    """邮件发送审计与去重依据。未配置 SMTP 时也保留 skipped 记录。"""

    STATUS_CHOICES = (("sent", "已发送"), ("skipped", "已跳过"), ("failed", "失败"))
    TYPE_CHOICES = (("limit_exhausted", "额度耗尽"), ("recommendation_changed", "建议变化"), ("rate_changed", "汇率变化"), ("collection_error", "采集失败"), ("test", "测试"))
    event_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    severity = models.CharField(max_length=16, default="warning")
    participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.SET_NULL)
    dedupe_key = models.CharField(max_length=255, db_index=True)
    recipient = models.EmailField(blank=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["dedupe_key", "-created_at"])]


class LoginEvent(models.Model):
    """本系统登录尝试审计；WebRTC 地址来自浏览器，只能作为辅助线索。"""

    username = models.CharField(max_length=150, blank=True)
    success = models.BooleanField(default=False)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    remote_ip = models.GenericIPAddressField(null=True, blank=True)
    webrtc_supported = models.BooleanField(null=True, blank=True)
    webrtc_ips = models.JSONField(default=list, blank=True)
    user_agent = models.TextField(blank=True)
    failure_reason = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="login_event_time"),
            models.Index(
                fields=["success", "-created_at"],
                name="login_success_time",
            ),
        ]
