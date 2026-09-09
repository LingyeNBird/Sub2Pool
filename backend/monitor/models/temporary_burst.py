"""Temporary unlimited recommendations and their auditable cycle settlements."""

from django.db import models


class TemporaryBurstSession(models.Model):
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    participant_users = models.JSONField(default=dict)
    base_url = models.CharField(max_length=500)
    exhaustion_reminder_enabled = models.BooleanField(default=False)
    carryover_enabled = models.BooleanField(default=True)
    terminated_at = models.DateTimeField(null=True, blank=True)


class TemporaryBurstCycle(models.Model):
    session = models.ForeignKey(
        TemporaryBurstSession, on_delete=models.PROTECT, related_name="cycles"
    )
    account = models.ForeignKey(
        "MonitoredAccount", on_delete=models.PROTECT, related_name="burst_cycles"
    )
    resets_at = models.DateTimeField()
    quota_model = models.CharField(max_length=32)
    # Immutable contract/identity and opening credit, expressed in percentage points.
    members = models.JSONField(default=list)
    is_burst_cycle = models.BooleanField(default=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    settlement = models.JSONField(default=list)
    evidence_at = models.DateTimeField(null=True, blank=True)
    settlement_context = models.JSONField(default=dict)
    carry_edits = models.JSONField(default=list)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "resets_at"], name="unique_burst_account_cycle"
            )
        ]
        indexes = [
            models.Index(fields=["account", "settled_at"], name="burst_pending_cycles")
        ]
