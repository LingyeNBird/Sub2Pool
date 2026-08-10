"""Structural interfaces consumed by sampling and correction services."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from .dto import (
    Sub2APIUsageLog,
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)


class Sub2APIReader(Protocol):
    def list_openai_accounts(self) -> list[dict[str, Any]]: ...
    def list_users(self) -> list[dict[str, Any]]: ...
    def query_weekly_window(
        self,
        account_id: int,
        mode: str = "passive",
    ) -> WeeklyWindow: ...
    def usage_stats(
        self,
        *,
        account_id: int,
        start_date: date,
        end_date: date,
        timezone_name: str,
        user_id: int | None = None,
    ) -> UsageStats: ...
    def all_user_usage_stats(
        self,
        *,
        account_id: int,
        start_date: date,
        end_date: date,
        timezone_name: str,
    ) -> list[Sub2APIUserUsage]: ...
    def usage_logs(
        self,
        *,
        account_id: int,
        started_at: datetime | None,
        ended_at: datetime,
        timezone_name: str,
        user_id: int | None = None,
    ) -> list[Sub2APIUsageLog]: ...
    def user_balance(self, user_id: int) -> UserBalance: ...
    def list_user_api_keys(self, user_id: int) -> list[dict[str, Any]]: ...



class RecommendationBalanceWriter(Protocol):
    def set_user_balance_from_recommendation(
        self,
        user_id: int,
        balance: Decimal,
    ) -> Decimal: ...
