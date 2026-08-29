"""Historical CPA usage-collection coverage, independent from quota samples."""

from django.db import models
from django.db.models import F, Q


class CPAAccountCollectionInterval(models.Model):
    """One account's coverage during a real RESP subscription session."""

    account = models.ForeignKey(
        "MonitoredAccount",
        on_delete=models.CASCADE,
        related_name="cpa_collection_intervals",
    )
    session_key = models.CharField(max_length=64)
    connected_at = models.DateTimeField(db_index=True)
    disconnected_at = models.DateTimeField(null=True, blank=True, db_index=True)
    end_reliable = models.BooleanField(default=False)
    opening_observation = models.ForeignKey(
        "Observation",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    closing_observation = models.ForeignKey(
        "Observation",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["connected_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "session_key"],
                name="unique_cpa_account_collection_session",
            ),
            models.UniqueConstraint(
                fields=["account"],
                condition=Q(disconnected_at__isnull=True),
                name="unique_open_cpa_collection_interval",
            ),
            models.CheckConstraint(
                condition=(
                    Q(disconnected_at__isnull=True)
                    | Q(disconnected_at__gte=F("connected_at"))
                ),
                name="cpa_collection_end_not_before_start",
            ),
        ]
        indexes = [
            models.Index(
                fields=["account", "connected_at"],
                name="cpa_collection_account_start",
            )
        ]
        verbose_name = "CPA 账号采集区间"
        verbose_name_plural = "CPA 账号采集区间"
