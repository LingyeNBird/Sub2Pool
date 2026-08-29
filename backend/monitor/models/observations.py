"""Raw observations and replayable participant usage facts."""
from decimal import Decimal

from django.db import models

from .validators import PERCENT_VALIDATORS
from .participants import Participant


class Observation(models.Model):
    """一次上游百分比采样；已保存成本是历史维护不会替换的来源事实。"""
    SOURCE_CHOICES = (
        ("scheduled", "定时"),
        ("manual", "手动"),
        ("exhausted", "额度耗尽触发"),
        ("reset", "重置临近"),
        ("cpa_subscription_opened", "CPA 订阅后采样"),
        ("cpa_subscription_closed", "CPA 关闭前采样"),
    )
    EXCLUSION_CHOICES = (
        ("", "未排除"),
        ("manual", "管理员排除"),
        ("automatic", "异常检测排除"),
    )
    account_id = models.BigIntegerField(db_index=True)
    sample_point = models.ForeignKey(
        "UsageSamplePoint",
        on_delete=models.SET_NULL,
        related_name="observations",
        null=True,
        blank=True,
    )
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default="scheduled")
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
    # 累计成本必须与其查询窗口一起解释；历史维护可用请求日志覆盖错误快照。
    # normalized_* 是官方周期累计值，selected_total_cost 是归属区间派生值。
    raw_selected_total_cost = models.DecimalField(max_digits=18, decimal_places=6)
    selected_total_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    cost_window_started_at = models.DateTimeField(null=True, blank=True)
    cost_window_ended_at = models.DateTimeField(null=True, blank=True)
    interval_cost_started_at = models.DateTimeField(null=True, blank=True)
    interval_standard_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    interval_actual_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    interval_cost_source = models.CharField(max_length=24, blank=True)
    normalized_standard_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    normalized_actual_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
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
    # 时变模型的潜在真实进度、容量区间与诊断均由确定性重放生成。
    estimated_used_percent = models.DecimalField(
        max_digits=10,
        decimal_places=5,
        default=0,
        validators=PERCENT_VALIDATORS,
    )
    capacity_lower_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    capacity_upper_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    model_diagnostics = models.JSONField(default=dict, blank=True)
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
    # 管理员起点以真实观测建立零基线；结束记录定义禁止再次切分周期的保护范围。
    is_manual_start = models.BooleanField(default=False)
    manual_start_end = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    manual_start_reason = models.CharField(max_length=255, blank=True)
    manual_start_set_at = models.DateTimeField(null=True, blank=True)
    exclusion_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def raw_cost(self, basis: str) -> Decimal:
        return (
            self.total_actual_cost
            if basis == "actual"
            else self.total_standard_cost
        )

    def interval_cost(self, basis: str) -> Decimal | None:
        return (
            self.interval_actual_cost
            if basis == "actual"
            else self.interval_standard_cost
        )

    def normalized_cost(self, basis: str) -> Decimal:
        normalized = (
            self.normalized_actual_cost
            if basis == "actual"
            else self.normalized_standard_cost
        )
        return self.raw_cost(basis) if normalized is None else normalized

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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_manual_start=False,
                        manual_start_end__isnull=True,
                    )
                    | models.Q(
                        is_manual_start=True,
                        manual_start_end__isnull=False,
                    )
                ),
                name="manual_start_end_matches_flag",
            )
        ]


class ParticipantSnapshot(models.Model):
    """参与者在某个观测点的百分比账本和人工调整建议。"""

    observation = models.ForeignKey(Observation, on_delete=models.CASCADE, related_name="participant_snapshots")
    participant = models.ForeignKey(Participant, on_delete=models.CASCADE, related_name="snapshots")
    # Identity at collection time; participant bindings may change later.
    source_sub2api_user_id = models.BigIntegerField(null=True, blank=True)
    share_percent = models.DecimalField(
        max_digits=7,
        decimal_places=3,
        validators=PERCENT_VALIDATORS,
    )
    is_owner = models.BooleanField(default=False)
    # 历史快照保存当时的池合同身份；池合并或拆分不得改写旧观测。
    quota_pool_id = models.BigIntegerField(null=True, blank=True)
    quota_pool_name = models.CharField(max_length=160, blank=True)
    pool_contract_revision = models.PositiveBigIntegerField(null=True, blank=True)
    selected_cost = models.DecimalField(max_digits=18, decimal_places=6)
    # 来源累计成本可由请求日志重建；selected_cost 是归属区间内的派生累计值。
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
    charged_percent_lower = models.DecimalField(
        max_digits=10,
        decimal_places=5,
        null=True,
        blank=True,
    )
    charged_percent_upper = models.DecimalField(
        max_digits=10,
        decimal_places=5,
        null=True,
        blank=True,
    )
    recommended_balance_min_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    recommended_balance_max_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    deterministic_balance_min_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    deterministic_balance_max_usd = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    balance_difference_usd = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True
    )
    needs_manual_update = models.BooleanField(default=False)
    # 仅记录该账号事实曾参与过应用；当前聚合是否已应用还必须匹配操作来源和份额。
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
    sample_point = models.ForeignKey(
        "UsageSamplePoint",
        on_delete=models.SET_NULL,
        related_name="participant_usage_samples",
        null=True,
        blank=True,
    )
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
    """一个时间点的完整 Sub2API 用户用量。

    记录不依赖参与者配置；历史维护只在独立证据验证具体区间与维度时
    生成替换 patch，查询 horizon 和分页一致本身不授权覆盖。
    """

    account_id = models.BigIntegerField(db_index=True)
    sample_point = models.ForeignKey(
        "UsageSamplePoint",
        on_delete=models.SET_NULL,
        related_name="user_samples",
        null=True,
        blank=True,
    )
    sub2api_user_id = models.BigIntegerField(db_index=True)
    username = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    observed_at = models.DateTimeField()
    # 统计接口实际按自然日接收查询范围；旧版误存为官方窗口起点，因此旧数据
    # 迁移为未知，只有新采样或历史重取后才写入真实查询窗口。
    window_started_at = models.DateTimeField(null=True, blank=True)
    window_ended_at = models.DateTimeField(null=True, blank=True)
    window_resets_at = models.DateTimeField()
    total_standard_cost = models.DecimalField(max_digits=18, decimal_places=6)
    total_actual_cost = models.DecimalField(max_digits=18, decimal_places=6)
    interval_started_at = models.DateTimeField(null=True, blank=True)
    interval_standard_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    interval_actual_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    interval_source = models.CharField(max_length=24, blank=True)
    normalized_standard_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    normalized_actual_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )

    def selected_cost(self, basis: str) -> Decimal:
        return (
            self.total_actual_cost
            if basis == "actual"
            else self.total_standard_cost
        )

    def selected_interval_cost(self, basis: str) -> Decimal | None:
        return (
            self.interval_actual_cost
            if basis == "actual"
            else self.interval_standard_cost
        )

    def normalized_cost(self, basis: str) -> Decimal:
        normalized = (
            self.normalized_actual_cost
            if basis == "actual"
            else self.normalized_standard_cost
        )
        return self.selected_cost(basis) if normalized is None else normalized

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
