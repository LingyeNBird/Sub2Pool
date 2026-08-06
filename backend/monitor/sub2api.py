"""Sub2API Admin API 的只读客户端。

本模块只暴露 GET 方法，刻意不实现 PUT/POST，保证本服务不会自动修改用户额度。
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx

from .models import AppSettings
from .secrets import decrypt_secret

OPENAI_PLATFORM = "openai"


class Sub2APIError(RuntimeError):
    """对用户可展示、且不包含 Admin Token 的上游错误。"""


@dataclass(frozen=True)
class WeeklyWindow:
    used_percent: Decimal
    window_seconds: int
    reset_after_seconds: int
    reset_at: int
    slot: str
    sampled_at: str | None = None


@dataclass(frozen=True)
class UsageStats:
    total_cost: Decimal
    total_actual_cost: Decimal

    def selected(self, basis: str) -> Decimal:
        return self.total_actual_cost if basis == "actual" else self.total_cost


@dataclass(frozen=True)
class PlatformQuota:
    weekly_usage_usd: Decimal
    weekly_limit_usd: Decimal | None
    weekly_window_start: str | None
    weekly_window_resets_at: str | None


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}") from exc


class Sub2APIClient:
    def __init__(
        self,
        config: AppSettings,
        *,
        base_url: str | None = None,
        admin_token: str | None = None,
        request_timeout_seconds: int | None = None,
        verify_tls: bool | None = None,
    ):
        # 设置页允许用尚未保存的地址和 Token 发起测试；空 Token 则安全地回退到已保存密文。
        token = admin_token or decrypt_secret(config.sub2api_admin_token_encrypted)
        if not token:
            raise Sub2APIError("尚未配置 Sub2API Admin Token")
        self.base_url = (base_url or config.sub2api_base_url).rstrip("/") + "/"
        self.client = httpx.Client(
            headers={"x-api-key": token, "Accept": "application/json"},
            timeout=request_timeout_seconds or config.request_timeout_seconds,
            verify=config.verify_tls if verify_tls is None else verify_tls,
            follow_redirects=False,
        )

    def __enter__(self) -> "Sub2APIClient":
        return self

    def __exit__(self, *_args) -> None:
        self.client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # urljoin 会处理用户在设置页中是否填写末尾斜杠，但 path 必须保持相对形式。
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise Sub2APIError(f"无法连接 Sub2API：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = ""
            suffix = f"：{message}" if message else ""
            raise Sub2APIError(f"Sub2API 返回 HTTP {response.status_code}{suffix}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise Sub2APIError("Sub2API 返回的不是 JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            message = payload.get("message", "未知错误") if isinstance(payload, dict) else "响应结构错误"
            raise Sub2APIError(f"Sub2API 请求失败：{message}")
        return payload.get("data")

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

    def query_weekly_window(self, account_id: int, mode: str = "passive") -> WeeklyWindow:
        """读取七天窗口。

        passive 只读取 Sub2API 已由真实转发请求写入账号 Extra 的快照；direct 才会调用
        /openai/accounts/:id/quota，后者会访问上游官方接口。
        """
        if mode == "passive":
            return self._query_passive_weekly_window(account_id)
        if mode != "direct":
            raise Sub2APIError(f"未知额度查询模式：{mode}")
        return self._query_direct_weekly_window(account_id)

    def _query_passive_weekly_window(self, account_id: int) -> WeeklyWindow:
        data = self._get(f"api/v1/admin/accounts/{account_id}")
        if not isinstance(data, dict):
            raise Sub2APIError("OpenAI 账号详情响应结构错误")
        if data.get("platform") != "openai":
            raise Sub2APIError("配置的账号不是 OpenAI 账号")
        extra = data.get("extra")
        if not isinstance(extra, dict) or "codex_7d_used_percent" not in extra:
            raise Sub2APIError("Sub2API 尚无该账号的被动七天快照；请先通过该账号产生一次真实请求")

        sampled_at = str(extra.get("codex_usage_updated_at") or "") or None
        reset_at = self._parse_passive_reset_at(extra, sampled_at)
        window_minutes = int(extra.get("codex_7d_window_minutes") or 10080)
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        if reset_at <= now_epoch:
            raise Sub2APIError("Sub2API 中的被动七天快照已过期；等待下一次真实请求刷新后再测算")
        return WeeklyWindow(
            used_percent=_decimal(extra.get("codex_7d_used_percent"), "extra.codex_7d_used_percent"),
            window_seconds=window_minutes * 60,
            reset_after_seconds=max(0, reset_at - now_epoch),
            reset_at=reset_at,
            slot="passive_snapshot",
            sampled_at=sampled_at,
        )

    @staticmethod
    def _parse_passive_reset_at(extra: dict[str, Any], sampled_at: str | None) -> int:
        raw = extra.get("codex_7d_reset_at")
        if raw:
            try:
                if isinstance(raw, (int, float)):
                    return int(raw)
                return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                pass
        reset_after = int(extra.get("codex_7d_reset_after_seconds") or 0)
        if reset_after > 0 and sampled_at:
            try:
                sampled = datetime.fromisoformat(sampled_at.replace("Z", "+00:00"))
                return int(sampled.timestamp()) + reset_after
            except ValueError:
                pass
        raise Sub2APIError("Sub2API 被动快照缺少有效的七天重置时间")

    def _query_direct_weekly_window(self, account_id: int) -> WeeklyWindow:
        data = self._get(f"api/v1/admin/openai/accounts/{account_id}/quota")
        rate_limit = (data or {}).get("rate_limit") if isinstance(data, dict) else None
        if not isinstance(rate_limit, dict):
            raise Sub2APIError("OpenAI 账号没有可用的 rate_limit 数据")
        fetched_at = (data or {}).get("fetched_at") if isinstance(data, dict) else None
        sampled_at = None
        if fetched_at:
            try:
                sampled_at = datetime.fromtimestamp(int(fetched_at), tz=timezone.utc).isoformat()
            except (ValueError, TypeError, OSError):
                sampled_at = None

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
                    sampled_at=sampled_at,
                )
            )
        if not candidates:
            raise Sub2APIError("OpenAI 账号没有主窗口或次窗口数据")
        weekly = min(candidates, key=lambda item: abs(item.window_seconds - 604800))
        if abs(weekly.window_seconds - 604800) > 86400:
            raise Sub2APIError(f"未找到七天窗口，最接近的窗口为 {weekly.window_seconds} 秒")
        if weekly.reset_at <= 0:
            raise Sub2APIError("七天窗口缺少 reset_at")
        return weekly

    def usage_stats(
        self,
        *,
        account_id: int,
        start_date: date,
        end_date: date,
        timezone_name: str,
        user_id: int | None = None,
    ) -> UsageStats:
        params: dict[str, Any] = {
            "account_id": account_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": timezone_name,
            "nocache": "true",
        }
        if user_id is not None:
            params["user_id"] = user_id
        data = self._get("api/v1/admin/usage/stats", params=params)
        if not isinstance(data, dict):
            raise Sub2APIError("用量统计响应结构错误")
        return UsageStats(
            total_cost=_decimal(data.get("total_cost"), "total_cost"),
            total_actual_cost=_decimal(data.get("total_actual_cost"), "total_actual_cost"),
        )

    def platform_quota(self, user_id: int, platform: str) -> PlatformQuota:
        data = self._get(f"api/v1/admin/users/{user_id}/platform-quotas")
        items = (data or {}).get("platform_quotas") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise Sub2APIError(f"用户 {user_id} 的平台额度响应结构错误")
        item = next((row for row in items if isinstance(row, dict) and row.get("platform") == platform), None)
        if item is None:
            # 没有记录代表该平台无限额。用量也没有可读取的独立计数，按 0 展示并提示配置。
            return PlatformQuota(Decimal("0"), None, None, None)
        limit = item.get("weekly_limit_usd")
        return PlatformQuota(
            weekly_usage_usd=_decimal(item.get("weekly_usage_usd"), "weekly_usage_usd"),
            weekly_limit_usd=None if limit is None else _decimal(limit, "weekly_limit_usd"),
            weekly_window_start=item.get("weekly_window_start"),
            weekly_window_resets_at=item.get("weekly_window_resets_at"),
        )

    def test_connection(self, account_id: int | None, quota_query_mode: str = "passive") -> dict[str, Any]:
        users = self._get("api/v1/admin/users", params={"page": 1, "page_size": 1})
        result: dict[str, Any] = {"users_api": "ok", "user_count": (users or {}).get("total") if isinstance(users, dict) else None}
        if account_id:
            window = self.query_weekly_window(account_id, quota_query_mode)
            result.update(
                {
                    "quota_api": "ok",
                    "quota_query_mode": quota_query_mode,
                    "used_percent": float(window.used_percent),
                    "reset_at": window.reset_at,
                    "sampled_at": window.sampled_at,
                }
            )
        return result
