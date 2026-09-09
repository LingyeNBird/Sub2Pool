"""Frozen-policy correction prefixes over immutable request evidence.

The public class name is retained for internal compatibility. Its monetary
methods return all local corrections frozen on historical observations.
"""

from bisect import bisect_right
from datetime import datetime
from decimal import Decimal

from .constants import MAX_KEY_ID, ZERO
from ..billing_correction.domain import CorrectionAmounts
from ..billing_correction.observations import interval_corrections
from ..models import AppSettings, Observation, Sub2APIUserUsageSample


class FastCorrectionPrefix:
    def __init__(self, account_id: int, basis: str, config: AppSettings | None = None):
        config = config or AppSettings.load()
        self.account_id = account_id
        self.total_keys = []
        self.total_values = []
        self.user_keys = {}
        self.user_values = {}
        self.missing_values = []
        self.unknown_values = []
        self.sample_total_actual_values = []
        self.sample_total_selected_values = []
        self.sample_user_keys = {}
        self.sample_user_actual_values = {}
        self.sample_user_selected_values = {}
        self.account_raw_costs = {}
        self.user_raw_costs = None
        if account_id < 0:
            return
        rules_cache = {}
        observations = list(
            Observation.objects.filter(account_id=account_id)
            .select_related("billing_capture")
            .prefetch_related("fast_corrections", "billing_capture__facts")
            .order_by("observed_at", "id")
        )
        total = CorrectionAmounts()
        users = {}
        sample_total_actual = sample_total_selected = ZERO
        sample_users = {}
        missing = unknown = 0
        for observation in observations:
            interval = interval_corrections(
                observation,
                config,
                rules_cache=rules_cache,
            )
            key = (observation.observed_at, observation.id)
            total += interval.amounts
            missing += int(
                observation.correction_source == "local"
                and not interval.facts_complete
            )
            unknown += interval.unknown_long_context_request_count
            if interval.raw_cost is not None and interval.actual_cost is not None:
                sample_total_actual += interval.actual_cost or ZERO
                sample_total_selected += (
                    (interval.raw_cost or ZERO) + interval.amounts.total
                )
            self.total_keys.append(key)
            self.total_values.append(total)
            self.missing_values.append(missing)
            self.unknown_values.append(unknown)
            self.sample_total_actual_values.append(sample_total_actual)
            self.sample_total_selected_values.append(sample_total_selected)
            self.account_raw_costs[observation.observed_at] = (
                observation.normalized_cost("actual"),
                observation.normalized_cost("standard"),
            )
            for user_id, row in interval.users.items():
                users[user_id] = (
                    users.get(user_id, CorrectionAmounts()) + row.amounts
                )
                self.user_keys.setdefault(user_id, []).append(key)
                self.user_values.setdefault(user_id, []).append(users[user_id])
                if interval.facts_complete:
                    actual, selected = sample_users.get(
                        user_id, (ZERO, ZERO)
                    )
                    sample_users[user_id] = (
                        actual + row.actual_cost,
                        selected + row.raw_cost + row.amounts.total,
                    )
                    self.sample_user_keys.setdefault(user_id, []).append(key)
                    self.sample_user_actual_values.setdefault(user_id, []).append(
                        sample_users[user_id][0]
                    )
                    self.sample_user_selected_values.setdefault(user_id, []).append(
                        sample_users[user_id][1]
                    )

    @staticmethod
    def _prefix_at(keys, values, key, zero=None):
        index = bisect_right(keys, key) - 1
        return values[index] if index >= 0 else (CorrectionAmounts() if zero is None else zero)

    @classmethod
    def _decimal_between(cls, keys, values, start, end) -> Decimal:
        return cls._prefix_at(keys, values, end, ZERO) - cls._prefix_at(
            keys, values, start, ZERO
        )

    def sample_between(
        self, started_at: datetime, observation: Observation
    ) -> tuple[Decimal, Decimal]:
        """Return actual wallet cost and frozen-policy selected request cost."""
        start = (started_at, MAX_KEY_ID)
        end = (observation.observed_at, observation.id)
        return (
            self._decimal_between(
                self.total_keys,
                self.sample_total_actual_values,
                start,
                end,
            ),
            self._decimal_between(
                self.total_keys,
                self.sample_total_selected_values,
                start,
                end,
            ),
        )

    def user_sample_between(
        self,
        user_id: int,
        started_at: datetime,
        observation: Observation,
    ) -> tuple[Decimal, Decimal]:
        """Return the same request sample pair for one historical user identity."""
        keys = self.sample_user_keys.get(user_id, [])
        start = (started_at, MAX_KEY_ID)
        end = (observation.observed_at, observation.id)
        return (
            self._decimal_between(
                keys,
                self.sample_user_actual_values.get(user_id, []),
                start,
                end,
            ),
            self._decimal_between(
                keys,
                self.sample_user_selected_values.get(user_id, []),
                start,
                end,
            ),
        )

    def account_raw_between(
        self, started_at: datetime, observation: Observation
    ) -> tuple[Decimal, Decimal]:
        """Return aligned aggregate actual/standard source costs."""
        end = (
            observation.normalized_cost("actual"),
            observation.normalized_cost("standard"),
        )
        start = self.account_raw_costs.get(
            started_at, (ZERO, ZERO)
        )
        return end[0] - start[0], end[1] - start[1]

    def user_raw_between(
        self,
        user_id: int,
        started_at: datetime,
        observation: Observation,
    ) -> tuple[Decimal, Decimal] | None:
        """Return aligned aggregate actual/standard costs when both endpoints exist."""
        if self.user_raw_costs is None:
            self.user_raw_costs = {
                (row.sub2api_user_id, row.observed_at): (
                    row.normalized_cost("actual"),
                    row.normalized_cost("standard"),
                )
                for row in Sub2APIUserUsageSample.objects.filter(
                    account_id=self.account_id
                ).order_by("observed_at", "sub2api_user_id", "id")
            }
        end = self.user_raw_costs.get((user_id, observation.observed_at))
        if end is None:
            return None
        if started_at in self.account_raw_costs:
            start = self.user_raw_costs.get((user_id, started_at))
            if start is None:
                return None
        else:
            start = (ZERO, ZERO)
        return end[0] - start[0], end[1] - start[1]

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
