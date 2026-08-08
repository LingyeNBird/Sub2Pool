"""SMTP / Resend 通知发送与去重。"""
from datetime import timedelta
from email.message import EmailMessage
import smtplib
import ssl

from django.utils import timezone
import httpx

from .models import AppSettings, NotificationEvent, Participant
from .secrets import decrypt_secret


class NotificationDeliveryError(RuntimeError):
    """可安全写入通知记录的发送错误，不包含邮件服务密钥。"""


def _send_smtp(
    config: AppSettings,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    if not config.smtp_host or not config.smtp_from_email:
        raise NotificationDeliveryError("未完整配置 SMTP 主机或发件人")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.smtp_from_email
    message["To"] = recipient
    message.set_content(body)

    password = decrypt_secret(config.smtp_password_encrypted)
    context = ssl.create_default_context()
    if config.smtp_use_ssl:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            config.smtp_host,
            config.smtp_port,
            timeout=config.request_timeout_seconds,
            context=context,
        )
    else:
        smtp = smtplib.SMTP(
            config.smtp_host,
            config.smtp_port,
            timeout=config.request_timeout_seconds,
        )
    with smtp:
        if config.smtp_use_tls and not config.smtp_use_ssl:
            smtp.starttls(context=context)
        if config.smtp_username:
            smtp.login(config.smtp_username, password)
        smtp.send_message(message)


def _send_resend(
    config: AppSettings,
    recipient: str,
    subject: str,
    body: str,
    delivery_event_id: int,
) -> None:
    api_key = decrypt_secret(config.resend_api_key_encrypted)
    if not api_key or not config.resend_from_email:
        raise NotificationDeliveryError("未完整配置 Resend API Key 或发件人")
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            # Resend 的幂等键标识一次具体 HTTP 投递，而不是业务冷却键。
            # 同一业务提醒过了冷却期会创建新审计事件，因此必须使用新键；
            # 若将 dedupe_key 复用于不同正文，Resend 会在 24 小时内返回 409。
            "Idempotency-Key": f"pinche-notification-{delivery_event_id}",
        },
        json={
            "from": config.resend_from_email,
            "to": [recipient],
            "subject": subject,
            "text": body,
        },
        timeout=config.request_timeout_seconds,
    )
    if response.status_code >= 400:
        try:
            message = str(response.json().get("message", ""))[:300]
        except ValueError:
            message = ""
        detail = f"：{message}" if message else ""
        raise NotificationDeliveryError(
            f"Resend 返回 HTTP {response.status_code}{detail}"
        )
    try:
        response_data = response.json()
    except ValueError as exc:
        raise NotificationDeliveryError("Resend 返回了无效 JSON") from exc
    if not isinstance(response_data, dict) or not response_data.get("id"):
        raise NotificationDeliveryError("Resend 响应中缺少邮件 ID")

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
    if not recipient:
        event.error = "未配置接收通知邮箱"
        event.save(update_fields=["error"])
        return event

    try:
        if config.email_provider == "resend":
            _send_resend(config, recipient, subject, body, event.pk)
        else:
            _send_smtp(config, recipient, subject, body)
    except (
        OSError,
        smtplib.SMTPException,
        httpx.HTTPError,
        NotificationDeliveryError,
        ValueError,
    ) as exc:
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
