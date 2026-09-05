"""Explain selected costs with signed, locally recalculated correction terms."""

from decimal import Decimal

from ..billing_correction.domain import CorrectionAmounts
from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import AppSettings, Observation

ZERO = Decimal("0")


class FastCorrectionBreakdownPresenter:
    # Name retained to avoid breaking consumers; this presents all corrections.
    def __init__(self, config: AppSettings, account_id: int | None):
        self.prefix = FastCorrectionPrefix(account_id, config.cost_basis, config) if account_id else None

    def for_observation(self, observation: Observation) -> dict:
        total = max(ZERO, observation.selected_total_cost)
        amounts = CorrectionAmounts()
        coverage = {"correction_facts_complete": True, "missing_correction_intervals": 0, "unknown_long_context_request_count": 0}
        if self.prefix is not None and observation.attribution_started_at is not None:
            amounts = self.prefix.breakdown_between(observation.attribution_started_at, observation)
            coverage = self.prefix.coverage_between(observation.attribution_started_at, observation)
        # Never clamp a negative correction; raw + signed corrections = total.
        return {
            "sub2api_cost_usd": float(total - amounts.total),
            "total_cost_usd": float(total),
            **amounts.payload(), **coverage,
        }

    @staticmethod
    def zero() -> dict:
        return {
            "sub2api_cost_usd": 0.0, "total_cost_usd": 0.0,
            **CorrectionAmounts().payload(),
            "correction_facts_complete": True, "missing_correction_intervals": 0,
            "unknown_long_context_request_count": 0,
        }
