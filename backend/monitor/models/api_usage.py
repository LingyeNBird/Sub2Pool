"""Persisted current-cycle API-key usage conclusions."""

from django.db import models

from .observations import Observation
from .participants import Participant


class ParticipantAPIUsageSnapshot(models.Model):
    """一次参与者 API 密钥用量汇总结论；不保存逐条请求日志。"""

    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="api_usage_snapshots",
    )
    observation = models.ForeignKey(
        Observation,
        on_delete=models.CASCADE,
        related_name="api_usage_snapshots",
    )
    account_id = models.BigIntegerField(db_index=True)
    attribution_started_at = models.DateTimeField()
    observed_at = models.DateTimeField()
    cost_basis = models.CharField(max_length=16)
    fast_correction_enabled = models.BooleanField(default=False)
    fast_correction_rules_hash = models.CharField(max_length=64, blank=True)
    participant_total_usd = models.DecimalField(max_digits=18, decimal_places=6)
    weekly_total_estimate_usd = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    participant_weekly_percent = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=0,
    )
    api_keys = models.JSONField(default=list)

    class Meta:
        ordering = ["-observed_at", "-id"]
        indexes = [
            models.Index(
                fields=["participant", "-observed_at"],
                name="api_usage_participant_time",
            ),
            models.Index(
                fields=["account_id", "attribution_started_at"],
                name="api_usage_account_cycle",
            ),
        ]
