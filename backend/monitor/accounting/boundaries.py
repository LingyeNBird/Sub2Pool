"""官方窗口、管理员起点与异常回退的周期边界推断。"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from .contracts import ALGORITHM_VERSION, ReplaySegment
from ..models import Observation, ParticipantSnapshot

ZERO = Decimal("0")
RATE_METHOD = ALGORITHM_VERSION
RESET_ROLLBACK_TOLERANCE = Decimal("0.1")
RESET_TIME_TOLERANCE = timedelta(minutes=10)


def same_official_reset(left: datetime, right: datetime) -> bool:
    return abs(left - right) <= RESET_TIME_TOLERANCE


def official_start(observation: Observation) -> datetime:
    return observation.upstream_resets_at - timedelta(
        seconds=observation.window_seconds
    )


def participant_raw_costs(observation: Observation) -> dict[int, Decimal]:
    return {
        snapshot.participant_id: snapshot.raw_selected_cost
        for snapshot in observation.participant_snapshots.all()
    }


def observed_baseline_segment(
    observation: Observation,
    *,
    reason: str,
    percent_baseline: Decimal,
    cost_basis: str,
) -> ReplaySegment:
    """复用“以真实观测建立基线”之后的全部区间初始化逻辑。"""

    return ReplaySegment(
        observations=[],
        started_at=observation.observed_at,
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason=reason,
        total_baseline=observation.normalized_cost(cost_basis),
        participant_baselines=participant_raw_costs(observation),
        percent_baseline=percent_baseline,
    )


def observation_key(observation: Observation) -> tuple[datetime, int]:
    return observation.observed_at, observation.id


def manual_start_interval_end_key(
    observation: Observation,
) -> tuple[datetime, int] | None:
    if not observation.is_manual_start:
        return None
    end = observation.manual_start_end
    if end is None:
        raise ValueError("管理员起点缺少区间终点")
    if end.account_id != observation.account_id:
        raise ValueError("管理员起点区间不能跨账号")
    start_key = observation_key(observation)
    end_key = observation_key(end)
    if end_key < start_key:
        raise ValueError("管理员起点区间终点早于起点")
    return end_key


def manual_start_protected_ids(
    observations: list[Observation],
) -> set[int]:
    protected: set[int] = set()
    active_end: tuple[datetime, int] | None = None
    for observation in observations:
        key = observation_key(observation)
        if active_end is not None and key <= active_end:
            protected.add(observation.id)
            continue
        active_end = None
        end_key = manual_start_interval_end_key(observation)
        if end_key is not None:
            protected.add(observation.id)
            active_end = end_key
    return protected


def defer_redundant_zero_observations(
    observations: list[Observation],
) -> tuple[list[Observation], list[Observation]]:
    """连续 0% 仅保留末点，但管理员起点区间内的观测全部参与同一周期。"""

    protected_ids = manual_start_protected_ids(observations)
    candidates: list[Observation] = []
    deferred: list[Observation] = []
    index = 0
    while index < len(observations):
        observation = observations[index]
        if (
            observation.id in protected_ids
            or observation.upstream_used_percent != ZERO
        ):
            candidates.append(observation)
            index += 1
            continue

        zero_run = [observation]
        scan = index + 1
        while scan < len(observations):
            candidate = observations[scan]
            if (
                candidate.id in protected_ids
                or candidate.upstream_used_percent != ZERO
            ):
                break
            zero_run.append(candidate)
            scan += 1

        previous = candidates[-1] if candidates else None
        same_window_rollback = bool(
            previous is not None
            and previous.upstream_used_percent > ZERO
            and same_official_reset(
                previous.upstream_resets_at,
                zero_run[0].upstream_resets_at,
            )
        )
        if same_window_rollback:
            candidates.extend(zero_run)
        else:
            deferred.extend(zero_run[:-1])
            candidates.append(zero_run[-1])
        index = scan
    return candidates, deferred


def mark_deferred_zero_observations(
    observations: list[Observation],
) -> None:
    """清除被后续 0% 取代的派生结果，原始采样事实仍完整保留。"""

    note = "连续 0% 空闲观测，等待首次使用后确认周期"
    for observation in observations:
        observation.attribution_started_at = None
        observation.selected_total_cost = ZERO
        observation.interval_used_percent = ZERO
        observation.delta_percent = None
        observation.delta_cost = None
        observation.sample_usd_per_percent = None
        observation.estimated_used_percent = ZERO
        observation.capacity_lower_usd = None
        observation.capacity_upper_usd = None
        observation.model_diagnostics = {}
        observation.valid_sample = False
        observation.sample_note = note
        raw_window = dict(observation.raw_window)
        raw_window.pop("replay_segment_reason", None)
        raw_window.update(
            {
                "rate_method": RATE_METHOD,
                "replay_decision": "deferred_zero_plateau",
            }
        )
        observation.raw_window = raw_window

        snapshots = list(observation.participant_snapshots.all())
        for snapshot in snapshots:
            snapshot.selected_cost = ZERO
            snapshot.delta_cost = None
            snapshot.charged_delta_percent = ZERO
            snapshot.charged_cycle_percent = ZERO
            snapshot.remaining_share_percent = snapshot.share_percent
            snapshot.recommended_balance_usd = None
            snapshot.charged_percent_lower = None
            snapshot.charged_percent_upper = None
            snapshot.recommended_balance_min_usd = None
            snapshot.recommended_balance_max_usd = None
            snapshot.deterministic_balance_min_usd = None
            snapshot.deterministic_balance_max_usd = None
            snapshot.balance_difference_usd = None
            snapshot.needs_manual_update = False
            snapshot.reason = note
        if snapshots:
            ParticipantSnapshot.objects.bulk_update(
                snapshots,
                [
                    "selected_cost",
                    "delta_cost",
                    "charged_delta_percent",
                    "charged_cycle_percent",
                    "remaining_share_percent",
                    "recommended_balance_usd",
                    "charged_percent_lower",
                    "charged_percent_upper",
                    "recommended_balance_min_usd",
                    "recommended_balance_max_usd",
                    "deterministic_balance_min_usd",
                    "deterministic_balance_max_usd",
                    "balance_difference_usd",
                    "needs_manual_update",
                    "reason",
                ],
            )


def waiting_for_first_use(segment: ReplaySegment) -> bool:
    return (
        segment.reason in {"official_zero_observation", "manual_override"}
        and all(
            observation.upstream_used_percent == ZERO
            for observation in segment.observations
        )
    )


def official_segment(observation: Observation, cost_basis: str) -> ReplaySegment:
    """建立官方窗口区间，并优先采用首个 0% 观测的累计成本基线。

    Sub2API 的聚合用量接口按自然日统计。官方窗口若在当天零点之后重置，
    该接口会把零点至重置时刻的旧周期成本一并返回。首个 0% 观测是能够
    直接确认的新周期零点，因此必须扣除它当时的累计成本；否则这段旧周期
    成本会被错误折算进新周期容量。
    """

    if observation.upstream_used_percent == ZERO:
        return observed_baseline_segment(
            observation,
            reason="official_zero_observation",
            percent_baseline=ZERO,
            cost_basis=cost_basis,
        )
    return ReplaySegment(
        observations=[],
        started_at=official_start(observation),
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason="official_window",
        total_baseline=ZERO,
        participant_baselines={},
        percent_baseline=ZERO,
    )


def manual_start_segment(observation: Observation, cost_basis: str) -> ReplaySegment:
    """管理员起点以该观测的累计成本和百分比作为新的零基线。"""

    return observed_baseline_segment(
        observation,
        reason="manual_override",
        percent_baseline=observation.upstream_used_percent,
        cost_basis=cost_basis,
    )


def mark_automatic_exclusion(
    observations: list[Observation],
    reason: str,
) -> None:
    excluded_at = timezone.now()
    for observation in observations:
        observation.excluded_at = excluded_at
        observation.exclusion_source = "automatic"
        observation.exclusion_reason = reason
        observation.attribution_started_at = None
        observation.selected_total_cost = observation.raw_selected_total_cost
        observation.interval_used_percent = ZERO
        observation.delta_percent = None
        observation.delta_cost = None
        observation.sample_usd_per_percent = None
        observation.estimated_used_percent = ZERO
        observation.capacity_lower_usd = None
        observation.capacity_upper_usd = None
        observation.model_diagnostics = {}
        observation.valid_sample = False
        observation.sample_note = f"已自动排除：{reason}"
        raw_window = dict(observation.raw_window)
        raw_window.pop("reset_candidate_status", None)
        raw_window.pop("previous_observation_id", None)
        raw_window.update(
            {
                "rate_method": RATE_METHOD,
                "replay_decision": "automatic_exclusion",
            }
        )
        observation.raw_window = raw_window

        snapshots = list(observation.participant_snapshots.all())
        for snapshot in snapshots:
            snapshot.selected_cost = snapshot.raw_selected_cost
            snapshot.delta_cost = None
            snapshot.charged_delta_percent = ZERO
            snapshot.charged_cycle_percent = ZERO
            snapshot.remaining_share_percent = snapshot.share_percent
            snapshot.recommended_balance_usd = None
            snapshot.charged_percent_lower = None
            snapshot.charged_percent_upper = None
            snapshot.recommended_balance_min_usd = None
            snapshot.recommended_balance_max_usd = None
            snapshot.deterministic_balance_min_usd = None
            snapshot.deterministic_balance_max_usd = None
            snapshot.balance_difference_usd = None
            snapshot.needs_manual_update = False
            snapshot.reason = "该观测已排除，不参与归属计算"
        if snapshots:
            ParticipantSnapshot.objects.bulk_update(
                snapshots,
                [
                    "selected_cost",
                    "delta_cost",
                    "charged_delta_percent",
                    "charged_cycle_percent",
                    "remaining_share_percent",
                    "recommended_balance_usd",
                    "charged_percent_lower",
                    "charged_percent_upper",
                    "recommended_balance_min_usd",
                    "recommended_balance_max_usd",
                    "deterministic_balance_min_usd",
                    "deterministic_balance_max_usd",
                    "balance_difference_usd",
                    "needs_manual_update",
                    "reason",
                ],
            )


def infer_segments(
    observations: list[Observation],
    cost_basis: str,
) -> tuple[list[ReplaySegment], list[Observation], list[Observation]]:
    """按“管理员起点区间 > 官方窗口 > 异常检测”识别派生区间。

    管理员区间从开始记录到结束记录（均包含）强制属于同一周期；区间内的
    0% 观测、官方重置时间变化和其他起点标记都不会再次切分周期。开始与
    结束相同时保持旧版单点起点语义。

    连续 0% 只说明账号尚未开始消费，不能用持续漂移的重置时间建立多个
    空周期。系统保留全部原始点，但只把最后一个 0% 作为待确认基线；首次
    正百分比出现后，再用它返回的重置时间确认该区间。

    官方重置时间没有变化时，百分比回退与七天窗口证据矛盾，不能再凭
    连续低点擅自建立新区间。此类低点保持自动排除，直到 reset_at
    变化或管理员明确设置起点区间。
    """

    segments: list[ReplaySegment] = []
    automatic: list[Observation] = []
    current: ReplaySegment | None = None
    active_manual_end: tuple[datetime, int] | None = None
    observations, deferred = defer_redundant_zero_observations(observations)
    index = 0

    while index < len(observations):
        observation = observations[index]
        key = observation_key(observation)
        if active_manual_end is not None:
            if key <= active_manual_end:
                if current is None:
                    raise ValueError("管理员起点区间缺少开始记录")
                current.resets_at = observation.upstream_resets_at
                current.observations.append(observation)
                index += 1
                continue
            active_manual_end = None
        if observation.is_manual_start:
            if current is not None and current.observations:
                segments.append(current)
            current = manual_start_segment(observation, cost_basis)
            current.observations.append(observation)
            active_manual_end = manual_start_interval_end_key(observation)
            index += 1
            continue

        if current is not None and waiting_for_first_use(current):
            current.resets_at = observation.upstream_resets_at
            current.observations.append(observation)
            index += 1
            continue

        if current is None or not same_official_reset(
            observation.upstream_resets_at,
            current.resets_at,
        ):
            if current is not None and current.observations:
                segments.append(current)
            current = official_segment(observation, cost_basis)
            current.observations.append(observation)
            index += 1
            continue

        previous = current.observations[-1]
        rollback = (
            observation.upstream_used_percent + RESET_ROLLBACK_TOLERANCE
            < previous.upstream_used_percent
        )
        if not rollback:
            current.observations.append(observation)
            index += 1
            continue

        low_run = [observation]
        scan = index + 1
        recovered = False
        while scan < len(observations):
            candidate = observations[scan]
            if candidate.is_manual_start or not same_official_reset(
                candidate.upstream_resets_at,
                current.resets_at,
            ):
                break
            if candidate.upstream_used_percent + RESET_ROLLBACK_TOLERANCE >= (
                previous.upstream_used_percent
            ):
                recovered = True
                break
            low_run.append(candidate)
            scan += 1

        reason = (
            "后续快照恢复到回退前进度，判定为瞬时异常"
            if recovered
            else "百分比回退但官方重置时间未变化，等待官方窗口更新或管理员设置起点"
        )
        mark_automatic_exclusion(low_run, reason)
        automatic.extend(low_run)
        index = scan

    if current is not None and current.observations:
        segments.append(current)
    return segments, automatic, deferred
