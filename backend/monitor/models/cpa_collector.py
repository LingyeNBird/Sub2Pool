from django.db import models


class CPACollectorState(models.Model):
    """Last known health of the singleton CPA usage collector."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    connected = models.BooleanField(default=False)
    connected_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_persisted_at = models.DateTimeField(null=True, blank=True)
    pending_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CPA collector state"
        verbose_name_plural = "CPA collector state"
