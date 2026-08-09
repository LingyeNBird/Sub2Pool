"""把持久化原始事实转换为时变模型的完整区间输入。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib

import numpy as np

from .contracts import ALGORITHM_VERSION, ReplaySegment
from .dynamic_contracts import DynamicModelInput
from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import AppSettings, Sub2APIUserUsageSample

ZERO = Decimal("0")
RESIDUAL_SUBJECT = None


@dataclass(frozen=True)
class DynamicReplayInput:
    model_input: DynamicModelInput
    subject_user_ids: tuple[int | None, ...]
    participant_subject_indices: dict[int, int]
    observation_row_indices: tuple[int, ...]
    selected_totals: tuple[Decimal, ...]
    residual_costs: tuple[Decimal, ...]
    aggregate_cost_differences: tuple[Decimal, ...]


def stable_segment_seed(account_id: int, segment: ReplaySegment) -> int:
    material = (
        f"{ALGORITHM_VERSION}|{account_id}|"
        f"{segment.started_at.isoformat()}"
    ).encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def build_dynamic_replay_input(
    *,
    account_id: int,
    segment: ReplaySegment,
    config: AppSettings,
    correction_prefix: FastCorrectionPrefix,
) -> DynamicReplayInput:
    """构造全用户成本矩阵；无法映射到用户的总成本进入残差主体。"""

    observations = segment.observations
    if not observations:
        raise ValueError("归属区间没有观测")

    observation_times = [item.observed_at for item in observations]
    raw_user_rows = list(
        Sub2APIUserUsageSample.objects.filter(
            account_id=account_id,
            observed_at__in=observation_times,
        ).order_by("observed_at", "sub2api_user_id", "id")
    )
    raw_by_time: dict[object, dict[int, Decimal]] = {}
    user_ids: set[int] = set()
    for row in raw_user_rows:
        raw_by_time.setdefault(row.observed_at, {})[row.sub2api_user_id] = (
            row.selected_cost(config.cost_basis)
        )
        user_ids.add(row.sub2api_user_id)

    participant_by_id = {}
    snapshots_by_observation: list[dict[int, object]] = []
    for observation in observations:
        by_user = {}
        for snapshot in observation.participant_snapshots.all():
            participant = snapshot.participant
            participant_by_id[participant.id] = participant
            user_ids.add(participant.sub2api_user_id)
            by_user[participant.sub2api_user_id] = snapshot
        snapshots_by_observation.append(by_user)

    ordered_users = tuple(sorted(user_ids))
    subject_user_ids: tuple[int | None, ...] = (*ordered_users, RESIDUAL_SUBJECT)
    user_index = {user_id: index for index, user_id in enumerate(ordered_users)}
    participant_subject_indices = {
        participant_id: user_index[participant.sub2api_user_id]
        for participant_id, participant in participant_by_id.items()
    }
    rights = np.zeros(len(subject_user_ids), dtype=float)
    for participant_id, index in participant_subject_indices.items():
        rights[index] = float(participant_by_id[participant_id].share_percent)

    baseline_by_user: dict[int, Decimal] = {}
    first_is_observed_baseline = bool(
        observations[0].observed_at == segment.started_at
        and segment.reason in {"manual_override", "official_zero_observation"}
    )
    if first_is_observed_baseline:
        baseline_rows = raw_by_time.get(observations[0].observed_at, {})
        baseline_by_user.update(baseline_rows)
        for participant_id, baseline in segment.participant_baselines.items():
            participant = participant_by_id.get(participant_id)
            if participant is not None:
                baseline_by_user.setdefault(
                    participant.sub2api_user_id,
                    baseline,
                )

    selected_totals: list[Decimal] = []
    residual_costs: list[Decimal] = []
    aggregate_cost_differences: list[Decimal] = []
    cost_rows: list[list[float]] = []
    last_raw_by_user: dict[int, Decimal] = dict(baseline_by_user)
    for observation, snapshot_by_user in zip(
        observations,
        snapshots_by_observation,
        strict=True,
    ):
        current_raw = raw_by_time.get(observation.observed_at, {})
        for user_id in ordered_users:
            snapshot = snapshot_by_user.get(user_id)
            if user_id in current_raw:
                last_raw_by_user[user_id] = current_raw[user_id]
            elif snapshot is not None:
                last_raw_by_user[user_id] = snapshot.raw_selected_cost

        selected_total = max(
            ZERO,
            observation.raw_selected_total_cost
            - segment.total_baseline
            + correction_prefix.total_between(segment.started_at, observation),
        )
        selected_totals.append(selected_total)

        user_costs: list[Decimal] = []
        for user_id in ordered_users:
            selected = max(
                ZERO,
                last_raw_by_user.get(user_id, baseline_by_user.get(user_id, ZERO))
                - baseline_by_user.get(user_id, ZERO)
                + correction_prefix.user_between(
                    user_id,
                    segment.started_at,
                    observation.observed_at,
                    observation_id=observation.id,
                ),
            )
            user_costs.append(selected)
        user_total = sum(user_costs, ZERO)
        residual = max(ZERO, selected_total - user_total)
        residual_costs.append(residual)
        aggregate_cost_differences.append(selected_total - user_total)
        cost_rows.append(
            [float(value) for value in user_costs] + [float(residual)]
        )

    times = [
        (observation.observed_at - segment.started_at).total_seconds() / 3600.0
        for observation in observations
    ]
    displayed = [float(item.upstream_used_percent) for item in observations]
    observation_row_indices = list(range(len(observations)))

    if not first_is_observed_baseline:
        times.insert(0, 0.0)
        cost_rows.insert(0, [0.0] * len(subject_user_ids))
        displayed.insert(0, float(segment.percent_baseline))
        observation_row_indices = [index + 1 for index in observation_row_indices]
    for index in range(1, len(times)):
        if times[index] <= times[index - 1]:
            times[index] = times[index - 1] + 1e-9

    model_input = DynamicModelInput(
        times_hours=np.asarray(times, dtype=float),
        costs_usd=np.asarray(cost_rows, dtype=float),
        displayed_percent=np.asarray(displayed, dtype=float),
        rights_percent=rights,
        baseline_display_percent=float(segment.percent_baseline),
        baseline_exact_zero=segment.percent_baseline == ZERO,
    )
    model_input.validate()
    return DynamicReplayInput(
        model_input=model_input,
        subject_user_ids=subject_user_ids,
        participant_subject_indices=participant_subject_indices,
        observation_row_indices=tuple(observation_row_indices),
        selected_totals=tuple(selected_totals),
        residual_costs=tuple(residual_costs),
        aggregate_cost_differences=tuple(aggregate_cost_differences),
    )
