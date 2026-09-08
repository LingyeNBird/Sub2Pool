"""CPA identities and immutable, time-scoped ownership/contract evidence."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class CPAAPIKey(models.Model):
    key_hash = models.CharField(max_length=64, unique=True)
    hint = models.CharField(max_length=4)
    name = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class CPAKeyBinding(models.Model):
    key = models.ForeignKey(
        CPAAPIKey, on_delete=models.PROTECT, related_name="bindings"
    )
    participant = models.ForeignKey(
        "Participant", on_delete=models.PROTECT, related_name="cpa_bindings"
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    claim = models.OneToOneField(
        "CPAClaimPlan", null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["started_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["key"],
                condition=Q(ended_at__isnull=True),
                name="one_open_cpa_key_binding",
            ),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gt=F("started_at")),
                name="cpa_binding_positive_interval",
            ),
        ]
        indexes = [models.Index(fields=["key", "started_at", "ended_at"])]


class CPAClaimPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.ForeignKey(CPAAPIKey, on_delete=models.PROTECT)
    participant = models.ForeignKey(
        "Participant", on_delete=models.PROTECT, related_name="cpa_claim_plans"
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    source_digest = models.CharField(max_length=64)
    preview = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    applied_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )


class CPAQuotaContract(models.Model):
    """Frozen per-account pool policy; never backdate today's allocations."""

    account = models.ForeignKey(
        "MonitoredAccount", on_delete=models.PROTECT, related_name="cpa_contracts"
    )
    effective_at = models.DateTimeField()
    pool_id_at_capture = models.BigIntegerField()
    pool_name = models.CharField(max_length=160)
    revision = models.PositiveBigIntegerField()
    allocations = models.JSONField(default=list)

    class Meta:
        ordering = ["effective_at", "id"]
        indexes = [models.Index(fields=["account", "effective_at"])]


class CPAClaimEvent(models.Model):
    """A historical claim owns only the immutable events confirmed in its preview."""

    plan = models.ForeignKey(
        CPAClaimPlan, on_delete=models.PROTECT, related_name="events"
    )
    event = models.OneToOneField(
        "CPAUsageEvent", on_delete=models.PROTECT, related_name="ownership_claim"
    )
