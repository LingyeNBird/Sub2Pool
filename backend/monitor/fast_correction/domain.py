"""FAST 请求日志的纯聚合领域模型。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from .constants import COST_PRECISION, FAST_EXTRA_FACTOR, ZERO
from ..integrations.sub2api import Sub2APIUsageLog


def money(value: Decimal) -> Decimal:
    return value.quantize(COST_PRECISION, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class UserFastCorrection:
    user_id: int
    request_count: int
    fast_request_count: int
    fast_standard_cost: Decimal
    fast_actual_cost: Decimal
    standard_correction_cost: Decimal
    actual_correction_cost: Decimal


@dataclass(frozen=True)
class FastCorrectionInterval:
    started_at: datetime
    ended_at: datetime
    request_count: int
    fast_request_count: int
    standard_correction_cost: Decimal
    actual_correction_cost: Decimal
    users: tuple[UserFastCorrection, ...]

    def selected_correction(self, basis: str) -> Decimal:
        return (
            self.actual_correction_cost
            if basis == "actual"
            else self.standard_correction_cost
        )


def aggregate_fast_logs(
    logs: list[Sub2APIUsageLog],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> FastCorrectionInterval:
    """把一个采样区间的 FAST 请求按原始 Sub2API 用户 ID 汇总。"""

    totals: dict[int, dict[str, Decimal | int]] = {}
    fast_count = 0
    for log in logs:
        row = totals.setdefault(
            log.user_id,
            {
                "request_count": 0,
                "count": 0,
                "standard": ZERO,
                "actual": ZERO,
            },
        )
        row["request_count"] = int(row["request_count"]) + 1
        if log.service_tier != "priority":
            continue
        fast_count += 1
        row["count"] = int(row["count"]) + 1
        row["standard"] = Decimal(row["standard"]) + log.total_cost
        row["actual"] = Decimal(row["actual"]) + log.actual_cost

    users: list[UserFastCorrection] = []
    for user_id in sorted(totals):
        row = totals[user_id]
        standard = money(Decimal(row["standard"]))
        actual = money(Decimal(row["actual"]))
        users.append(
            UserFastCorrection(
                user_id=user_id,
                request_count=int(row["request_count"]),
                fast_request_count=int(row["count"]),
                fast_standard_cost=standard,
                fast_actual_cost=actual,
                standard_correction_cost=money(
                    standard * FAST_EXTRA_FACTOR
                ),
                actual_correction_cost=money(actual * FAST_EXTRA_FACTOR),
            )
        )

    return FastCorrectionInterval(
        started_at=started_at,
        ended_at=ended_at,
        request_count=len(logs),
        fast_request_count=fast_count,
        standard_correction_cost=money(
            sum((row.standard_correction_cost for row in users), ZERO)
        ),
        actual_correction_cost=money(
            sum((row.actual_correction_cost for row in users), ZERO)
        ),
        users=tuple(users),
    )
