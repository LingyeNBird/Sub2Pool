"""Passive and direct seven-day quota-window resources."""
from datetime import datetime, timezone
from typing import Any

from .dto import Sub2APIError, WeeklyWindow, _decimal


class QuotaResourceMixin:
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
