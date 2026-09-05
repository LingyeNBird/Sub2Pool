"""A configured reduction must never turn a tiny source charge negative."""

from decimal import Decimal

import pytest

from monitor.billing_correction.domain import BillingCorrectionRules
from monitor.models import AppSettings
from .test_corrections import log


@pytest.mark.parametrize("basis", ["actual", "standard"])
@pytest.mark.parametrize("raw", ["0.0000001", "0.0000006", "0.0000016", "0.0000026"])
def test_tiny_cost_reductions_remain_nonnegative_and_additive(basis, raw):
    amount = Decimal(raw)
    for source, target, multiplier in [(2, "0.01", 1), (2, 1, "0.01"), (100, "0.01", "0.01")]:
        config = AppSettings(
            fast_correction_enabled=False,
            long_context_correction_rules=[{
                "model_pattern": "*", "source_multiplier": source,
                "target_multiplier": target,
            }],
            model_correction_rules=[{"model_pattern": "*", "multiplier": multiplier}],
        )
        result = BillingCorrectionRules(config).calculate(
            log(actual_cost=amount, total_cost=amount), basis,
        )
        assert result.corrected_cost >= 0
        assert result.raw_cost + result.amounts.total == result.corrected_cost
        assert result.amounts.fast == 0
        assert result.amounts.long_context <= 0
        assert result.amounts.model <= 0
