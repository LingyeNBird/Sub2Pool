"""官方窗口、管理员起点与异常回退的周期边界推断。"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from .contracts import ALGORITHM_VERSION, ReplaySegment
from ..models import (
    CPAAccountCollectionInterval,
    Observation,
    ParticipantSnapshot,
)

ZERO = Decimal("0")
RATE_METHOD = ALGORITHM_VERSION
RESET_ROLLBACK_TOLERANCE = Decimal("0.1")
RESET_TIME_TOLERANCE = timedelta(minutes=10)


def same_official_reset(left: datetime, right: datetime) -> bool:
    return abs(left - right) <= RESET_TIME_TOLERANCE


def official_reset_advanced(candidate: datetime, current: datetime) -> bool:
    return candidate - current > RESET_TIME_TOLERANCE


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


def waiting_for_first_use(segment: ReplaySegment) -> bool:
    return (
        segment.reason
        in {
            "official_zero_observation",
            "manual_override",
            "provider_collection_baseline",
        }
        and all(
            observation.upstream_used_percent == ZERO
            for observation in segment.observations
        )
    )


def official_segment(
    observation: Observation,
    cost_basis: str,
    *,
    collection_baseline: bool = False,
) -> ReplaySegment:
    """建立官方窗口区间，并优先采用首个 0% 观测的累计成本基线。

    Sub2API 的聚合用量接口按自然日统计。官方窗口若在当天零点之后重置，
    该接口会把零点至重置时刻的旧周期成本一并返回。首个 0% 观测是能够
    直接确认的新周期零点，因此必须扣除它当时的累计成本；否则这段旧周期
    成本会被错误折算进新周期容量。
    """
    if collection_baseline:
        return observed_baseline_segment(
            observation,
            reason="provider_collection_baseline",
            percent_baseline=observation.upstream_used_percent,
            cost_basis=cost_basis,
        )

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
    *,
    collection_intervals: list[CPAAccountCollectionInterval] | None = None,
    collection_history: list[Observation] | None = None,
) -> tuple[list[ReplaySegment], list[Observation]]:
    """按“管理员起点区间 > 官方窗口 > 异常检测”识别派生区间。

    管理员区间从开始记录到结束记录（均包含）强制属于同一周期；区间内的
    0% 观测、官方重置时间变化和其他起点标记都不会再次切分周期。开始与
    结束相同时保持旧版单点起点语义。

    上游报告的 ``reset_at`` 显著向后推进时，首个 0% 观测建立新官方周期的
    固定基线；后续连续 0% 全部延续该周期并保留累计成本增量。首次使用
    之前的重置时间漂移不再移动基线或建立额外空周期。

    如果候选新窗口在首次使用前暂时恢复旧 ``reset_at``，但后续观测再次
    报告候选窗口，则中间恢复点属于瞬时异常，候选窗口继续生效。只有没有
    后续候选窗口证据，且 ``reset_at`` 和百分比都恢复到前一窗口时，才排除
    候选窗口中的 0% 并让恢复点继续前一周期。

    官方重置时间没有变化时，百分比回退与七天窗口证据矛盾，不能再凭
    连续低点擅自建立新区间。此类低点保持自动排除，直到 reset_at
    变化或管理员明确设置起点区间。CPA 百分比观测是否处于连续采集覆盖内，
    由独立持久化的账号采集区间决定；百分比记录本身不再承担 RESP 连接状态。
    """

    automatic: list[Observation] = []
    covered_observations: list[Observation] = []
    collection_baseline_ids: set[int] = set()
    if collection_intervals is None:
        covered_observations = [
            observation
            for observation in observations
            if observation.exclusion_source != "manual"
        ]
    else:
        intervals = sorted(
            collection_intervals,
            key=lambda interval: (interval.connected_at, interval.id),
        )

        def covering_interval(
            observation: Observation,
        ) -> CPAAccountCollectionInterval | None:
            return next(
                (
                    candidate
                    for candidate in reversed(intervals)
                    if candidate.connected_at <= observation.observed_at
                    and (
                        candidate.disconnected_at is None
                        or observation.observed_at <= candidate.disconnected_at
                    )
                ),
                None,
            )

        seen_intervals: set[int] = set()
        for observation in collection_history or observations:
            if observation.exclusion_source == "manual":
                continue
            interval = covering_interval(observation)
            if interval is not None and interval.id not in seen_intervals:
                collection_baseline_ids.add(observation.id)
                seen_intervals.add(interval.id)

        for observation in observations:
            if observation.exclusion_source == "manual":
                continue
            interval = covering_interval(observation)
            if interval is None:
                mark_automatic_exclusion(
                    [observation],
                    "CPA usage 采集区间未覆盖该百分比观测",
                )
                automatic.append(observation)
                continue
            covered_observations.append(observation)

    observations = covered_observations
    segments: list[ReplaySegment] = []
    current: ReplaySegment | None = None
    active_manual_end: tuple[datetime, int] | None = None
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

        if observation.id in collection_baseline_ids:
            if current is not None and current.observations:
                segments.append(current)
            current = official_segment(
                observation,
                cost_basis,
                collection_baseline=True,
            )
            current.observations.append(observation)
            index += 1
            continue

        if current is not None and waiting_for_first_use(current):
            transient_reversion: list[Observation] = []
            if (
                current.reason
                in {
                    "official_zero_observation",
                    "provider_collection_baseline",
                }
                and not same_official_reset(
                    observation.upstream_resets_at,
                    current.resets_at,
                )
            ):
                scan = index
                while scan < len(observations):
                    candidate = observations[scan]
                    if candidate.is_manual_start or same_official_reset(
                        candidate.upstream_resets_at,
                        current.resets_at,
                    ):
                        break
                    transient_reversion.append(candidate)
                    scan += 1
                candidate_window_confirmed = (
                    scan < len(observations)
                    and not observations[scan].is_manual_start
                    and same_official_reset(
                        observations[scan].upstream_resets_at,
                        current.resets_at,
                    )
                )
                if candidate_window_confirmed:
                    reason = "后续快照再次确认候选窗口，判定中间窗口恢复为瞬时异常"
                    mark_automatic_exclusion(
                        transient_reversion,
                        reason,
                    )
                    automatic.extend(transient_reversion)
                    index = scan
                    continue
            previous_segment = segments[-1] if segments else None
            restored_previous_window = (
                current.reason
                in {
                    "official_zero_observation",
                    "provider_collection_baseline",
                }
                and previous_segment is not None
                and bool(previous_segment.observations)
                and same_official_reset(
                    observation.upstream_resets_at,
                    previous_segment.resets_at,
                )
                and observation.upstream_used_percent
                + RESET_ROLLBACK_TOLERANCE
                >= previous_segment.observations[-1].upstream_used_percent
            )
            if restored_previous_window:
                reason = "上游重置时间和百分比恢复到前一窗口，判定候选重置为瞬时异常"
                mark_automatic_exclusion(current.observations, reason)
                automatic.extend(current.observations)
                current = segments.pop()
                current.observations.append(observation)
                index += 1
                continue
            current.resets_at = observation.upstream_resets_at
            current.observations.append(observation)
            index += 1
            continue

        if current is None or official_reset_advanced(
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
    return segments, automatic
