"""Separate explicit consent and immutable research facts from billing settings."""
from django.db import models
from ..research.protocol import DEFAULT_ENDPOINT


class ResearchSettings(models.Model):
    # Deliberately not writable through the generic /settings PATCH serializer.
    enabled = models.BooleanField(default=False)
    projects = models.JSONField(default=list)
    endpoint = models.CharField(max_length=512, default=DEFAULT_ENDPOINT)
    interval_hours = models.PositiveSmallIntegerField(default=6)
    gateway_only = models.BooleanField(default=False)
    consent_hash = models.CharField(max_length=64, blank=True)
    consent_at = models.DateTimeField(null=True)
    config_revision = models.PositiveBigIntegerField(default=0)
    identity_encrypted = models.TextField(blank=True)
    report_revision = models.PositiveBigIntegerField(default=0)
    next_run_at = models.DateTimeField(null=True)
    lease_token = models.CharField(max_length=36, blank=True)
    lease_until = models.DateTimeField(null=True)
    last_computed_at = models.DateTimeField(null=True)
    last_sent_at = models.DateTimeField(null=True)
    last_sent_endpoint = models.CharField(max_length=512, blank=True)
    last_sent_hash = models.CharField(max_length=64, blank=True)
    last_status = models.CharField(max_length=48, default="disabled")
    last_error = models.CharField(max_length=160, blank=True)
    failures = models.PositiveIntegerField(default=0)
    # Disposable, reproducible projections, NEVER used as billing/source facts.
    summary = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class ResearchRequestComponents(models.Model):
    """Optional, immutable upstream component amounts, captured ONLY after opt-in.

    No guessed prices. Null/missing evidence does not become a zero charge.
    A separate table avoids changing existing billing facts or API cache equality.
    """
    fact = models.OneToOneField("BillingUsageFact", related_name="research_components", on_delete=models.CASCADE)
    input_cost = models.CharField(max_length=96)
    cache_creation_cost = models.CharField(max_length=96)
    cache_read_cost = models.CharField(max_length=96)
    output_cost = models.CharField(max_length=96)
