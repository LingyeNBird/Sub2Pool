"""Bounded administrator disables applied to upstream Sub2API accounts."""

from django.conf import settings
from django.db import models
from django.utils import timezone

ACCOUNT_SCOPE = "account"
MODEL_SCOPE = "model"


class AccountTemporaryDisable(models.Model):
    """One upstream disable with its own restore deadline and audit trail.

    记录本身即日志：``restore_context`` 保存恢复时写回上游的状态，
    ``last_error`` 保留未完成的写入，因此进程中断后仍可人工恢复。
    同一账号同时最多一条账号级与一条模型级有效记录。
    """

    SCOPE_CHOICES = (
        (ACCOUNT_SCOPE, "整个账号"),
        (MODEL_SCOPE, "单个模型"),
    )

    account = models.ForeignKey(
        "MonitoredAccount",
        on_delete=models.CASCADE,
        related_name="temporary_disables",
    )
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    model = models.CharField(max_length=200, blank=True)
    # 账号级记录原 schedulable；模型级记录原 model_mapping（None 表示原本不设白名单）。
    restore_context = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="temporary_disables",
    )
    started_at = models.DateTimeField(default=timezone.now)
    restore_at = models.DateTimeField()
    # 恢复写入失败后的重试时刻，避免上游故障时反复重试。
    retry_at = models.DateTimeField(null=True, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    restore_source = models.CharField(max_length=16, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["started_at", "id"]
        verbose_name = "临时禁用"
        verbose_name_plural = "临时禁用"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "scope"],
                condition=models.Q(restored_at__isnull=True),
                name="unique_active_account_temporary_disable",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope=ACCOUNT_SCOPE, model="")
                    | models.Q(scope=MODEL_SCOPE, model__gt="")
                ),
                name="temporary_disable_model_matches_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=["restored_at", "restore_at"],
                name="temporary_disable_due",
            ),
        ]

    def __str__(self) -> str:
        target = self.model or "整个账号"
        return f"{self.account_id}:{target}"

    @property
    def is_active(self) -> bool:
        return self.restored_at is None
