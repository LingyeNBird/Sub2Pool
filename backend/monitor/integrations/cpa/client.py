"""Authenticated CLIProxyAPI management client."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from ...models import AppSettings
from ...secrets import decrypt_secret
from ..sub2api.dto import WeeklyWindow


class CPAError(RuntimeError):
    """User-displayable CPA error that never contains the management key."""


def management_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")
    if not path.endswith("/v0/management"):
        path = f"{path}/v0/management"
    return urlunparse(parsed._replace(path=path)) + "/"


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CPAError(f"CPA 返回了无效字段 {field}") from exc
    if not result.is_finite():
        raise CPAError(f"CPA 返回了无效字段 {field}")
    return result


class CPAClient:
    def __init__(
        self,
        config: AppSettings,
        *,
        base_url: str | None = None,
        management_key: str | None = None,
        request_timeout_seconds: int | None = None,
        verify_tls: bool | None = None,
    ):
        key = management_key or decrypt_secret(config.cpa_management_key_encrypted)
        if not key:
            raise CPAError("尚未配置 CPA Management Key")
        self.base_url = management_base_url(base_url or config.cpa_base_url)
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=request_timeout_seconds or config.request_timeout_seconds,
            verify=config.verify_tls if verify_tls is None else verify_tls,
            follow_redirects=False,
        )

    def __enter__(self) -> "CPAClient":
        return self

    def __exit__(self, *_args) -> None:
        self.client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.client.request(
                method,
                url,
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            raise CPAError(f"无法连接 CPA：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = str(payload.get("error") or payload.get("message") or "")
            except (ValueError, AttributeError):
                message = ""
            suffix = f"：{message}" if message else ""
            raise CPAError(f"CPA 返回 HTTP {response.status_code}{suffix}")
        try:
            return response.json()
        except ValueError as exc:
            raise CPAError("CPA 返回的不是 JSON") from exc

    def list_codex_accounts(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "auth-files")
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise CPAError("CPA auth-files 响应结构错误")
        accounts: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or item.get("type") or "").strip()
            auth_index = str(item.get("auth_index") or "").strip()
            if provider.lower() != "codex" or not auth_index:
                continue
            claims = item.get("id_token")
            if not isinstance(claims, dict):
                claims = {}
            email = str(item.get("email") or item.get("account") or "").strip()
            accounts.append(
                {
                    "auth_index": auth_index,
                    "name": str(item.get("label") or item.get("name") or email or auth_index),
                    "email": email,
                    "chatgpt_account_id": str(
                        claims.get("chatgpt_account_id")
                        or item.get("chatgpt_account_id")
                        or ""
                    ).strip(),
                    "plan_type": str(
                        claims.get("plan_type") or item.get("plan_type") or ""
                    ).strip(),
                    "status": str(item.get("status") or "").strip(),
                    "status_message": str(item.get("status_message") or "").strip(),
                    "disabled": bool(item.get("disabled")),
                    "unavailable": bool(item.get("unavailable")),
                    "success": int(item.get("success") or 0),
                    "failed": int(item.get("failed") or 0),
                    "recent_requests": item.get("recent_requests") or [],
                }
            )
        return accounts

    def get_codex_account(self, auth_index: str) -> dict[str, Any]:
        for account in self.list_codex_accounts():
            if account["auth_index"] == auth_index:
                return account
        raise CPAError("CPA 中未找到该 Codex 账号")

    def query_weekly_window(self, auth_index: str) -> WeeklyWindow:
        account = self.get_codex_account(auth_index)
        chatgpt_account_id = account["chatgpt_account_id"]
        if not chatgpt_account_id:
            raise CPAError("CPA Codex 账号缺少 ChatGPT Account ID")
        payload = self._request(
            "POST",
            "api-call",
            json_body={
                "auth_index": auth_index,
                "method": "GET",
                "url": "https://chatgpt.com/backend-api/wham/usage",
                "header": {
                    "Authorization": "Bearer $TOKEN$",
                    "Chatgpt-Account-Id": chatgpt_account_id,
                },
            },
        )
        if not isinstance(payload, dict):
            raise CPAError("CPA api-call 响应结构错误")
        upstream_status = int(payload.get("status_code") or 0)
        if upstream_status >= 400 or upstream_status <= 0:
            raise CPAError(f"CPA 上游额度接口返回 HTTP {upstream_status or '未知'}")
        body = payload.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as exc:
                raise CPAError("CPA 上游额度接口返回的不是 JSON") from exc
        if not isinstance(body, dict):
            raise CPAError("CPA 上游额度响应结构错误")
        rate_limit = body.get("rate_limit")
        if not isinstance(rate_limit, dict):
            raise CPAError("CPA Codex 账号没有可用的 rate_limit 数据")
        candidates: list[WeeklyWindow] = []
        for slot in ("primary_window", "secondary_window"):
            item = rate_limit.get(slot)
            if not isinstance(item, dict):
                continue
            seconds = int(item.get("limit_window_seconds") or 0)
            candidates.append(
                WeeklyWindow(
                    used_percent=_decimal(item.get("used_percent"), f"{slot}.used_percent"),
                    window_seconds=seconds,
                    reset_after_seconds=int(item.get("reset_after_seconds") or 0),
                    reset_at=int(item.get("reset_at") or 0),
                    slot=slot,
                    sampled_at=datetime.now(tz=timezone.utc).isoformat(),
                    plan_type=account["plan_type"] or None,
                )
            )
        if not candidates:
            raise CPAError("CPA Codex 账号没有主窗口或次窗口数据")
        weekly = min(candidates, key=lambda item: abs(item.window_seconds - 604800))
        if abs(weekly.window_seconds - 604800) > 86400:
            raise CPAError(f"未找到七天窗口，最接近的窗口为 {weekly.window_seconds} 秒")
        if weekly.reset_at <= 0:
            raise CPAError("CPA 七天窗口缺少 reset_at")
        return weekly


    def test_connection(self) -> dict[str, Any]:
        accounts = self.list_codex_accounts()
        usage = self._request("GET", "usage-statistics-enabled")
        enabled = (
            bool(usage.get("usage-statistics-enabled"))
            if isinstance(usage, dict)
            else False
        )
        if not enabled:
            raise CPAError("CPA usage statistics 未启用")
        return {
            "auth_files_api": "ok",
            "codex_account_count": len(accounts),
            "usage_statistics_enabled": True,
        }
