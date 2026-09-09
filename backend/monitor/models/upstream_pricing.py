"""Local journal for upstream pricing ownership and historical cutover."""
from django.db import models
from django.utils import timezone


def default_upstream_pricing_policy():
    return {
        "fast_rules": [{"model_pattern": "*", "multiplier": "2.5"}],
        "model_rules": [{"model_pattern": "gpt-6*", "multiplier": "1.8"}],
        "long_context_pricing_enabled": False,
    }


class UpstreamPricingState(models.Model):
    """One explicit pricing migration/apply journal per Sub2API connection."""
    policy = models.JSONField(default=default_upstream_pricing_policy)
    legacy_policy = models.JSONField(default=dict)
    local_cutoff_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=24, default="pending")
    revision = models.PositiveIntegerField(default=0)
    base_url = models.CharField(max_length=500, blank=True)
    targets = models.JSONField(default=list)
    selected_group_ids = models.JSONField(default=list, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    reverted_at = models.DateTimeField(null=True, blank=True)
    announcement_applied_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]
