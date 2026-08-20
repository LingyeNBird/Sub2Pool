"""Per-page visibility grants for non-staff system users."""

from django.conf import settings
from django.db import models


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


PARTICIPANT_SCOPED_PAGE_PERMISSIONS = frozenset(
    {
        PagePermission.DASHBOARD,
        PagePermission.PARTICIPANTS,
        PagePermission.OBSERVATIONS,
        PagePermission.STATISTICS,
        PagePermission.NOTIFICATIONS,
    }
)


class SystemUserPageAccess(models.Model):
    """One explicit page grant; staff users bypass these rows entirely."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="page_accesses",
    )
    page_code = models.CharField(max_length=32, choices=PagePermission.choices)
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
