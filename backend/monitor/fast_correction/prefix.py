"""Current-policy correction prefixes over immutable local request evidence.

The public class name is retained for internal compatibility. Its monetary
methods now return ALL corrections, including signed long-context reductions.
"""

from bisect import bisect_right
from datetime import datetime
from decimal import Decimal

from .constants import MAX_KEY_ID
from ..billing_correction.domain import BillingCorrectionRules, CorrectionAmounts
from ..billing_correction.observations import interval_corrections
from ..models import AppSettings, Observation


class FastCorrectionPrefix:
    def __init__(self, account_id: int, basis: str, config: AppSettings | None = None):
        config = config or AppSettings.load()
        # Callers may request a basis other than the singleton's current basis.
        from copy import copy
        config = copy(config)
        config.cost_basis = basis
        self.total_keys = []
        self.total_values = []
        self.user_keys = {}
        self.user_values = {}
        self.missing_values = []
        self.unknown_values = []
        if account_id < 0:
            return
        rules = BillingCorrectionRules(config)
        observations = Observation.objects.filter(account_id=account_id).select_related("billing_capture").prefetch_related("fast_corrections", "billing_capture__facts").order_by("observed_at", "id")
        total = CorrectionAmounts()
        users = {}
        missing = unknown = 0
        for observation in observations:
            interval = interval_corrections(observation, config, rules=rules)
            key = (observation.observed_at, observation.id)
            total += interval.amounts
            missing += int(not interval.facts_complete)
            unknown += interval.unknown_long_context_request_count
            self.total_keys.append(key)
            self.total_values.append(total)
            self.missing_values.append(missing)
            self.unknown_values.append(unknown)
            for user_id, row in interval.users.items():
                users[user_id] = users.get(user_id, CorrectionAmounts()) + row.amounts
                self.user_keys.setdefault(user_id, []).append(key)
                self.user_values.setdefault(user_id, []).append(users[user_id])

    @staticmethod
    def _prefix_at(keys, values, key, zero=None):
        index = bisect_right(keys, key) - 1
        return values[index] if index >= 0 else (CorrectionAmounts() if zero is None else zero)

    def breakdown_between(self, started_at: datetime, observation: Observation) -> CorrectionAmounts:
        end = self._prefix_at(self.total_keys, self.total_values, (observation.observed_at, observation.id))
        start = self._prefix_at(self.total_keys, self.total_values, (started_at, MAX_KEY_ID))
        return end - start

    def coverage_between(self, started_at: datetime, observation: Observation) -> dict:
        start, end = (started_at, MAX_KEY_ID), (observation.observed_at, observation.id)
        missing = self._prefix_at(self.total_keys, self.missing_values, end, 0) - self._prefix_at(self.total_keys, self.missing_values, start, 0)
        unknown = self._prefix_at(self.total_keys, self.unknown_values, end, 0) - self._prefix_at(self.total_keys, self.unknown_values, start, 0)
        return {"correction_facts_complete": missing == 0, "missing_correction_intervals": missing, "unknown_long_context_request_count": unknown}

    def total_between(self, started_at: datetime, observation: Observation) -> Decimal:
        return self.breakdown_between(started_at, observation).total

    def user_between(self, user_id: int, started_at: datetime, ended_at: datetime, *, observation_id: int = MAX_KEY_ID) -> Decimal:
        keys, values = self.user_keys.get(user_id, []), self.user_values.get(user_id, [])
        end = self._prefix_at(keys, values, (ended_at, observation_id))
        start = self._prefix_at(keys, values, (started_at, MAX_KEY_ID))
        return (end - start).total
