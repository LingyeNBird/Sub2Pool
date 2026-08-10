"""Read-only usage aggregation and request-log resources."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .dto import (
    Sub2APIError,
    Sub2APIUsageLog,
    Sub2APIUserUsage,
    UsageStats,
    _decimal,
    _timestamp,
)


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _api_key_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("name") or "").strip()


class UsageResourceMixin:
    def all_user_usage_stats(
        self,
        *,
        account_id: int,
        start_date: date,
        end_date: date,
        timezone_name: str,
    ) -> list[Sub2APIUserUsage]:
        """只读获取指定上游账号下全部 Sub2API 用户的累计用量。

        新版 Sub2API 的用户用量分解接口单次最多返回 200 名有用量的用户。
        先用一次分解请求覆盖常见场景；用户数超过 200 时，再只为未返回的用户
        逐个读取统计，避免静默漏掉低用量用户。旧版没有分解接口时则完整回退
        到逐用户 GET 查询。全程不会调用任何上游官方额度接口或写接口。
        """

        users = self.list_users()
        metadata = {
            int(user["id"]): {
                "email": str(user.get("email") or ""),
                "username": str(user.get("username") or ""),
            }
            for user in users
        }
        stats_by_user: dict[int, UsageStats] = {}
        breakdown_available = True
        try:
            data = self._get(
                "api/v1/admin/dashboard/user-breakdown",
                params={
                    "account_id": account_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "timezone": timezone_name,
                    "limit": 200,
                    "nocache": "true",
                },
            )
        except Sub2APIError:
            breakdown_available = False
            data = None

        if breakdown_available:
            if not isinstance(data, dict) or not isinstance(
                data.get("users"),
                list,
            ):
                raise Sub2APIError("用户用量分解响应结构错误")
            for raw in data["users"]:
                if not isinstance(raw, dict):
                    continue
                try:
                    user_id = int(raw.get("user_id"))
                except (TypeError, ValueError):
                    continue
                if user_id <= 0:
                    continue
                metadata.setdefault(
                    user_id,
                    {
                        "email": str(raw.get("email") or ""),
                        "username": "",
                    },
                )
                stats_by_user[user_id] = UsageStats(
                    total_cost=_decimal(raw.get("cost"), "users.cost"),
                    total_actual_cost=_decimal(
                        raw.get("actual_cost"),
                        "users.actual_cost",
                    ),
                )

        must_query_missing = not breakdown_available or len(metadata) > 200
        for user_id in metadata:
            if user_id in stats_by_user:
                continue
            if must_query_missing:
                stats_by_user[user_id] = self.usage_stats(
                    account_id=account_id,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    timezone_name=timezone_name,
                )
            else:
                stats_by_user[user_id] = UsageStats(
                    total_cost=Decimal("0"),
                    total_actual_cost=Decimal("0"),
                )

        return [
            Sub2APIUserUsage(
                user_id=user_id,
                email=metadata[user_id]["email"],
                username=metadata[user_id]["username"],
                stats=stats_by_user[user_id],
            )
            for user_id in sorted(metadata)
        ]

    def usage_logs(
        self,
        *,
        account_id: int,
        started_at: datetime | None,
        ended_at: datetime,
        timezone_name: str,
        user_id: int | None = None,
    ) -> list[Sub2APIUsageLog]:
        """分页只读请求日志，并在本地收紧到精确的半开时间区间。

        Sub2API 列表接口只支持按自然日过滤，所以网络请求先覆盖相关日期，
        再按 ``[started_at, ended_at)`` 过滤。重建全部历史时 ``started_at``
        为 ``None``，此时从该账号最早的请求日志开始。
        """

        if ended_at.tzinfo is None:
            raise ValueError("ended_at 必须包含时区")
        try:
            location = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise Sub2APIError("FAST 修正使用了无效的统计时区") from exc

        end_utc = ended_at.astimezone(timezone.utc)
        start_utc = (
            started_at.astimezone(timezone.utc)
            if started_at is not None
            else None
        )
        params: dict[str, Any] = {
            "page_size": 1000,
            "account_id": account_id,
            "end_date": end_utc.astimezone(location).date().isoformat(),
            "timezone": timezone_name,
            "sort_by": "created_at",
            "sort_order": "asc",
            "exact_total": "true",
        }
        if start_utc is not None:
            params["start_date"] = (
                start_utc.astimezone(location).date().isoformat()
            )
        if user_id is not None:
            params["user_id"] = user_id

        rows: list[Sub2APIUsageLog] = []
        seen_ids: set[int] = set()
        page = 1
        while True:
            params["page"] = page
            data = self._get("api/v1/admin/usage", params=params)
            if not isinstance(data, dict) or not isinstance(
                data.get("items"),
                list,
            ):
                raise Sub2APIError("请求日志响应结构错误")
            for raw in data["items"]:
                if not isinstance(raw, dict):
                    continue
                try:
                    log_id = int(raw.get("id"))
                    returned_user_id = int(raw.get("user_id"))
                    returned_account_id = int(raw.get("account_id"))
                except (TypeError, ValueError):
                    continue
                if (
                    log_id <= 0
                    or returned_user_id <= 0
                    or returned_account_id != account_id
                    or log_id in seen_ids
                ):
                    continue
                if user_id is not None and returned_user_id != user_id:
                    continue
                created_at = _timestamp(raw.get("created_at"), "created_at")
                if created_at >= end_utc or (
                    start_utc is not None and created_at < start_utc
                ):
                    continue
                seen_ids.add(log_id)
                rows.append(
                    Sub2APIUsageLog(
                        id=log_id,
                        user_id=returned_user_id,
                        account_id=returned_account_id,
                        created_at=created_at,
                        service_tier=str(raw.get("service_tier") or "")
                        .strip()
                        .lower(),
                        total_cost=_decimal(raw.get("total_cost"), "total_cost"),
                        actual_cost=_decimal(
                            raw.get("actual_cost"),
                            "actual_cost",
                        ),
                        api_key_id=_positive_int(raw.get("api_key_id")),
                        api_key_name=_api_key_name(raw.get("api_key")),
                    )
                )

            try:
                pages = max(1, int(data.get("pages") or 1))
            except (TypeError, ValueError) as exc:
                raise Sub2APIError("请求日志分页字段无效") from exc
            if page >= pages:
                break
            page += 1
            if page > 10_000:
                raise Sub2APIError("请求日志数量异常，已停止读取")

        rows.sort(key=lambda item: (item.created_at, item.id))
        return rows

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
