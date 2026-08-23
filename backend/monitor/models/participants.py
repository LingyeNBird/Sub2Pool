"""Participant ownership and access model."""
from collections.abc import Iterable

from django.conf import settings
from django.db import models

from .validators import PERCENT_VALIDATORS


class QuotaPool(models.Model):
    """One allocation contract shared by one or more monitored accounts."""

    name = models.CharField(max_length=160)
    contract_revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "额度池"
        verbose_name_plural = "额度池"

    @classmethod
    def for_new_account(cls, account_name: str) -> "QuotaPool":
        """Reuse the sole preserved upgrade pool; otherwise start isolated."""
        pools = list(cls.objects.select_for_update().order_by("id")[:2])
        if (
            len(pools) == 1
            and pools[0].name == "默认混池"
            and not pools[0].accounts.exists()
        ):
            pool = pools[0]
            pool.contract_revision += 1
            pool.save(update_fields=["contract_revision", "updated_at"])
            return pool
        return cls.objects.create(name=f"{account_name} 独立池")

    @classmethod
    def bump_contract_revisions(cls, pool_ids: Iterable[int]) -> None:
        """Record a new audit revision for the changed pool contract."""
        for pool in (
            cls.objects.select_for_update()
            .filter(pk__in=set(pool_ids))
            .order_by("id")
        ):
            pool.contract_revision += 1
            pool.save(update_fields=["contract_revision", "updated_at"])

    def __str__(self) -> str:
        return self.name


class Participant(models.Model):
    """一个 Sub2API 用户；余额与身份属于整个 Sub2API 渠道。"""

    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    sub2api_user_id = models.BigIntegerField(unique=True)
    # Sub2API 用户名随参与者关系一起缓存，避免首页为了显示名称额外访问 Admin API。
    sub2api_username = models.CharField(max_length=150, blank=True)
    # 邮箱与用户名来自同一次 Admin 用户列表读取；用户名为空时用邮箱展示账号身份。
    sub2api_email = models.EmailField(blank=True)
    # 合同份额属于额度池，由 PoolParticipant 保存。
    is_owner = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="quota_participants",
        blank=True,
    )

    # 最近一次全局余额探测值属于 Sub2API 用户；逐账号用量保存在 AccountParticipant。
    latest_balance_usd = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_owner", "id"]
        verbose_name = "拼车参与者"
        verbose_name_plural = "拼车参与者"

    def __str__(self) -> str:
        return self.name


class PoolParticipant(models.Model):
    """A participant's contractual percentage within one quota pool."""

    pool = models.ForeignKey(
        QuotaPool,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="pool_allocations",
    )
    share_percent = models.DecimalField(
        max_digits=7,
        decimal_places=3,
        validators=PERCENT_VALIDATORS,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pool_id", "participant_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["pool", "participant"],
                name="unique_pool_participant",
            )
        ]

    def __str__(self) -> str:
        return f"{self.pool}: {self.participant} ({self.share_percent}%)"


class AccountParticipant(models.Model):
    """A participant's latest usage cache within one monitored account."""

    account = models.ForeignKey(
        "MonitoredAccount",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="account_memberships",
    )
    latest_selected_cost = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        null=True,
        blank=True,
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_id", "participant_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "participant"],
                name="unique_account_participant",
            )
        ]

    def __str__(self) -> str:
        return f"{self.account}: {self.participant}"
