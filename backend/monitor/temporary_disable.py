"""Temporary upstream disables with an explicit, auditable restore deadline.

管理员可以临时禁用整个 Sub2API 账号的调度，或把某个模型从账号模型白名单里去掉；
每条记录都带恢复时刻与恢复目标，后台轮询到点后写回上游。所有写操作都在账号租约
内进行，失败时留下未确认写入而不是静默丢弃。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from .integrations.sub2api import Sub2APIClient, Sub2APIError
from .models import (
    ACCOUNT_SCOPE,
    MODEL_SCOPE,
    AccountTemporaryDisable,
    AppSettings,
    MonitoredAccount,
)

MIN_DISABLE_MINUTES = 1
MAX_DISABLE_MINUTES = 60 * 24 * 30
RESTORE_RETRY_SECONDS = 300
RESTORE_BATCH_SIZE = 5
DISABLE_PENDING_ERROR = "上游禁用尚未确认：写入中断或失败，请核对后重试。"
RESTORE_PENDING_ERROR = "上游恢复尚未确认：写入中断或失败，请核对后重试。"


class DisableNotFound(ValueError):
    """The referenced journal row does not exist."""


def normalize_minutes(value: Any) -> int:
    """Validate one administrator-entered duration in minutes."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("禁用时长必须是整数分钟")
    if not MIN_DISABLE_MINUTES <= value <= MAX_DISABLE_MINUTES:
        raise ValueError(
            f"禁用时长必须在 {MIN_DISABLE_MINUTES} 分钟到 "
            f"{MAX_DISABLE_MINUTES // (60 * 24)} 天之间"
        )
    return value


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def disable_payload(
    disable: AccountTemporaryDisable,
    *,
    include_actor: bool = True,
) -> dict[str, Any]:
    return {
        "id": disable.id,
        "account_id": disable.account_id,
        "scope": disable.scope,
        "model": disable.model,
        "started_at": _isoformat(disable.started_at),
        "restore_at": _isoformat(disable.restore_at),
        "retry_at": _isoformat(disable.retry_at),
        "restored_at": _isoformat(disable.restored_at),
        "restore_source": disable.restore_source,
        "created_by": (
            disable.created_by.get_username()
            if include_actor and disable.created_by_id
            else ""
        ),
        "last_error": disable.last_error,
    }


def disables_by_account(
    accounts: Iterable[MonitoredAccount],
    *,
    include_actor: bool = True,
) -> dict[int, list[dict[str, Any]]]:
    """Active disables grouped by monitored account for the status page."""
    account_ids = [account.pk for account in accounts]
    if not account_ids:
        return {}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rows = (
        AccountTemporaryDisable.objects.filter(
            account_id__in=account_ids,
            restored_at__isnull=True,
        )
        .select_related("created_by")
        .order_by("started_at", "id")
    )
    for row in rows:
        grouped[row.account_id].append(
            disable_payload(row, include_actor=include_actor)
        )
    return dict(grouped)


def _sub2api_account(account_id: int) -> MonitoredAccount:
    if (
        isinstance(account_id, bool)
        or not isinstance(account_id, int)
        or account_id <= 0
    ):
        raise ValueError("监控账号不存在")
    try:
        account = MonitoredAccount.objects.get(pk=account_id)
    except MonitoredAccount.DoesNotExist as exc:
        raise ValueError("监控账号不存在") from exc
    if account.provider != "sub2api" or account.external_account_id is None:
        raise ValueError("临时禁用目前只支持 Sub2API 上游账号")
    return account


def _require_connection(config: AppSettings) -> None:
    if not config.sub2api_admin_token_encrypted:
        raise ValueError("请先配置 Sub2API Admin Token")


def _whitelist_state(
    client: Sub2APIClient,
    account: MonitoredAccount,
) -> tuple[list[str], dict[str, str]]:
    """Return the model names this account serves now and their upstream mapping.

    账号已配置白名单时，白名单键就是全部可服务模型；未配置白名单时 Sub2API
    按上游目录服务全部模型，此时用上游目录作为可禁用集合。
    """
    configuration = client.account_configuration(account.external_account_id)
    raw = configuration["credentials"].get("model_mapping")
    mapping = (
        {str(key): str(value) for key, value in raw.items()}
        if isinstance(raw, dict)
        else {}
    )
    if mapping:
        return sorted(mapping), mapping
    return client.account_models(account.external_account_id), {}


def disablable_models(account_id: int) -> list[str]:
    """Public model names the disable dialog may offer for one account."""
    account = _sub2api_account(account_id)
    config = AppSettings.load()
    _require_connection(config)
    with Sub2APIClient(config) as client:
        models, _mapping = _whitelist_state(client, account)
    return models


def _write_disable(
    client: Sub2APIClient,
    account: MonitoredAccount,
    disable: AccountTemporaryDisable,
) -> None:
    upstream_id = account.external_account_id
    if disable.scope == ACCOUNT_SCOPE:
        client.set_account_schedulable(upstream_id, False)
        return
    mapping = disable.restore_context.get("model_mapping") or {}
    models = sorted(mapping) if mapping else _whitelist_state(client, account)[0]
    if disable.model not in models:
        raise ValueError(f"该账号当前的白名单中没有模型 {disable.model}")
    whitelist = {
        name: mapping.get(name, name)
        for name in models
        if name != disable.model
    }
    if not whitelist:
        raise ValueError("禁用后模型白名单为空，请改用禁用整个账号")
    client.set_account_model_whitelist(upstream_id, whitelist)


def _write_restore(
    client: Sub2APIClient,
    account: MonitoredAccount,
    disable: AccountTemporaryDisable,
) -> None:
    upstream_id = account.external_account_id
    if disable.scope == ACCOUNT_SCOPE:
        client.set_account_schedulable(
            upstream_id,
            bool(disable.restore_context.get("schedulable")),
        )
        return
    mapping = disable.restore_context.get("model_mapping")
    client.set_account_model_whitelist(
        upstream_id,
        None
        if mapping is None
        else {str(key): str(value) for key, value in mapping.items()},
    )


def _create_row(
    *,
    account: MonitoredAccount,
    scope: str,
    model: str,
    restore_context: dict[str, Any],
    minutes: int,
    user,
    now: datetime,
) -> AccountTemporaryDisable:
    try:
        with transaction.atomic():
            return AccountTemporaryDisable.objects.create(
                account=account,
                scope=scope,
                model=model,
                restore_context=restore_context,
                created_by=(
                    user
                    if getattr(user, "is_authenticated", False) and user.pk
                    else None
                ),
                started_at=now,
                restore_at=now + timedelta(minutes=minutes),
                last_error=DISABLE_PENDING_ERROR,
            )
    except IntegrityError as exc:
        raise ValueError("该账号已有一条同类临时禁用，请先调整或提前恢复") from exc


def _apply_write(
    disable: AccountTemporaryDisable,
    account: MonitoredAccount,
    config: AppSettings,
    guard: LeaseGuard,
) -> None:
    """Confirm the recorded disable upstream; the row already exists either way."""
    disable.last_error = DISABLE_PENDING_ERROR
    disable.save(update_fields=["last_error", "updated_at"])
    guard.renew()
    with Sub2APIClient(config) as client:
        _write_disable(client, account, disable)
    disable.last_error = ""
    disable.save(update_fields=["last_error", "updated_at"])


def create_disable(
    *,
    account_id: int,
    scope: str,
    model: str,
    minutes: int,
    user,
) -> AccountTemporaryDisable:
    """Apply one upstream disable and journal it with its restore deadline."""
    minutes = normalize_minutes(minutes)
    scope = str(scope or "")
    model = str(model or "").strip()
    if scope not in {ACCOUNT_SCOPE, MODEL_SCOPE}:
        raise ValueError("请选择禁用整个账号或禁用某个模型")
    if scope == MODEL_SCOPE and not model:
        raise ValueError("请选择要禁用的模型")
    if scope == ACCOUNT_SCOPE:
        model = ""
    account = _sub2api_account(account_id)
    config = AppSettings.load()
    _require_connection(config)
    if AccountTemporaryDisable.objects.filter(
        account=account,
        scope=scope,
        restored_at__isnull=True,
    ).exists():
        raise ValueError("该账号已有一条同类临时禁用，请先调整或提前恢复")

    guard = LeaseGuard.acquire(account.fact_key)
    try:
        now = timezone.now()
        with Sub2APIClient(config) as client:
            if scope == ACCOUNT_SCOPE:
                runtime = client.account_runtime_status(account.external_account_id)
                restore_context: dict[str, Any] = {
                    "schedulable": bool(runtime["schedulable"]),
                }
            else:
                models, mapping = _whitelist_state(client, account)
                if model not in models:
                    raise ValueError(f"该账号当前的白名单中没有模型 {model}")
                restore_context = {"model_mapping": dict(mapping) or None}
            disable = _create_row(
                account=account,
                scope=scope,
                model=model,
                restore_context=restore_context,
                minutes=minutes,
                user=user,
                now=now,
            )
            try:
                _apply_write(disable, account, config, guard)
            except (Sub2APIError, ValueError, LeaseLostError) as exc:
                disable.refresh_from_db()
                disable.last_error = f"{DISABLE_PENDING_ERROR} {exc}"
                disable.save(update_fields=["last_error", "updated_at"])
                raise
        return disable
    finally:
        guard.release()


def update_disable(*, disable_id: int, minutes: int) -> AccountTemporaryDisable:
    """Move one disable's restore deadline; also confirms an unapplied disable."""
    minutes = normalize_minutes(minutes)
    disable = _load_disable(disable_id)
    account = _sub2api_account(disable.account_id)
    config = AppSettings.load()
    guard = LeaseGuard.acquire(account.fact_key)
    try:
        disable.refresh_from_db()
        if not disable.is_active:
            raise ValueError("该临时禁用已经恢复")
        if disable.last_error and disable.last_error.startswith(
            DISABLE_PENDING_ERROR
        ):
            _apply_write(disable, account, config, guard)
        disable.restore_at = timezone.now() + timedelta(minutes=minutes)
        disable.retry_at = None
        disable.save(update_fields=["restore_at", "retry_at", "updated_at"])
        return disable
    finally:
        guard.release()


def _load_disable(disable_id: int) -> AccountTemporaryDisable:
    try:
        return AccountTemporaryDisable.objects.select_related(
            "account",
            "created_by",
        ).get(pk=disable_id)
    except AccountTemporaryDisable.DoesNotExist as exc:
        raise DisableNotFound("临时禁用记录不存在") from exc


def _apply_restore(
    disable: AccountTemporaryDisable,
    account: MonitoredAccount,
    config: AppSettings,
    guard: LeaseGuard,
    source: str,
) -> AccountTemporaryDisable:
    disable.last_error = RESTORE_PENDING_ERROR
    disable.retry_at = None
    disable.save(update_fields=["last_error", "retry_at", "updated_at"])
    guard.renew()
    with Sub2APIClient(config) as client:
        _write_restore(client, account, disable)
    disable.restored_at = timezone.now()
    disable.restore_source = source
    disable.last_error = ""
    disable.save(
        update_fields=[
            "restored_at",
            "restore_source",
            "last_error",
            "retry_at",
            "updated_at",
        ]
    )
    return disable


def restore_disable(*, disable_id: int, source: str = "manual") -> AccountTemporaryDisable:
    """Restore one disable now, before its deadline."""
    if source != "manual":
        raise ValueError("未知的恢复来源")
    disable = _load_disable(disable_id)
    account = _sub2api_account(disable.account_id)
    config = AppSettings.load()
    _require_connection(config)
    guard = LeaseGuard.acquire(account.fact_key)
    try:
        disable.refresh_from_db()
        if not disable.is_active:
            raise ValueError("该临时禁用已经恢复")
        return _apply_restore(disable, account, config, guard, "manual")
    finally:
        guard.release()


def _record_restore_failure(
    disable: AccountTemporaryDisable,
    now: datetime,
    exc: Exception,
) -> None:
    disable.last_error = f"自动恢复失败：{exc}"
    disable.retry_at = now + timedelta(seconds=RESTORE_RETRY_SECONDS)
    disable.save(update_fields=["last_error", "retry_at", "updated_at"])


def restore_due_disables(*, now: datetime | None = None) -> dict[str, int]:
    """Restore every disable whose deadline passed; safe inside the poll loop."""
    current = now or timezone.now()
    due = list(
        AccountTemporaryDisable.objects.filter(
            restored_at__isnull=True,
            restore_at__lte=current,
        )
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=current))
        .select_related("account")
        .order_by("restore_at", "id")[:RESTORE_BATCH_SIZE]
    )
    result = {"restored": 0, "failed": 0}
    if not due:
        return result
    config = AppSettings.load()
    if not config.sub2api_admin_token_encrypted:
        return result
    for disable in due:
        try:
            account = _sub2api_account(disable.account_id)
        except ValueError:
            continue
        try:
            guard = LeaseGuard.acquire(account.fact_key)
        except LeaseBusyError:
            continue
        try:
            disable.refresh_from_db()
            if not disable.is_active:
                continue
            _apply_restore(disable, account, config, guard, "auto")
        except Exception as exc:  # noqa: BLE001 - 单条记录失败不得中断轮询
            _record_restore_failure(disable, current, exc)
            result["failed"] += 1
        else:
            result["restored"] += 1
        finally:
            guard.release()
    return result
