"""Core structures for shared-resource attribution experiments."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import numpy as np

@dataclass(frozen=True)
class CycleData:
    duration_minutes: float
    event_times: np.ndarray
    event_users: np.ndarray
    event_costs: np.ndarray
    event_inverse_rates: np.ndarray  # pp / dollar
    event_true_q: np.ndarray         # pp
    sample_times: np.ndarray
    true_progress_samples: np.ndarray
    observed_z: np.ndarray
    n_users: int
    quantizer_name: str
    quantizer_params: dict[str, Any] = field(default_factory=dict)
    scenario_name: str = ""
    seed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def true_user_totals(self) -> np.ndarray:
        return np.bincount(self.event_users, weights=self.event_true_q,
                           minlength=self.n_users).astype(float)
    @property
    def cost_user_totals(self) -> np.ndarray:
        return np.bincount(self.event_users, weights=self.event_costs,
                           minlength=self.n_users).astype(float)
    @property
    def true_total(self) -> float:
        return float(self.event_true_q.sum())
    @property
    def observed_final(self) -> int:
        return int(self.observed_z[-1])

@dataclass(frozen=True)
class AggregatedCycle:
    interval_end_times: np.ndarray
    costs_by_interval_user: np.ndarray
    observed_z: np.ndarray
    true_progress: np.ndarray
    n_users: int
    @property
    def total_costs(self) -> np.ndarray:
        return self.costs_by_interval_user.sum(axis=1)


def aggregate_cycle(cycle: CycleData, keep_final: bool = True) -> AggregatedCycle:
    """Aggregate costs into sampling intervals and remove redundant empty intervals."""
    s = cycle.sample_times
    idx = np.searchsorted(s, cycle.event_times, side="left")
    idx = np.clip(idx, 1, len(s)-1)
    K = len(s)-1
    C = np.zeros((K, cycle.n_users), float)
    for j, k in enumerate(idx):
        C[k-1, cycle.event_users[j]] += cycle.event_costs[j]
    keep = C.sum(axis=1) > 0
    if keep_final and K:
        keep[-1] = True
    return AggregatedCycle(s[1:][keep], C[keep], cycle.observed_z[1:][keep],
                           cycle.true_progress_samples[1:][keep], cycle.n_users)


def truncate_cycle(cycle: CycleData, sample_index: int) -> CycleData:
    if not (1 <= sample_index < len(cycle.sample_times)):
        raise ValueError("invalid sample index")
    end = float(cycle.sample_times[sample_index])
    m = cycle.event_times <= end
    return CycleData(end, cycle.event_times[m].copy(), cycle.event_users[m].copy(),
                     cycle.event_costs[m].copy(), cycle.event_inverse_rates[m].copy(),
                     cycle.event_true_q[m].copy(), cycle.sample_times[:sample_index+1].copy(),
                     cycle.true_progress_samples[:sample_index+1].copy(),
                     cycle.observed_z[:sample_index+1].copy(), cycle.n_users,
                     cycle.quantizer_name, dict(cycle.quantizer_params), cycle.scenario_name,
                     cycle.seed, dict(cycle.metadata))
