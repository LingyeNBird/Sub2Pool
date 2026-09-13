"""OpenAI upstream account discovery and status resources."""
from math import isfinite
from typing import Any
from urllib.parse import urljoin

import httpx

from .dto import Sub2APIError, _decimal, _timestamp

OPENAI_PLATFORM = "openai"

def _optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if value == "" or isinstance(value, bool):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {key}")
    parsed = _decimal(value, key)
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise Sub2APIError(f"Sub2API 返回了无效字段 {key}")
    return int(parsed)


def _optional_float(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    parsed = _decimal(value, key)
    converted = float(parsed)
    if not parsed.is_finite() or not isfinite(converted):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {key}")
    return converted


def _optional_bool(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {key}")
    return value


def _optional_text(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {key}")
    return value


def _optional_timestamp(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return None if value in (None, "") else _timestamp(value, key).isoformat()


def _usage_window(data: Any, field: str) -> dict[str, Any] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}")
    stats = data.get("window_stats")
    if stats is not None and not isinstance(stats, dict):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}.window_stats")
    return {
        "used_percent": _optional_float(data, "utilization"),
        "reset_at": _optional_timestamp(data, "resets_at"),
        "remaining_seconds": _optional_int(data, "remaining_seconds"),
        "request_count": _optional_int(stats, "requests") if stats else None,
        "token_count": _optional_int(stats, "tokens") if stats else None,
        "account_cost_usd": _optional_float(stats, "cost") if stats else None,
        "standard_cost_usd": (
            _optional_float(stats, "standard_cost") if stats else None
        ),
        "user_cost_usd": _optional_float(stats, "user_cost") if stats else None,
    }


def _today_stats(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise Sub2APIError("Sub2API 返回了无效字段 summary.today")
    return {
        "date": _optional_text(data, "date"),
        "account_cost_usd": _optional_float(data, "cost"),
        "user_cost_usd": _optional_float(data, "user_cost"),
        "request_count": _optional_int(data, "requests"),
        "token_count": _optional_int(data, "tokens"),
    }


class AccountResourceMixin:
    def list_openai_accounts(self) -> list[dict[str, Any]]:
        """分页读取 Sub2API 中的 OpenAI 上游账号，只返回下拉框所需的非敏感字段。"""
        accounts: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "api/v1/admin/accounts",
                params={
                    "page": page,
                    "page_size": 100,
                    "platform": OPENAI_PLATFORM,
                    "sort_by": "name",
                    "sort_order": "asc",
                    "lite": "true",
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise Sub2APIError("OpenAI 账号列表响应结构错误")

            for raw in data["items"]:
                if not isinstance(raw, dict) or raw.get("platform") != OPENAI_PLATFORM:
                    continue
                try:
                    account_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                if account_id <= 0:
                    continue
                accounts.append(
                    {
                        "id": account_id,
                        "name": str(raw.get("name") or f"OpenAI 账号 {account_id}"),
                        "type": str(raw.get("type") or ""),
                        "status": str(raw.get("status") or ""),
                        "schedulable": bool(raw.get("schedulable")),
                    }
                )

            try:
                pages = max(1, int(data.get("pages") or 1))
            except (TypeError, ValueError):
                raise Sub2APIError("OpenAI 账号列表分页字段无效")
            if page >= pages:
                break
            page += 1
            if page > 100:
                raise Sub2APIError("OpenAI 账号数量异常，已停止读取")
        return accounts

    def account_runtime_status(self, account_id: int) -> dict[str, Any]:
        """Read non-sensitive account configuration and live scheduler state."""
        data = self._get(f"api/v1/admin/accounts/{account_id}")
        if not isinstance(data, dict):
            raise Sub2APIError("OpenAI 账号详情响应结构错误")
        if data.get("platform") != OPENAI_PLATFORM:
            raise Sub2APIError("配置的账号不是 OpenAI 账号")
        response_id = _optional_int(data, "id")
        if response_id is not None and response_id != account_id:
            raise Sub2APIError("Sub2API 返回了不匹配的账号")
        return {
            "name": _optional_text(data, "name"),
            "account_type": _optional_text(data, "type"),
            "status": _optional_text(data, "status"),
            "schedulable": _optional_bool(data, "schedulable"),
            "current_concurrency": _optional_int(data, "current_concurrency"),
            "concurrency_limit": _optional_int(data, "concurrency"),
            "last_used_at": _optional_timestamp(data, "last_used_at"),
            "rate_limited_at": _optional_timestamp(data, "rate_limited_at"),
            "rate_limit_reset_at": _optional_timestamp(
                data,
                "rate_limit_reset_at",
            ),
            "overload_until": _optional_timestamp(data, "overload_until"),
            "temp_unschedulable_until": _optional_timestamp(
                data,
                "temp_unschedulable_until",
            ),
            "temp_unschedulable_reason": _optional_text(
                data,
                "temp_unschedulable_reason",
            ),
            "error_message": _optional_text(data, "error_message"),
        }

    def account_models(self, account_id: int) -> list[str]:
        """List the public model names Sub2API currently offers for one account."""
        data = self._get(f"api/v1/admin/accounts/{account_id}/models")
        if not isinstance(data, list):
            raise Sub2APIError("OpenAI 账号模型列表响应结构错误")
        models: list[str] = []
        seen: set[str] = set()
        for item in data:
            name = None
            if isinstance(item, dict):
                name = item.get("id") or item.get("display_name")
            elif isinstance(item, str):
                name = item
            if not isinstance(name, str) or not name.strip():
                raise Sub2APIError("OpenAI 账号模型列表包含无效模型名")
            name = name.strip()
            if name not in seen:
                seen.add(name)
                models.append(name)
        return sorted(models)

    def account_configuration(self, account_id: int) -> dict[str, Any]:
        """Read the redacted credentials needed to write a model whitelist back."""
        data = self._get(f"api/v1/admin/accounts/{account_id}")
        if not isinstance(data, dict):
            raise Sub2APIError("OpenAI 账号详情响应结构错误")
        if data.get("platform") != OPENAI_PLATFORM:
            raise Sub2APIError("配置的账号不是 OpenAI 账号")
        response_id = _optional_int(data, "id")
        if response_id is not None and response_id != account_id:
            raise Sub2APIError("Sub2API 返回了不匹配的账号")
        credentials = data.get("credentials")
        if credentials is not None and not isinstance(credentials, dict):
            raise Sub2APIError("Sub2API 返回了无效的账号凭据")
        return {
            "name": _optional_text(data, "name") or "",
            "schedulable": _optional_bool(data, "schedulable"),
            "credentials": credentials or {},
        }

    def set_account_schedulable(
        self,
        account_id: int,
        schedulable: bool,
    ) -> dict[str, Any]:
        """Enable or disable scheduler selection for one Sub2API account."""
        if type(schedulable) is not bool:
            raise Sub2APIError("调度开关必须是布尔值")
        url = urljoin(self.base_url, f"api/v1/admin/accounts/{account_id}/schedulable")
        try:
            response = self.client.post(url, json={"schedulable": schedulable})
        except httpx.HTTPError as exc:
            raise Sub2APIError(
                f"无法连接 Sub2API：{exc.__class__.__name__}"
            ) from exc
        data = self._response_data(response)
        if not isinstance(data, dict):
            raise Sub2APIError(f"账号 {account_id} 的调度更新响应结构错误")
        returned = _optional_bool(data, "schedulable")
        if returned is None or returned != schedulable:
            raise Sub2APIError(f"账号 {account_id} 的调度状态读回不一致")
        return data

    def set_account_model_whitelist(
        self,
        account_id: int,
        model_mapping: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Replace the account-level model whitelist; ``None`` restores no whitelist.

        非敏感凭据由本次请求整体决定（Sub2API 只保留未提供的敏感子键），
        因此先读回脱敏凭据再整体回写，避免清空 base_url 等配置。
        """
        if model_mapping is not None and not isinstance(model_mapping, dict):
            raise Sub2APIError("模型白名单必须是映射")
        mapping: dict[str, str] = {}
        for key, value in (model_mapping or {}).items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(value, str)
                or not value.strip()
            ):
                raise Sub2APIError("模型白名单包含无效条目")
            mapping[key.strip()] = value.strip()
        current = self.account_configuration(account_id)
        credentials = {
            str(key): value
            for key, value in current["credentials"].items()
            if key != "model_mapping"
        }
        if mapping:
            credentials["model_mapping"] = mapping
        url = urljoin(self.base_url, f"api/v1/admin/accounts/{account_id}")
        payload: dict[str, Any] = {"credentials": credentials}
        if current["name"]:
            payload["name"] = current["name"]
        try:
            response = self.client.put(url, json=payload)
        except httpx.HTTPError as exc:
            raise Sub2APIError(
                f"无法连接 Sub2API：{exc.__class__.__name__}"
            ) from exc
        data = self._response_data(response)
        if not isinstance(data, dict):
            raise Sub2APIError(f"账号 {account_id} 的白名单更新响应结构错误")
        actual = data.get("credentials")
        actual_mapping = actual.get("model_mapping") if isinstance(actual, dict) else None
        if isinstance(actual_mapping, dict) or mapping:
            normalized = {
                str(key): str(value)
                for key, value in (
                    actual_mapping.items() if isinstance(actual_mapping, dict) else []
                )
            }
            if normalized != mapping:
                raise Sub2APIError(f"账号 {account_id} 的模型白名单读回不一致")
        return data

    def account_usage_status(
        self,
        account_id: int,
        *,
        source: str = "passive",
    ) -> dict[str, Any]:
        """Read Sub2API's account usage snapshot without exposing credentials."""
        if source not in {"passive", "active"}:
            raise Sub2APIError(f"未知账号状态查询模式：{source}")
        data = self._get(
            f"api/v1/admin/accounts/{account_id}/usage",
            params={"source": source},
        )
        if not isinstance(data, dict):
            raise Sub2APIError("账号额度状态响应结构错误")
        return {
            "source": _optional_text(data, "source"),
            "updated_at": _optional_timestamp(data, "updated_at"),
            "five_hour": _usage_window(data.get("five_hour"), "five_hour"),
            "seven_day": _usage_window(data.get("seven_day"), "seven_day"),
            "needs_verify": _optional_bool(data, "needs_verify"),
            "is_banned": _optional_bool(data, "is_banned"),
            "needs_reauth": _optional_bool(data, "needs_reauth"),
            "error_code": _optional_text(data, "error_code"),
            "error": _optional_text(data, "error"),
        }

    def account_usage_stats(
        self,
        account_id: int,
        *,
        days: int = 30,
    ) -> dict[str, Any]:
        """Read the account's local request, token, cost, and latency totals."""
        if not 1 <= days <= 90:
            raise Sub2APIError("账号统计天数必须在 1 到 90 之间")
        data = self._get(
            f"api/v1/admin/accounts/{account_id}/stats",
            params={"days": days},
        )
        summary = data.get("summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict):
            raise Sub2APIError("账号用量统计响应结构错误")
        history = data.get("history")
        if history is not None and not isinstance(history, list):
            raise Sub2APIError("账号用量统计历史响应结构错误")
        actual_days_used = (
            len(history)
            if isinstance(history, list)
            else _optional_int(summary, "actual_days_used")
        )
        return {
            "days": days,
            "actual_days_used": actual_days_used,
            "account_cost_usd": _optional_float(summary, "total_cost"),
            "standard_cost_usd": _optional_float(
                summary,
                "total_standard_cost",
            ),
            "user_cost_usd": _optional_float(summary, "total_user_cost"),
            "request_count": _optional_int(summary, "total_requests"),
            "token_count": _optional_int(summary, "total_tokens"),
            "avg_daily_cost_usd": _optional_float(summary, "avg_daily_cost"),
            "avg_daily_request_count": _optional_float(
                summary,
                "avg_daily_requests",
            ),
            "avg_daily_token_count": _optional_float(
                summary,
                "avg_daily_tokens",
            ),
            "avg_duration_ms": _optional_float(summary, "avg_duration_ms"),
            "today": _today_stats(summary.get("today")),
        }
