"""FAST 修正的账号级前缀和查询。"""

from bisect import bisect_right
from datetime import datetime
from decimal import Decimal

from .constants import MAX_KEY_ID, ZERO
from ..models import Observation


class FastCorrectionPrefix:
    """一次加载账号全部修正，提供任意归属边界到观测点的前缀差。"""

    def __init__(self, account_id: int, basis: str):
        total_field = (
            "fast_correction_actual_cost"
            if basis == "actual"
            else "fast_correction_standard_cost"
        )
        user_field = (
            "actual_correction_cost"
            if basis == "actual"
            else "standard_correction_cost"
        )
        observations = list(
            Observation.objects.filter(account_id=account_id)
            .prefetch_related("fast_corrections")
            .order_by("observed_at", "id")
        )

        self.total_keys: list[tuple[datetime, int]] = []
        self.total_values: list[Decimal] = []
        self.user_keys: dict[int, list[tuple[datetime, int]]] = {}
        self.user_values: dict[int, list[Decimal]] = {}
        total_running = ZERO
        user_running: dict[int, Decimal] = {}
        for observation in observations:
            key = (observation.observed_at, observation.id)
            total_running += getattr(observation, total_field) or ZERO
            self.total_keys.append(key)
            self.total_values.append(total_running)
            for row in observation.fast_corrections.all():
                user_id = row.sub2api_user_id
                user_running[user_id] = (
                    user_running.get(user_id, ZERO) + getattr(row, user_field)
                )
                self.user_keys.setdefault(user_id, []).append(key)
                self.user_values.setdefault(user_id, []).append(
                    user_running[user_id]
                )

    @staticmethod
    def _prefix_at(
        keys: list[tuple[datetime, int]],
        values: list[Decimal],
        key: tuple[datetime, int],
    ) -> Decimal:
        index = bisect_right(keys, key) - 1
        return values[index] if index >= 0 else ZERO

    def total_between(
        self,
        started_at: datetime,
        observation: Observation,
    ) -> Decimal:
        end = self._prefix_at(
            self.total_keys,
            self.total_values,
            (observation.observed_at, observation.id),
        )
        start = self._prefix_at(
            self.total_keys,
            self.total_values,
            (started_at, MAX_KEY_ID),
        )
        return max(ZERO, end - start)

    def user_between(
        self,
        user_id: int,
        started_at: datetime,
        ended_at: datetime,
        *,
        observation_id: int = MAX_KEY_ID,
    ) -> Decimal:
        keys = self.user_keys.get(user_id, [])
        values = self.user_values.get(user_id, [])
        end = self._prefix_at(keys, values, (ended_at, observation_id))
        start = self._prefix_at(
            keys,
            values,
            (started_at, MAX_KEY_ID),
        )
        return max(ZERO, end - start)
