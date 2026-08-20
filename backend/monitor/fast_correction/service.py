"""从 Sub2API 只读请求日志构造 FAST 修正区间。"""

from datetime import datetime
from typing import Any

from .domain import FastCorrectionInterval, aggregate_fast_logs
from .rules import FastCorrectionRuleSet
from ..integrations.sub2api import Sub2APIReader


def fetch_fast_interval(
    client: Sub2APIReader,
    *,
    account_id: int,
    started_at: datetime,
    ended_at: datetime,
    timezone_name: str,
    correction_rules: Any,
) -> FastCorrectionInterval:
    rules = FastCorrectionRuleSet(correction_rules)
    logs = client.usage_logs(
        account_id=account_id,
        started_at=started_at,
        ended_at=ended_at,
        timezone_name=timezone_name,
    )
    return aggregate_fast_logs(
        logs,
        started_at=started_at,
        ended_at=ended_at,
        rules=rules,
    )
