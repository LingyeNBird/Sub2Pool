"""面向 Vue 前端的 JSON API。所有修改均只作用于本服务自己的 SQLite。"""
import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .engine import run_monitor
from .models import AppSettings, NotificationEvent, Observation, Participant, ParticipantSnapshot, QuotaCycle
from .notifications import send_notification
from .secrets import encrypt_secret
from .sub2api import Sub2APIClient, Sub2APIError


def ok(data=None, status=200):
    return JsonResponse({"ok": True, "data": data}, status=status)


def error(message: str, status=400, details=None):
    payload = {"ok": False, "message": message}
    if details:
        payload["details"] = details
    return JsonResponse(payload, status=status)


def body_json(request):
    try:
        value = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("请求体必须是 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return value


def api_login_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("请先登录", 401)
        if not request.user.is_staff:
            return error("只有管理员可以访问", 403)
        return view(request, *args, **kwargs)

    return wrapped


def iso(value):
    return value.isoformat() if value else None


@require_GET
def health(_request):
    return ok({"status": "ok", "time": timezone.now().isoformat()})


@ensure_csrf_cookie
@require_GET
def csrf(_request):
    return ok({"csrf": "ready"})


@require_POST
def login_view(request):
    try:
        payload = body_json(request)
    except ValueError as exc:
        return error(str(exc))
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    ip = request.META.get("REMOTE_ADDR", "unknown")
    throttle_key = f"login-fail:{ip}:{username[:100]}"
    failures = int(cache.get(throttle_key, 0))
    from django.conf import settings

    if failures >= settings.LOGIN_FAILURE_LIMIT:
        return error("登录失败次数过多，请稍后再试", 429)
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_staff:
        cache.set(throttle_key, failures + 1, settings.LOGIN_FAILURE_WINDOW_SECONDS)
        return error("用户名或密码错误", 401)
    cache.delete(throttle_key)
    login(request, user)
    return ok({"username": user.get_username()})


@api_login_required
@require_POST
def logout_view(request):
    logout(request)
    return ok({"logged_out": True})


@api_login_required
@require_GET
def me(request):
    return ok({"username": request.user.get_username(), "is_staff": request.user.is_staff})


@api_login_required
@require_POST
def change_password(request):
    try:
        payload = body_json(request)
    except ValueError as exc:
        return error(str(exc))
    old_password = str(payload.get("old_password", ""))
    new_password = str(payload.get("new_password", ""))
    if not request.user.check_password(old_password):
        return error("当前密码不正确")
    try:
        validate_password(new_password, request.user)
    except ValidationError as exc:
        return error("新密码不符合安全要求", details=list(exc.messages))
    request.user.set_password(new_password)
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)
    return ok({"changed": True})


SETTINGS_FIELDS = {
    "monitoring_enabled",
    "sub2api_base_url",
    "openai_account_id",
    "quota_platform",
    "quota_query_mode",
    "request_timeout_seconds",
    "verify_tls",
    "timezone",
    "cost_basis",
    "initial_usd_per_percent",
    "safety_factor",
    "conservative_percentile",
    "rate_history_samples",
    "local_poll_minutes",
    "progress_threshold_percent",
    "active_max_calibration_hours",
    "reset_proximity_minutes",
    "stale_warning_hours",
    "limit_warning_usd",
    "recommendation_change_usd",
    "rate_change_alert_percent",
    "notify_on_limit_exhausted",
    "notify_on_recommendation_change",
    "notify_on_rate_change",
    "notify_on_collection_error",
    "notification_cooldown_minutes",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_use_tls",
    "smtp_use_ssl",
    "smtp_from_email",
    "notification_email",
}


def settings_data(config: AppSettings) -> dict:
    result = {field: getattr(config, field) for field in SETTINGS_FIELDS}
    for key, value in list(result.items()):
        if isinstance(value, Decimal):
            result[key] = float(value)
    result.update(
        {
            "sub2api_token_configured": bool(config.sub2api_admin_token_encrypted),
            "smtp_password_configured": bool(config.smtp_password_encrypted),
            "last_local_check_at": iso(config.last_local_check_at),
            "last_upstream_check_at": iso(config.last_upstream_check_at),
            "last_success_at": iso(config.last_success_at),
            "last_error": config.last_error,
        }
    )
    return result


@api_login_required
@require_http_methods(["GET", "PATCH"])
def settings_view(request):
    config = AppSettings.load()
    if request.method == "GET":
        return ok(settings_data(config))
    try:
        payload = body_json(request)
    except ValueError as exc:
        return error(str(exc))

    try:
        for field_name in SETTINGS_FIELDS:
            if field_name not in payload:
                continue
            model_field = config._meta.get_field(field_name)
            value = payload[field_name]
            # JSON 没有 Decimal 类型。先经由字符串转换，避免 0.95 这样的浮点数
            # 在 DecimalField 校验时暴露二进制浮点尾差。
            if isinstance(model_field, models.DecimalField) and value is not None:
                value = Decimal(str(value))
            else:
                value = model_field.to_python(value)
            setattr(config, field_name, value)
    except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
        details = exc.message_dict if isinstance(exc, ValidationError) and hasattr(exc, "message_dict") else None
        return error("设置字段格式无效", details=details)
    token = str(payload.get("sub2api_admin_token", ""))
    smtp_password = str(payload.get("smtp_password", ""))
    if token:
        config.sub2api_admin_token_encrypted = encrypt_secret(token)
    if payload.get("clear_sub2api_admin_token") is True:
        config.sub2api_admin_token_encrypted = ""
    if smtp_password:
        config.smtp_password_encrypted = encrypt_secret(smtp_password)
    if payload.get("clear_smtp_password") is True:
        config.smtp_password_encrypted = ""
    if config.smtp_use_ssl and config.smtp_use_tls:
        return error("SMTP SSL 与 STARTTLS 不能同时启用")
    try:
        config.full_clean()
    except ValidationError as exc:
        return error("设置校验失败", details=exc.message_dict)
    config.save()
    return ok(settings_data(config))


@api_login_required
@require_POST
def test_sub2api(request):
    config = AppSettings.load()
    try:
        with Sub2APIClient(config) as client:
            result = client.test_connection(config.openai_account_id, config.quota_query_mode)
    except (Sub2APIError, ValueError) as exc:
        return error(str(exc), 502)
    return ok(result)


@api_login_required
@require_POST
def test_email(request):
    config = AppSettings.load()
    event = send_notification(
        config=config,
        event_type="test",
        dedupe_key=f"test:{timezone.now().timestamp()}",
        subject="[拼车额度] 邮件配置测试",
        body="这是一封测试邮件。收到它说明 SMTP 配置可以正常工作。",
        severity="info",
        ignore_cooldown=True,
    )
    if event is None or event.status != "sent":
        return error(event.error if event else "邮件未发送", 502)
    return ok({"event_id": event.id})


def latest_snapshot(participant: Participant) -> ParticipantSnapshot | None:
    return participant.snapshots.select_related("observation", "observation__cycle").order_by("-observation__observed_at").first()


def participant_data(participant: Participant) -> dict:
    snapshot = latest_snapshot(participant)
    return {
        "id": participant.id,
        "name": participant.name,
        "email": participant.email,
        "sub2api_user_id": participant.sub2api_user_id,
        "share_percent": float(participant.share_percent),
        "is_owner": participant.is_owner,
        "enabled": participant.enabled,
        "notes": participant.notes,
        "latest_weekly_usage_usd": float(participant.latest_weekly_usage_usd) if participant.latest_weekly_usage_usd is not None else None,
        "latest_weekly_limit_usd": float(participant.latest_weekly_limit_usd) if participant.latest_weekly_limit_usd is not None else None,
        "latest_selected_cost": float(participant.latest_selected_cost) if participant.latest_selected_cost is not None else None,
        "last_checked_at": iso(participant.last_checked_at),
        "snapshot": snapshot_data(snapshot) if snapshot else None,
    }


def snapshot_data(snapshot: ParticipantSnapshot) -> dict:
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": snapshot.participant.name if hasattr(snapshot, "participant") else "",
        "selected_cost": float(snapshot.selected_cost),
        "delta_cost": float(snapshot.delta_cost) if snapshot.delta_cost is not None else None,
        "charged_delta_percent": float(snapshot.charged_delta_percent),
        "charged_cycle_percent": float(snapshot.charged_cycle_percent),
        "remaining_share_percent": float(snapshot.remaining_share_percent),
        "platform_weekly_usage_usd": float(snapshot.platform_weekly_usage_usd) if snapshot.platform_weekly_usage_usd is not None else None,
        "platform_weekly_limit_usd": float(snapshot.platform_weekly_limit_usd) if snapshot.platform_weekly_limit_usd is not None else None,
        "recommended_weekly_limit_usd": float(snapshot.recommended_weekly_limit_usd),
        "recommendation_difference_usd": float(snapshot.recommendation_difference_usd) if snapshot.recommendation_difference_usd is not None else None,
        "needs_manual_update": snapshot.needs_manual_update,
        "reason": snapshot.reason,
    }


def _validate_participant_share(instance: Participant | None, share: Decimal, enabled: bool) -> None:
    queryset = Participant.objects.filter(enabled=True)
    if instance:
        queryset = queryset.exclude(pk=instance.pk)
    total = sum((item.share_percent for item in queryset), Decimal("0"))
    if enabled and total + share > Decimal(100):
        raise ValueError(f"启用参与者的权益合计将达到 {total + share}%，不能超过 100%")


@api_login_required
@require_http_methods(["GET", "POST"])
def participants_view(request):
    if request.method == "GET":
        return ok([participant_data(item) for item in Participant.objects.all()])
    try:
        payload = body_json(request)
        share = Decimal(str(payload.get("share_percent")))
        enabled = bool(payload.get("enabled", True))
        _validate_participant_share(None, share, enabled)
        participant = Participant(
            name=str(payload.get("name", "")).strip(),
            email=str(payload.get("email", "")).strip(),
            sub2api_user_id=int(payload.get("sub2api_user_id")),
            share_percent=share,
            is_owner=bool(payload.get("is_owner", False)),
            enabled=enabled,
            notes=str(payload.get("notes", "")).strip(),
        )
        participant.full_clean()
        participant.save()
    except (ValueError, TypeError, InvalidOperation, ValidationError) as exc:
        details = exc.message_dict if isinstance(exc, ValidationError) else None
        return error(str(exc) if not details else "参与者校验失败", details=details)
    return ok(participant_data(participant), 201)


@api_login_required
@require_http_methods(["PUT", "DELETE"])
def participant_detail(request, participant_id: int):
    try:
        participant = Participant.objects.get(pk=participant_id)
    except Participant.DoesNotExist:
        return error("参与者不存在", 404)
    if request.method == "DELETE":
        if participant.snapshots.exists():
            return error("该参与者已有测算账本，不能删除；请改为停用", 409)
        participant.delete()
        return ok({"deleted": True})
    try:
        payload = body_json(request)
        share = Decimal(str(payload.get("share_percent", participant.share_percent)))
        enabled = bool(payload.get("enabled", participant.enabled))
        _validate_participant_share(participant, share, enabled)
        for field in ("name", "email", "sub2api_user_id", "is_owner", "enabled", "notes"):
            if field in payload:
                setattr(participant, field, payload[field])
        participant.share_percent = share
        participant.full_clean()
        participant.save()
    except (ValueError, TypeError, InvalidOperation, ValidationError) as exc:
        details = exc.message_dict if isinstance(exc, ValidationError) else None
        return error(str(exc) if not details else "参与者校验失败", details=details)
    return ok(participant_data(participant))


@api_login_required
@require_GET
def dashboard(request):
    config = AppSettings.load()
    cycle = QuotaCycle.objects.filter(active=True).first()
    observation = Observation.objects.filter(cycle=cycle).prefetch_related("participant_snapshots__participant").first() if cycle else None
    snapshots = list(observation.participant_snapshots.select_related("participant")) if observation else []
    total_charged = sum((item.charged_cycle_percent for item in snapshots), Decimal("0"))
    data = {
        "configured": bool(config.sub2api_admin_token_encrypted and config.openai_account_id),
        "monitoring_enabled": config.monitoring_enabled,
        "last_local_check_at": iso(config.last_local_check_at),
        "last_upstream_check_at": iso(config.last_upstream_check_at),
        "last_success_at": iso(config.last_success_at),
        "last_error": config.last_error,
        "quota_query_mode": config.quota_query_mode,
        "cycle": None,
        "participants": [participant_data(item) for item in Participant.objects.filter(enabled=True)],
        "needs_manual_update_count": sum(1 for item in snapshots if item.needs_manual_update),
    }
    if cycle:
        data["cycle"] = {
            "id": cycle.id,
            "starts_at": iso(cycle.starts_at),
            "resets_at": iso(cycle.resets_at),
            "upstream_used_percent": float(observation.upstream_used_percent) if observation else None,
            "effective_usd_per_percent": float(observation.effective_usd_per_percent) if observation else None,
            "selected_total_cost": float(observation.selected_total_cost) if observation else None,
            "unattributed_used_percent": float(max(Decimal("0"), observation.upstream_used_percent - total_charged)) if observation else None,
            "sample_note": observation.sample_note if observation else "",
            "snapshot_sampled_at": observation.raw_window.get("sampled_at") if observation else None,
        }
    return ok(data)


@api_login_required
@require_GET
def observations(request):
    try:
        limit = min(max(int(request.GET.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50
    rows = Observation.objects.select_related("cycle").prefetch_related("participant_snapshots__participant")[:limit]
    result = []
    for item in rows:
        result.append(
            {
                "id": item.id,
                "observed_at": iso(item.observed_at),
                "source": item.source,
                "cycle_id": item.cycle_id,
                "cycle_resets_at": iso(item.cycle.resets_at),
                "upstream_used_percent": float(item.upstream_used_percent),
                "selected_total_cost": float(item.selected_total_cost),
                "delta_percent": float(item.delta_percent) if item.delta_percent is not None else None,
                "delta_cost": float(item.delta_cost) if item.delta_cost is not None else None,
                "sample_usd_per_percent": float(item.sample_usd_per_percent) if item.sample_usd_per_percent is not None else None,
                "effective_usd_per_percent": float(item.effective_usd_per_percent),
                "valid_sample": item.valid_sample,
                "sample_note": item.sample_note,
                "query_mode": item.raw_window.get("query_mode", "direct"),
                "snapshot_sampled_at": item.raw_window.get("sampled_at"),
                "participants": [snapshot_data(snapshot) for snapshot in item.participant_snapshots.all()],
            }
        )
    return ok(result)


@api_login_required
@require_GET
def notifications(request):
    try:
        limit = min(max(int(request.GET.get("limit", 100)), 1), 300)
    except ValueError:
        limit = 100
    rows = NotificationEvent.objects.select_related("participant")[:limit]
    return ok(
        [
            {
                "id": item.id,
                "event_type": item.event_type,
                "event_type_label": item.get_event_type_display(),
                "severity": item.severity,
                "participant_name": item.participant.name if item.participant else None,
                "recipient": item.recipient,
                "subject": item.subject,
                "body": item.body,
                "status": item.status,
                "status_label": item.get_status_display(),
                "error": item.error,
                "created_at": iso(item.created_at),
                "sent_at": iso(item.sent_at),
            }
            for item in rows
        ]
    )


@api_login_required
@require_POST
def run_monitor_view(request):
    try:
        result = run_monitor(force_upstream=True, source="manual")
    except (Sub2APIError, ValueError) as exc:
        return error(str(exc), 502)
    except Exception as exc:
        return error(f"采集失败：{exc}", 500)
    return ok(result)
