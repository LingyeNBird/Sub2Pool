"""周限报告使用的累计成本拆分。"""

from decimal import Decimal, ROUND_HALF_UP

from ..fast_correction import FastCorrectionPrefix
from ..models import AppSettings, Observation


ZERO = Decimal("0")
CENT = Decimal("0.01")


class FastCorrectionBreakdownPresenter:
    """把重放后的累计成本拆成 Sub2API 原值与累计 FAST 修正。"""

    def __init__(self, config: AppSettings, account_id: int | None):
        self.enabled = bool(config.fast_correction_enabled)
        self.prefix = (
            FastCorrectionPrefix(account_id, config.cost_basis)
            if self.enabled and account_id
            else None
        )

    @staticmethod
    def _money(value: Decimal) -> float:
        return float(value.quantize(CENT, rounding=ROUND_HALF_UP))

    def for_observation(self, observation: Observation) -> dict[str, float]:
        total = max(ZERO, observation.selected_total_cost)
        correction = (
            self.prefix.total_between(
                observation.attribution_started_at,
                observation,
            )
            if self.prefix is not None
            and observation.attribution_started_at is not None
            else ZERO
        )
        # 重放保证修正包含在总成本中；上限保护让历史异常数据也保持 A + B = 总额。
        correction = min(total, max(ZERO, correction))
        return {
            "sub2api_cost_usd": self._money(total - correction),
            "fast_correction_usd": self._money(correction),
            "total_cost_usd": self._money(total),
        }

    @staticmethod
    def zero() -> dict[str, float]:
        return {
            "sub2api_cost_usd": 0.0,
            "fast_correction_usd": 0.0,
            "total_cost_usd": 0.0,
        }
