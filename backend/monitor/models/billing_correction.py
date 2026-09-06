"""Immutable upstream evidence; no long-context/model correction totals are stored."""

from decimal import Decimal
from django.db import models


class ObservationBillingCapture(models.Model):
    observation = models.OneToOneField("Observation", on_delete=models.CASCADE, related_name="billing_capture")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    request_count = models.PositiveIntegerField()
    schema_version = models.PositiveSmallIntegerField(default=1)
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(ended_at__gte=models.F("started_at")), name="billing_capture_time_order")]


class RequestFactFields(models.Model):
    source_log_id = models.BigIntegerField()
    user_id = models.BigIntegerField()
    created_at = models.DateTimeField()
    model = models.CharField(max_length=255, blank=True)
    service_tier = models.CharField(max_length=32, blank=True)
    # Text avoids SQLite float coercion and preserves upstream decimal precision.
    total_cost = models.CharField(max_length=96)
    actual_cost = models.CharField(max_length=96)
    input_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cache_creation_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cache_read_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    # None means upstream did not return the field, not "false".
    long_context_billing_applied = models.BooleanField(null=True, blank=True)
    api_key_id = models.BigIntegerField(default=0)
    api_key_name = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    def selected(self, basis: str) -> Decimal:
        return Decimal(self.actual_cost if basis == "actual" else self.total_cost)


class BillingUsageFact(RequestFactFields):
    capture = models.ForeignKey(ObservationBillingCapture, on_delete=models.CASCADE, related_name="facts")

    class Meta:
        ordering = ["created_at", "source_log_id"]
        constraints = [models.UniqueConstraint(fields=["capture", "source_log_id"], name="billing_capture_source_log")]
        indexes = [models.Index(fields=["capture", "user_id"], name="billing_capture_user")]


class APIUsageRequestFact(RequestFactFields):
    """Deduplicated API-statistics evidence, separate from quota calibration facts.

    Hourly snapshots reference a verified time interval, not repeated copies of
    the entire week's request logs. Repricing does not write this table.
    """
    account_id = models.BigIntegerField()

    class Meta:
        ordering = ["created_at", "source_log_id"]
        constraints = [models.UniqueConstraint(fields=["account_id", "source_log_id"], name="api_usage_account_source_log")]
        indexes = [models.Index(fields=["account_id", "user_id", "created_at"], name="api_usage_raw_user_time")]
