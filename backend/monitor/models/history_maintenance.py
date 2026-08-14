"""Canonical sampling points and auditable history-maintenance control plane."""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class UsageSamplePoint(models.Model):
    """One locally committed sampling fact group.

    ``write_status=complete`` only proves that this application's local rows were
    committed together.  It is deliberately separate from remote-log coverage.
    """

    WRITE_STATUS_CHOICES = (
        ("complete", "本地写入完整"),
        ("legacy_unknown", "旧数据完整性未知"),
    )
    RECONCILIATION_CHOICES = (
        ("reconciled", "账号与用户合计一致"),
        ("residual", "存在显式未分配残差"),
        ("unknown", "无法核对"),
        ("conflict", "核对冲突"),
    )

    account_id = models.BigIntegerField(db_index=True)
    observed_at = models.DateTimeField()
    window_started_at = models.DateTimeField(null=True, blank=True)
    window_ended_at = models.DateTimeField(null=True, blank=True)
    window_resets_at = models.DateTimeField(null=True, blank=True)
    capture_started_at = models.DateTimeField(null=True, blank=True)
    capture_finished_at = models.DateTimeField(null=True, blank=True)
    account_standard_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    account_actual_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    interval_started_at = models.DateTimeField(null=True, blank=True)
    interval_standard_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    interval_actual_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    residual_standard_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    residual_actual_cost = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    expected_user_count = models.PositiveIntegerField(null=True, blank=True)
    expected_user_digest = models.CharField(max_length=64, blank=True)
    write_status = models.CharField(
        max_length=24,
        choices=WRITE_STATUS_CHOICES,
        default="legacy_unknown",
    )
    reconciliation_status = models.CharField(
        max_length=16,
        choices=RECONCILIATION_CHOICES,
        default="unknown",
    )
    provenance = models.JSONField(default=dict, blank=True)
    fact_revision = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["observed_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "observed_at"],
                name="unique_usage_sample_point",
            )
        ]
        indexes = [
            models.Index(
                fields=["account_id", "observed_at"],
                name="usage_point_account_time",
            )
        ]


class ParticipantBalanceSample(models.Model):
    """Participant balance evidence independent from derived projections."""

    point = models.ForeignKey(
        UsageSamplePoint,
        on_delete=models.CASCADE,
        related_name="balance_samples",
    )
    participant = models.ForeignKey(
        "Participant",
        on_delete=models.CASCADE,
        related_name="balance_samples",
    )
    balance_usd = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    captured_at = models.DateTimeField()
    provenance = models.CharField(max_length=48)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["captured_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["point", "participant", "provenance"],
                name="unique_point_balance_source",
            )
        ]



class ParticipantBalanceOperation(models.Model):
    """Durable, idempotent journal for one administrator balance side effect."""

    STATE_CHOICES = (
        ("prepared", "已持久化，尚未确认远端结果"),
        ("reconciliation_required", "远端结果不确定，等待对账"),
        ("remote_confirmed", "远端已确认，等待本地提交"),
        ("committed", "远端与本地均已提交"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.BigIntegerField(db_index=True)
    base_revision = models.PositiveBigIntegerField()
    participant = models.ForeignKey(
        "Participant",
        on_delete=models.PROTECT,
        related_name="balance_operations",
    )
    snapshot = models.OneToOneField(
        "ParticipantSnapshot",
        on_delete=models.PROTECT,
        related_name="balance_operation",
    )
    sub2api_user_id = models.BigIntegerField()
    requested_balance_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )
    confirmed_balance_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    state = models.CharField(
        max_length=32,
        choices=STATE_CHOICES,
        default="prepared",
    )
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    remote_confirmed_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["account_id", "state"],
                name="balance_op_account_state",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        state__in=(
                            "prepared",
                            "reconciliation_required",
                        ),
                        confirmed_balance_usd__isnull=True,
                    )
                    | Q(
                        state__in=("remote_confirmed", "committed"),
                        confirmed_balance_usd__isnull=False,
                    )
                ),
                name="balance_op_state_payload",
            )
        ]

class HistoryMaintenanceState(models.Model):
    """Monotonic source revision and fenced lease for one upstream account."""

    account_id = models.BigIntegerField(unique=True)
    fact_revision = models.PositiveBigIntegerField(default=0)
    fence_token = models.PositiveBigIntegerField(default=0)
    lease_owner = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class HistoricalRebuildRun(models.Model):
    """Immutable plan identity plus mutable apply/rollback lifecycle metadata."""

    MODE_CHOICES = (
        ("audit_replay", "本地审计并重放"),
        ("verified_remote_repair", "远端验证修复"),
    )
    STATE_CHOICES = (
        ("generating", "生成中"),
        ("ready", "可应用"),
        ("blocked", "已阻断"),
        ("stale", "已过期"),
        ("applying", "应用中"),
        ("applied", "已应用"),
        ("rolled_back", "已回滚"),
        ("failed", "失败"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.BigIntegerField(db_index=True)
    mode = models.CharField(max_length=32, choices=MODE_CHOICES)
    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default="generating"
    )
    base_revision = models.PositiveBigIntegerField()
    result_revision = models.PositiveBigIntegerField(null=True, blank=True)
    rollback_revision = models.PositiveBigIntegerField(null=True, blank=True)
    cutoff = models.DateTimeField(null=True, blank=True)
    requested_started_at = models.DateTimeField(null=True, blank=True)
    requested_ended_at = models.DateTimeField(null=True, blank=True)
    source_digest = models.CharField(max_length=64)
    plan_digest = models.CharField(max_length=64, blank=True)
    algorithm_version = models.CharField(max_length=64)
    build_id = models.CharField(max_length=128)
    config_digest = models.CharField(max_length=64)
    participant_policy_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    blockers = models.JSONField(default=list, blank=True)
    patch_summary = models.JSONField(default=dict, blank=True)
    before_source_hash = models.CharField(max_length=64, blank=True)
    before_observable_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["account_id", "-created_at"],
                name="history_run_account_time",
            )
        ]


class HistoricalRebuildCoverage(models.Model):
    """Evidence status for one exact half-open range and fact dimension."""

    DIMENSION_CHOICES = (
        ("account_cost", "账号成本"),
        ("user_cost", "用户成本"),
        ("fast_cost", "FAST 成本"),
        ("request_count", "请求数"),
        ("api_key", "API Key"),
    )
    STATUS_CHOICES = (
        ("verified", "已验证"),
        ("verified_empty", "已验证为空"),
        ("captured_local", "本地完整写入"),
        ("out_of_scope", "目标范围外"),
        ("policy_only", "仅策略推定"),
        ("unknown", "未知"),
        ("unavailable", "不可用"),
    )

    run = models.ForeignKey(
        HistoricalRebuildRun,
        on_delete=models.CASCADE,
        related_name="coverage_rows",
    )
    point = models.ForeignKey(
        UsageSamplePoint,
        on_delete=models.PROTECT,
        related_name="rebuild_coverage_rows",
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    dimension = models.CharField(max_length=24, choices=DIMENSION_CHOICES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    evidence_type = models.CharField(max_length=64)
    evidence_digest = models.CharField(max_length=64, blank=True)
    blocker = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at", "dimension", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "point", "started_at", "ended_at", "dimension"],
                name="unique_rebuild_coverage",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__gt=models.F("started_at")),
                name="coverage_nonempty_range",
            ),
        ]


class HistoricalRebuildPatch(models.Model):
    """Typed before/after source patch protected by the immutable plan digest."""

    KIND_CHOICES = (
        ("observation_cost", "观测/采样点成本"),
        ("user_cost", "用户成本"),
        ("fast_fact", "FAST 事实"),
    )

    run = models.ForeignKey(
        HistoricalRebuildRun,
        on_delete=models.CASCADE,
        related_name="patches",
    )
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    sample_point = models.ForeignKey(
        UsageSamplePoint,
        on_delete=models.PROTECT,
        related_name="rebuild_patches",
    )
    observation = models.ForeignKey(
        "Observation",
        on_delete=models.PROTECT,
        related_name="rebuild_patches",
        null=True,
        blank=True,
    )
    user_sample = models.ForeignKey(
        "Sub2APIUserUsageSample",
        on_delete=models.PROTECT,
        related_name="rebuild_patches",
        null=True,
        blank=True,
    )
    sub2api_user_id = models.BigIntegerField(null=True, blank=True)
    natural_key = models.JSONField()
    schema_version = models.PositiveSmallIntegerField(default=1)
    before_payload = models.JSONField(null=True, blank=True)
    after_payload = models.JSONField()
    required_coverage_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "sequence"],
                name="unique_rebuild_patch_sequence",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="observation_cost",
                        sub2api_user_id__isnull=True,
                    )
                    | Q(
                        kind="user_cost",
                        sub2api_user_id__isnull=False,
                    )
                    | Q(
                        kind="fast_fact",
                        observation__isnull=False,
                        sub2api_user_id__isnull=True,
                    )
                ),
                name="rebuild_patch_natural_key",
            ),
        ]
