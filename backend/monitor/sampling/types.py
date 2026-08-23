"""采样流程内部使用的数据结构与窗口引用。"""

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from ..integrations.sub2api import (
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from ..models import AccountParticipant, Observation, Participant, PoolParticipant


@dataclass
class LocalParticipantData:
    membership: AccountParticipant
    stats: UsageStats
    balance: UserBalance
    allocation: PoolParticipant | None = None

    @property
    def participant(self) -> Participant:
        return self.membership.participant


    @property
    def contract(self) -> PoolParticipant:
        if self.allocation is None:
            self.allocation = PoolParticipant.objects.select_related("pool").get(
                pool_id=self.membership.account.pool_id,
                participant_id=self.membership.participant_id,
            )
        return self.allocation

    def selected_cost(self, basis: str) -> Decimal:
        return self.stats.selected(basis)


@dataclass
class LocalBundle:
    total: UsageStats
    participants: list[LocalParticipantData]
    users: list[Sub2APIUserUsage]
    checked_at: datetime
    cost_window_started_at: datetime
    cost_window_ended_at: datetime


@dataclass(frozen=True)
class WindowReference:
    account_id: int
    reset_at: datetime
    window_seconds: int


def epoch_datetime(value: int) -> datetime:
    """兼容被代理误转成毫秒的 Unix 时间，内部统一为 UTC。"""

    if value > 10_000_000_000:
        value //= 1000
    return datetime.fromtimestamp(value, tz=dt_timezone.utc)


def window_reference(account_id: int, window: WeeklyWindow) -> WindowReference:
    return WindowReference(
        account_id=account_id,
        reset_at=epoch_datetime(window.reset_at),
        window_seconds=window.window_seconds,
    )


def observation_reference(observation: Observation) -> WindowReference:
    return WindowReference(
        account_id=observation.account_id,
        reset_at=observation.upstream_resets_at,
        window_seconds=observation.window_seconds,
    )
