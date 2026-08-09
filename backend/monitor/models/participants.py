"""Participant ownership and access model."""
from django.conf import settings
from django.db import models

from . import PERCENT_VALIDATORS


class Participant(models.Model):
    """一个 Sub2API 用户及其在上游周限中的百分比权益。"""

    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    sub2api_user_id = models.BigIntegerField(unique=True)
    # Sub2API 用户名随参与者关系一起缓存，避免首页为了显示名称额外访问 Admin API。
    sub2api_username = models.CharField(max_length=150, blank=True)
    # 邮箱与用户名来自同一次 Admin 用户列表读取；用户名为空时用邮箱展示账号身份。
    sub2api_email = models.EmailField(blank=True)
    share_percent = models.DecimalField(max_digits=7, decimal_places=3, validators=PERCENT_VALIDATORS)
    is_owner = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="quota_participants",
        blank=True,
    )

    # 最近一次本地探测值用于展示；它们不是账本，真正的分配账本在 ParticipantSnapshot。
    latest_balance_usd = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True
    )
    latest_selected_cost = models.DecimalField(
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
