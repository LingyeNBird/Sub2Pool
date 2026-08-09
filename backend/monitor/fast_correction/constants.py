"""FAST 修正所使用的固定倍率与数值常量。"""

from decimal import Decimal

ZERO = Decimal("0")
COST_PRECISION = Decimal("0.000001")
SUB2API_FAST_MULTIPLIER = Decimal("2")
UPSTREAM_FAST_MULTIPLIER = Decimal("2.5")
FAST_EXTRA_FACTOR = (
    UPSTREAM_FAST_MULTIPLIER / SUB2API_FAST_MULTIPLIER - Decimal("1")
)
MAX_KEY_ID = 2**63 - 1
