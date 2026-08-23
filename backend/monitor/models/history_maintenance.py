"""Canonical sampling points and auditable history-maintenance control plane."""
from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class UsageSamplePoint(models.Model):
    """One locally committed sampling fact group.

    ``write_status=complete`` only proves that this application's local rows were
    committed together; it does not prove older upstream logs were retained.
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
    """Durable journal for one global Sub2API user balance side effect."""

    STATE_CHOICES = (
        ("prepared", "已持久化，尚未确认远端结果"),
        ("reconciliation_required", "远端结果不确定，等待对账"),
        ("remote_confirmed", "远端已确认，等待本地提交"),
        ("committed", "远端与本地均已提交"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participant = models.ForeignKey(
        "Participant",
        on_delete=models.PROTECT,
        related_name="balance_operations",
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
                fields=["participant", "state"],
                name="balance_op_participant_state",
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


class ParticipantBalanceOperationSource(models.Model):
    """One account fact and allocation share contributing to a global operation."""

    operation = models.ForeignKey(
        ParticipantBalanceOperation,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    account = models.ForeignKey(
        "MonitoredAccount",
        on_delete=models.PROTECT,
        related_name="balance_operation_sources",
    )
    account_external_id = models.BigIntegerField(db_index=True)
    base_revision = models.PositiveBigIntegerField()
    snapshot = models.ForeignKey(
        "ParticipantSnapshot",
        on_delete=models.PROTECT,
        related_name="balance_operation_sources",
    )
    share_percent = models.DecimalField(max_digits=7, decimal_places=3)
    contribution_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
    )

    class Meta:
        ordering = ["account_external_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "account_external_id"],
                name="unique_balance_op_account",
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
    """Immutable local audit identity plus mutable apply metadata."""

    STATE_CHOICES = (
        ("generating", "生成中"),
        ("ready", "可应用"),
        ("blocked", "已阻断"),
        ("stale", "已过期"),
        ("applying", "应用中"),
        ("applied", "已应用"),
        ("failed", "失败"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_id = models.BigIntegerField(db_index=True)
    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default="generating"
    )
    base_revision = models.PositiveBigIntegerField()
    result_revision = models.PositiveBigIntegerField(null=True, blank=True)
    source_digest = models.CharField(max_length=64)
    plan_digest = models.CharField(max_length=64, blank=True)
    algorithm_version = models.CharField(max_length=64)
    build_id = models.CharField(max_length=128)
    config_digest = models.CharField(max_length=64)
    participant_policy_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    blockers = models.JSONField(default=list, blank=True)
    replay_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["account_id", "-created_at"],
                name="history_run_account_time",
            )
        ]
