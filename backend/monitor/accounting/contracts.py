"""归属重放过程中的内部数据契约。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..models import Observation

ALGORITHM_VERSION = "particle_filter_v2"


@dataclass
class ReplaySegment:
    """由原始采样推断出的连续归属区间，仅在本次重放期间存在。"""

    observations: list[Observation]
    started_at: datetime
    first_observed_at: datetime
    resets_at: datetime
    reason: str
    total_baseline: Decimal
    participant_baselines: dict[int, Decimal]
    percent_baseline: Decimal


@dataclass(frozen=True)
class ReplayResult:
    rebuilt_observations: int
    automatic_exclusions: int
    inferred_intervals: int
    latest_observation_id: int | None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "rebuilt_observations": self.rebuilt_observations,
            "automatic_exclusions": self.automatic_exclusions,
            "inferred_intervals": self.inferred_intervals,
            "latest_observation_id": self.latest_observation_id,
        }
