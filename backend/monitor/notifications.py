"""SMTP 通知发送与去重。"""
from datetime import timedelta
from email.message import EmailMessage
import smtplib
import ssl

from django.utils import timezone

from .models import AppSettings, NotificationEvent, Participant
from .secrets import decrypt_secret


def send_notification(
    *,
    config: AppSettings,
    event_type: str,
    dedupe_key: str,
    subject: str,
    body: str,
    participant: Participant | None = None,
    severity: str = "warning",
    ignore_cooldown: bool = False,
) -> NotificationEvent | None:
    """发送一封通知，并把每次尝试持久化。

    相同 dedupe_key 在冷却期内返回 None，避免后台轮询反复轰炸邮箱。
    """
    if not ignore_cooldown:
        cutoff = timezone.now() - timedelta(minutes=config.notification_cooldown_minutes)
        if NotificationEvent.objects.filter(dedupe_key=dedupe_key, created_at__gte=cutoff).exists():
            return None

    recipient = config.notification_email.strip()
    event = NotificationEvent.objects.create(
        event_type=event_type,
        severity=severity,
        participant=participant,
        dedupe_key=dedupe_key,
        recipient=recipient,
        subject=subject,
        body=body,
        status="skipped",
    )
    if not recipient or not config.smtp_host or not config.smtp_from_email:
        event.error = "未完整配置收件人、SMTP 主机或发件人"
        event.save(update_fields=["error"])
        return event

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_from_email
    message["To"] = recipient
    message.set_content(body)

    try:
        password = decrypt_secret(config.smtp_password_encrypted)
        context = ssl.create_default_context()
        if config.smtp_use_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=config.request_timeout_seconds, context=context)
        else:
            smtp = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.request_timeout_seconds)
        with smtp:
            if config.smtp_use_tls and not config.smtp_use_ssl:
                smtp.starttls(context=context)
            if config.smtp_username:
                smtp.login(config.smtp_username, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        event.status = "failed"
        event.error = f"{exc.__class__.__name__}: {exc}"
        event.save(update_fields=["status", "error"])
        return event

    event.status = "sent"
    event.sent_at = timezone.now()
    event.save(update_fields=["status", "sent_at"])
    return event


def notify_collection_error(config: AppSettings, message: str) -> None:
    if not config.notify_on_collection_error:
        return
    # 按错误类别而不是完整消息去重，避免含时间或 ID 的错误绕过去重。
    kind = message.split("：", 1)[0][:80]
    send_notification(
        config=config,
        event_type="collection_error",
        dedupe_key=f"collection-error:{kind}",
        subject="[拼车额度] 采集失败",
        body=f"额度监控本次采集失败。\n\n{message}\n\n请登录服务检查 Sub2API 地址、Admin Token 与账号配置。",
        severity="error",
    )
