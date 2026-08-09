"""Raw observations and replayable participant usage facts."""
from decimal import Decimal

from django.db import models

from .validators import PERCENT_VALIDATORS
from .participants import Participant


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
