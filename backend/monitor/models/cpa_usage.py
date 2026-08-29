"""Raw CPA usage events persisted from the management usage stream."""

from django.db import models


class CPAUsageEvent(models.Model):
    account = models.ForeignKey(
        "MonitoredAccount",
        on_delete=models.CASCADE,
        related_name="cpa_usage_events",
    )
    event_fingerprint = models.CharField(max_length=64, unique=True)
    request_id = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    model = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, blank=True)
    endpoint = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=64, blank=True)
    api_key_hash = models.CharField(max_length=64, blank=True)
    api_key_hint = models.CharField(max_length=4, blank=True)
    input_tokens = models.PositiveBigIntegerField(default=0)
    cached_input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    reasoning_tokens = models.PositiveBigIntegerField(default=0)
    total_tokens = models.PositiveBigIntegerField(default=0)
    failed = models.BooleanField(default=False)
    latency_ms = models.PositiveBigIntegerField(default=0)
    ttft_ms = models.PositiveBigIntegerField(default=0)
    requested_service_tier = models.CharField(max_length=32, blank=True)
    response_service_tier = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        indexes = [
            models.Index(fields=["account", "occurred_at"]),
            models.Index(fields=["account", "api_key_hash", "occurred_at"]),
        ]
        verbose_name = "CPA 用量事件"
        verbose_name_plural = "CPA 用量事件"
