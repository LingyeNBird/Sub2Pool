"""Per-page visibility grants for non-staff system users."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class PagePermission(models.TextChoices):
    DASHBOARD = "dashboard", "额度总览"
    ACCOUNT_STATUS = "account_status", "账号状态"
    PARTICIPANTS = "participants", "参与者"
    SYSTEM_USERS = "system_users", "系统用户"
    OBSERVATIONS = "observations", "观测记录"
    PARTICLE_FILTER = "particle_filter", "粒子轨迹"
    STATISTICS = "statistics", "额度统计"
    NOTIFICATIONS = "notifications", "通知记录"
    LOGIN_RECORDS = "login_records", "登录记录"
    SETTINGS = "settings", "系统设置"
    TUTORIAL = "tutorial", "使用教程"

ASSIGNABLE_PAGE_PERMISSIONS = tuple(
    permission
    for permission in PagePermission.values
    if permission != PagePermission.SETTINGS
)
ASSIGNABLE_PAGE_PERMISSION_CHOICES = tuple(
    choice
    for choice in PagePermission.choices
    if choice[0] != PagePermission.SETTINGS
)


PARTICIPANT_SCOPED_PAGE_PERMISSIONS = frozenset(
    {
        PagePermission.DASHBOARD,
        PagePermission.PARTICIPANTS,
        PagePermission.OBSERVATIONS,
        PagePermission.STATISTICS,
        PagePermission.NOTIFICATIONS,
    }
)
ACCOUNT_SCOPED_PAGE_PERMISSIONS = frozenset(
    {
        PagePermission.DASHBOARD,
        PagePermission.ACCOUNT_STATUS,
        PagePermission.OBSERVATIONS,
        PagePermission.PARTICLE_FILTER,
        PagePermission.STATISTICS,
    }
)


class SystemUserPageAccess(models.Model):
    """One explicit page grant; staff users bypass these rows entirely."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="page_accesses",
    )
    page_code = models.CharField(
        max_length=32,
        choices=ASSIGNABLE_PAGE_PERMISSION_CHOICES,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user_id", "page_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "page_code"],
                name="unique_system_user_page_access",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.get_page_code_display()}"


class SystemUserAPIKey(models.Model):
    """One permanent API key bound to an ordinary system user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="system_api_key",
    )
    key_hash = models.CharField(max_length=64, unique=True)
    hint = models.CharField(max_length=4)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["user_id"]

    def __str__(self) -> str:
        return f"{self.user}: ****{self.hint}"
