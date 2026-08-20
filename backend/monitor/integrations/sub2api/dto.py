"""Typed facts returned by the Sub2API Admin API."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


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
class Sub2APIUserUsage:
    """一个 Sub2API 用户在指定上游账号与日期窗口内的累计成本。"""

    user_id: int
    email: str
    username: str
    stats: UsageStats


@dataclass(frozen=True)
class Sub2APIUsageLog:
    """FAST 修正所需的最小请求日志事实。"""

    id: int
    user_id: int
    account_id: int
    created_at: datetime
    service_tier: str
    total_cost: Decimal
    actual_cost: Decimal
    api_key_id: int = 0
    api_key_name: str = ""
    model: str = ""

    def selected(self, basis: str) -> Decimal:
        return self.actual_cost if basis == "actual" else self.total_cost



@dataclass(frozen=True)
class UserBalance:
    """Sub2API 用户的全局余额；该余额会被该用户的所有用量共同消耗。"""

    balance: Decimal
    frozen_balance: Decimal


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}") from exc


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
