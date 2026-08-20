"""FAST 修正所使用的固定倍率与数值常量。"""

from decimal import Decimal

ZERO = Decimal("0")
COST_PRECISION = Decimal("0.000001")
MAX_KEY_ID = 2**63 - 1
