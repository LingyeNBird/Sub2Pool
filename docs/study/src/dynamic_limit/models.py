from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SimulationSpec:
    case_id: str
    seed: int
    speed: str
    n_participants: int
    rights_profile: str
    scenario: str
    sample_hours: float
    quantizer: str
    horizon_hours: float = 168.0
    dt_hours: float = 1.0 / 6.0
    target_progress_low: float = 70.0
    target_progress_high: float = 92.0
    capacity_min_usd: float = 1400.0
    capacity_max_usd: float = 2100.0


@dataclass
class SimulationTruth:
    spec: SimulationSpec
    t: np.ndarray
    v: np.ndarray
    x: np.ndarray
    rights: np.ndarray
    d_c: np.ndarray
    c: np.ndarray
    d_q: np.ndarray
    q: np.ndarray
    p: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observations:
    spec: SimulationSpec
    sample_idx: np.ndarray
    times: np.ndarray
    c_obs: np.ndarray
    z: np.ndarray
    quantizer: str


@dataclass
class AlgorithmOutput:
    algorithm: str
    times: np.ndarray
    b_hat: np.ndarray
    l_hat: np.ndarray
    b_lower: np.ndarray | None = None
    b_upper: np.ndarray | None = None
    l_lower: np.ndarray | None = None
    l_upper: np.ndarray | None = None
    q_hat: np.ndarray | None = None
    v_hat: np.ndarray | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
