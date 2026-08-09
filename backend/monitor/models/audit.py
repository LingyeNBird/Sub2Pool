"""Notification, login, and IP-block audit models."""
from django.db import models

from .participants import Participant


class NotificationEvent(models.Model):
    """邮件发送审计与去重依据。未配置 SMTP 时也保留 skipped 记录。"""

    STATUS_CHOICES = (("sent", "已发送"), ("skipped", "已跳过"), ("failed", "失败"))
    TYPE_CHOICES = (("limit_exhausted", "额度耗尽"), ("recommendation_changed", "建议变化"), ("rate_changed", "汇率变化"), ("collection_error", "采集失败"), ("test", "测试"))
    event_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    severity = models.CharField(max_length=16, default="warning")
    participant = models.ForeignKey(Participant, null=True, blank=True, on_delete=models.SET_NULL)
    dedupe_key = models.CharField(max_length=255, db_index=True)
    recipient = models.EmailField(blank=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["dedupe_key", "-created_at"])]


class LoginEvent(models.Model):
    """本系统登录尝试审计；WebRTC 地址来自浏览器，只能作为辅助线索。"""

    username = models.CharField(max_length=150, blank=True)
    success = models.BooleanField(default=False)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    remote_ip = models.GenericIPAddressField(null=True, blank=True)
    webrtc_supported = models.BooleanField(null=True, blank=True)
    webrtc_ips = models.JSONField(default=list, blank=True)
    user_agent = models.TextField(blank=True)
    failure_reason = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="login_event_time"),
            models.Index(
                fields=["success", "-created_at"],
                name="login_success_time",
            ),
        ]


class BlockedIPAddress(models.Model):
    """管理员封禁的登录来源地址；同一地址可按不同来源类型分别封禁。"""

    SOURCE_CHOICES = (
        ("request", "服务器来源 IP"),
        ("remote", "直连地址"),
        ("webrtc", "WebRTC IP"),
    )

    address = models.GenericIPAddressField()
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    login_event = models.ForeignKey(
        LoginEvent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_ip_blocks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["address", "source_type"],
                name="unique_blocked_ip_source",
            )
        ]
        indexes = [
            models.Index(
                fields=["source_type", "address"],
                name="blocked_ip_source_addr",
            )
        ]
