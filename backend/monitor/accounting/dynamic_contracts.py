"""时变额度算法的纯计算输入输出契约。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DynamicModelInput:
    """一个归属区间内按时间排序的模型输入。"""

    times_hours: np.ndarray
    costs_usd: np.ndarray
    displayed_percent: np.ndarray
    rights_percent: np.ndarray
    baseline_display_percent: float = 0.0
    baseline_exact_zero: bool = True

    def validate(self) -> None:
        count = len(self.times_hours)
        if count == 0:
            raise ValueError("时变模型至少需要一个观测点")
        if self.costs_usd.ndim != 2 or self.costs_usd.shape[0] != count:
            raise ValueError("成本矩阵与观测时间数量不一致")
        if len(self.displayed_percent) != count:
            raise ValueError("百分比序列与观测时间数量不一致")
        if self.costs_usd.shape[1] != len(self.rights_percent):
            raise ValueError("成本主体数量与权益数量不一致")
        if self.costs_usd.shape[1] == 0:
            raise ValueError("时变模型至少需要一个成本主体")
        if not np.all(np.isfinite(self.times_hours)) or not np.all(
            np.isfinite(self.costs_usd)
        ):
            raise ValueError("时变模型输入包含非有限值")
        if np.any(np.diff(self.times_hours) <= 0):
            raise ValueError("观测时间必须严格递增")
        if np.any(self.costs_usd < -1e-9) or np.any(
            np.diff(self.costs_usd, axis=0) < -1e-9
        ):
            raise ValueError("累计成本必须非负且单调")
        if np.any(self.displayed_percent < 0) or np.any(
            self.displayed_percent > 100
        ):
            raise ValueError("显示百分比必须位于 0 到 100")
        if np.any(self.rights_percent < 0) or np.any(
            self.rights_percent > 100
        ):
            raise ValueError("权益百分比必须位于 0 到 100")


@dataclass(frozen=True)
class ParticleFilterOutput:
    capacity_hat_usd: np.ndarray
    capacity_lower_usd: np.ndarray
    capacity_upper_usd: np.ndarray
    total_percent_hat: np.ndarray
    total_percent_lower: np.ndarray
    total_percent_upper: np.ndarray
    attributed_percent_hat: np.ndarray
    attributed_percent_lower: np.ndarray
    attributed_percent_upper: np.ndarray
    balance_hat_usd: np.ndarray
    balance_lower_usd: np.ndarray
    balance_upper_usd: np.ndarray
    quantizer_probabilities: np.ndarray
    speed_probabilities: np.ndarray
    ess_fraction: np.ndarray
    resampled: np.ndarray


@dataclass(frozen=True)
class DeterministicBoundsOutput:
    total_percent_lower: np.ndarray
    total_percent_upper: np.ndarray
    attributed_percent_lower: np.ndarray
    attributed_percent_upper: np.ndarray
    balance_lower_usd: np.ndarray
    balance_upper_usd: np.ndarray
    infeasible_repairs: int
