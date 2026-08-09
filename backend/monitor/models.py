"""数据库模型：配置、参与者、原始观测、可重建归属结果和通知记录。"""
from decimal import Decimal
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from django.core.validators import MaxValueValidator, MinValueValidator
from django.conf import settings
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
    weekly_quota_model = models.CharField(
        max_length=24,
        choices=(
            ("time_varying", "时变额度"),
            ("constant_average", "平均恒定"),
        ),
        default="time_varying",
    )
    # FAST 修正只控制新采样是否读取请求日志；已经落库的修正事实永久保留。
    fast_correction_enabled = models.BooleanField(default=True)
    initial_usd_per_percent = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("16"))
    safety_factor = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal("0.95"),
        validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal("1"))],
    )
    conservative_percentile = models.PositiveSmallIntegerField(default=25, validators=[MinValueValidator(1), MaxValueValidator(50)])
    rate_history_samples = models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(1), MaxValueValidator(100)])
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


class Participant(models.Model):
    """一个 Sub2API 用户及其在上游周限中的百分比权益。"""

    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    sub2api_user_id = models.BigIntegerField(unique=True)
    # Sub2API 用户名随参与者关系一起缓存，避免首页为了显示名称额外访问 Admin API。
    sub2api_username = models.CharField(max_length=150, blank=True)
    # 邮箱与用户名来自同一次 Admin 用户列表读取；用户名为空时用邮箱展示账号身份。
    sub2api_email = models.EmailField(blank=True)
    share_percent = models.DecimalField(max_digits=7, decimal_places=3, validators=PERCENT_VALIDATORS)
    is_owner = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="quota_participants",
        blank=True,
    )

    # 最近一次本地探测值用于展示；它们不是账本，真正的分配账本在 ParticipantSnapshot。
    latest_balance_usd = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True
    )
    latest_selected_cost = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_owner", "id"]
        verbose_name = "拼车参与者"
        verbose_name_plural = "拼车参与者"

    def __str__(self) -> str:
        return self.name




class Observation(models.Model):
    """一次上游百分比采样；原始事实保持不变，所有计算字段均可重放生成。"""

    SOURCE_CHOICES = (("scheduled", "定时"), ("manual", "手动"), ("exhausted", "额度耗尽触发"), ("reset", "重置临近"))
    EXCLUSION_CHOICES = (
        ("", "未排除"),
        ("manual", "管理员排除"),
        ("automatic", "异常检测排除"),
    )
    account_id = models.BigIntegerField(db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="scheduled")
    observed_at = models.DateTimeField()
    window_seconds = models.PositiveIntegerField(default=604800)
    upstream_resets_at = models.DateTimeField()
    # 这是按原始采样推导的边界，不是独立、可变的周期实体。
    attribution_started_at = models.DateTimeField(null=True, blank=True)
    upstream_used_percent = models.DecimalField(max_digits=8, decimal_places=4, validators=PERCENT_VALIDATORS)
    # 区间内有效进度是可重放派生值；官方窗口等于上游值，手动起点则扣除起点进度。
    interval_used_percent = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        validators=PERCENT_VALIDATORS,
    )
    # raw_* 与两种成本字段是不可变采样事实；selected_total_cost 是重放后的区间累计值。
    raw_selected_total_cost = models.DecimalField(max_digits=18, decimal_places=6)
    selected_total_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    # 每个字段记录“上一原始采样点到当前采样点”的额外等效成本。
    # NULL 表示该区间尚未计算；0 表示已计算且没有 FAST 请求。
    fast_correction_standard_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    fast_correction_actual_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    fast_correction_started_at = models.DateTimeField(null=True, blank=True)
    # FAST 明细的区间请求总数；NULL 表示旧版本尚未保存，重建后可补齐。
    fast_correction_request_count = models.PositiveIntegerField(
        null=True,
        blank=True,
    )
    delta_percent = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    delta_cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    sample_usd_per_percent = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    effective_usd_per_percent = models.DecimalField(max_digits=18, decimal_places=6)
    valid_sample = models.BooleanField(default=False)
    sample_note = models.CharField(max_length=255, blank=True)
    raw_window = models.JSONField(default=dict, blank=True)
    excluded_at = models.DateTimeField(null=True, blank=True)
    exclusion_source = models.CharField(
        max_length=16,
        choices=EXCLUSION_CHOICES,
        blank=True,
        default="",
    )
    # 管理员可把一个真实观测点固定为最高优先级起点；该观测的成本和百分比均作为零基线。
    is_manual_start = models.BooleanField(default=False)
    manual_start_reason = models.CharField(max_length=255, blank=True)
    manual_start_set_at = models.DateTimeField(null=True, blank=True)
    exclusion_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_at"]
        indexes = [
            models.Index(
                fields=["account_id", "-observed_at"],
                name="observation_account_time",
            ),
            models.Index(
                fields=["account_id", "attribution_started_at"],
                name="observation_replay_segment",
            ),
        ]


class ParticipantSnapshot(models.Model):
    """参与者在某个观测点的百分比账本和人工调整建议。"""

    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="participant_snapshots")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="snapshots")
    selected_cost = models.DecimalField(max_digits=18, decimal_places=6)
    # 原始累计成本永久保留；selected_cost 是当前重放区间内的累计成本。
    raw_selected_cost = models.DecimalField(max_digits=18, decimal_places=6)
    delta_cost = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    charged_delta_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    charged_cycle_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    remaining_share_percent = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    current_balance_usd = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    recommended_balance_usd = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    balance_difference_usd = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    needs_manual_update = models.BooleanField(default=False)
    # 一键应用成功后只隐藏当前观测的建议；下一次观测会生成新的快照并重新参与展示。
    recommendation_applied = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["participant_id"]
        constraints = [models.UniqueConstraint(fields=["observation", "participant"], name="unique_observation_participant")]


class ParticipantUsageSample(models.Model):
    """每次本地探测保存的参与者账号用量与用户余额，用于历史趋势图。"""

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="usage_samples",
    )
    account_id = models.BigIntegerField(db_index=True)
    attribution_started_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField()
    balance_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    selected_cost = models.DecimalField(max_digits=18, decimal_places=6)
    raw_selected_cost = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        ordering = ["observed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "account_id", "observed_at"],
                name="unique_participant_account_sample",
            )
        ]
        indexes = [
            models.Index(
                fields=["participant", "observed_at"],
                name="participant_usage_time",
            )
        ]

class Sub2APIUserUsageSample(models.Model):
    """每次本地探测保存的全量 Sub2API 用户原始用量。

    记录不依赖参与者配置；以后才绑定为参与者的用户，也能用这些不可变原始
    事实补建历史账本。标准成本与实际成本同时保存，避免丢失采样时的计费口径。
    """

    account_id = models.BigIntegerField(db_index=True)
    sub2api_user_id = models.BigIntegerField(db_index=True)
    username = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    observed_at = models.DateTimeField()
    window_started_at = models.DateTimeField()
    window_resets_at = models.DateTimeField()
    total_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)

    def selected_cost(self, basis: str) -> Decimal:
        return (
            self.total_actual_cost
            if basis == "actual"
            else self.total_standard_cost
        )

    class Meta:
        ordering = ["observed_at", "sub2api_user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "sub2api_user_id", "observed_at"],
                name="unique_sub2api_user_usage_sample",
            )
        ]
        indexes = [
            models.Index(
                fields=["sub2api_user_id", "observed_at"],
                name="sub2api_user_usage_time",
            )
        ]


class ObservationFastCorrection(models.Model):
    """一个观测区间内，单个 Sub2API 用户的 FAST 等效成本修正。

    记录按原始 Sub2API 用户 ID 保存，不依赖当时是否已经创建参与者。因此以后
    才绑定的参与者也能在重放时获得完整的历史 FAST 修正。
    """

    observation = models.ForeignKey(
        Observation,
        on_delete=models.CASCADE,
        related_name="fast_corrections",
    )
    sub2api_user_id = models.BigIntegerField(db_index=True)
    fast_request_count = models.PositiveIntegerField(default=0)
    # 该用户在区间内的全部请求数；FAST 与非 FAST 请求均计入。
    # NULL 表示旧版本明细尚未通过修正重建补齐。
    request_count = models.PositiveIntegerField(null=True, blank=True)
    fast_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    fast_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    standard_correction_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    actual_correction_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )

    class Meta:
        ordering = ["sub2api_user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["observation", "sub2api_user_id"],
                name="unique_observation_fast_user",
            )
        ]
        indexes = [
            models.Index(
                fields=["sub2api_user_id", "observation"],
                name="fast_correction_user_obs",
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


class BlockedIPAddress(models.Model):
    """管理员封禁的登录来源地址；同一地址可按不同来源类型分别封禁。"""

    SOURCE_CHOICES = (
        ("request", "服务器来源 IP"),
        ("remote", "直连地址"),
        ("webrtc", "WebRTC IP"),
    )

    address = models.GenericIPAddressField()
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    login_event = models.ForeignKey(
        LoginEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_ip_blocks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["address", "source_type"],
                name="unique_blocked_ip_source",
            )
        ]
        indexes = [
            models.Index(
                fields=["source_type", "address"],
                name="blocked_ip_source_addr",
            )
        ]
