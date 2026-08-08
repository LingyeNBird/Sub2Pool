"""从不可变原始采样重放额度折算、重置边界和参与者归属。

这里没有需要维护的“周期”数据库实体。每次新增、排除或恢复观测后，系统都会按时间顺序
重新读取该上游账号的全部原始采样：先识别可靠的官方重置或人工刷新边界，再重算所有派生字段。
因此任何结果都能由当前原始数据复现，管理员排除一条错误记录后也不会留下旧周期状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")
RATE_PRECISION = Decimal("0.000001")
RATE_METHOD = "full_replay_v1"
RESET_ROLLBACK_TOLERANCE = Decimal("0.1")
RESET_TIME_TOLERANCE = timedelta(minutes=5)
INDEPENDENT_SAMPLE_GAP = timedelta(minutes=1)


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


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)


def _same_official_reset(left: datetime, right: datetime) -> bool:
    return abs(left - right) <= RESET_TIME_TOLERANCE


def _sample_key(observation: Observation) -> str | None:
    sampled_at = observation.raw_window.get("sampled_at")
    return str(sampled_at) if sampled_at else None


def _is_independent(
    observation: Observation,
    accepted: list[Observation],
) -> bool:
    """优先按上游快照时间去重；旧数据无快照时间时要求至少间隔一分钟。"""

    key = _sample_key(observation)
    if key is not None:
        return all(_sample_key(item) != key for item in accepted)
    return all(
        abs(observation.observed_at - item.observed_at)
        >= INDEPENDENT_SAMPLE_GAP
        for item in accepted
    )


def _weighted_percentile(
    samples: list[tuple[Decimal, Decimal]],
    percentile: int,
) -> Decimal:
    ordered = sorted(
        (
            (rate, max(weight, Decimal("0.0001")))
            for rate, weight in samples
            if rate > 0
        ),
        key=lambda item: item[0],
    )
    if not ordered:
        raise ValueError("no samples")
    target = (
        sum((weight for _rate, weight in ordered), ZERO)
        * Decimal(percentile)
        / Decimal(100)
    )
    cumulative = ZERO
    for rate, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return rate
    return ordered[-1][0]


def _participant_raw_costs(observation: Observation) -> dict[int, Decimal]:
    return {
        snapshot.participant_id: snapshot.raw_selected_cost
        for snapshot in observation.participant_snapshots.all()
    }


def _official_segment(observation: Observation) -> ReplaySegment:
    started_at = observation.upstream_resets_at - timedelta(
        seconds=observation.window_seconds
    )
    return ReplaySegment(
        observations=[],
        started_at=started_at,
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason="official_window",
        total_baseline=ZERO,
        participant_baselines={},
    )


def _manual_refresh_segment(observation: Observation) -> ReplaySegment:
    return ReplaySegment(
        observations=[],
        started_at=observation.observed_at,
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason="confirmed_manual_refresh",
        total_baseline=observation.raw_selected_total_cost,
        participant_baselines=_participant_raw_costs(observation),
    )


def _mark_automatic_exclusion(
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
        observation.delta_percent = None
        observation.delta_cost = None
        observation.sample_usd_per_percent = None
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
            snapshot.remaining_share_percent = snapshot.participant.share_percent
            snapshot.recommended_balance_usd = None
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
                    "balance_difference_usd",
                    "needs_manual_update",
                    "reason",
                ],
            )


def _infer_segments(
    observations: list[Observation],
) -> tuple[list[ReplaySegment], list[Observation]]:
    """识别官方窗口、已确认人工刷新和单点异常回退。

    同一官方 reset_at 下的百分比回退不能凭单点直接判定刷新：
    * 回退后只有一份独立快照：先自动排除，等待后续数据；
    * 下一份快照恢复到回退前进度：确认是瞬时异常，继续排除低点；
    * 两份独立快照持续位于回退后的低位：确认人工刷新并建立新的派生区间；
    * 管理员恢复的回退点：人工判断优先，立即作为新的派生区间。
    """

    segments: list[ReplaySegment] = []
    automatic: list[Observation] = []
    current: ReplaySegment | None = None
    index = 0

    while index < len(observations):
        observation = observations[index]
        if current is None or not _same_official_reset(
            observation.upstream_resets_at,
            current.resets_at,
        ):
            if current is not None and current.observations:
                segments.append(current)
            current = _official_segment(observation)
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

        if observation.force_included:
            if current.observations:
                segments.append(current)
            current = _manual_refresh_segment(observation)
            current.observations.append(observation)
            index += 1
            continue

        low_run = [observation]
        independent = [observation]
        scan = index + 1
        recovered = False
        confirmed = False
        while scan < len(observations):
            candidate = observations[scan]
            if not _same_official_reset(
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
            if candidate.force_included or _is_independent(candidate, independent):
                independent.append(candidate)
            if len(independent) >= 2 or candidate.force_included:
                confirmed = True
                scan += 1
                break
            scan += 1

        if confirmed:
            if current.observations:
                segments.append(current)
            current = _manual_refresh_segment(low_run[0])
            current.observations.extend(low_run)
            index = scan
            continue

        reason = (
            "后续快照恢复到回退前进度，判定为瞬时异常"
            if recovered
            else "仅有一份独立快照显示百分比回退，等待后续采样确认"
        )
        _mark_automatic_exclusion(low_run, reason)
        automatic.extend(low_run)
        index = scan

    if current is not None and current.observations:
        segments.append(current)
    return segments, automatic


def _sample_note(
    previous: Observation | None,
    delta_percent: Decimal | None,
    delta_cost: Decimal | None,
    valid_sample: bool,
    rate_source: str,
) -> str:
    if rate_source == "previous_interval_history":
        return "当前区间尚无有效样本，暂沿用上一归属区间的有效估值"
    if previous is None:
        return (
            "当前归属区间累计口径初始化样本"
            if valid_sample
            else "区间首次观测，当前没有足够数据形成累计口径样本"
        )
    if valid_sample:
        return "有效累计口径样本"
    if delta_percent == 0:
        return "上游百分比未变化，本次不更新美元/百分比"
    if delta_percent is not None and delta_percent < 0:
        return "上游百分比回退，本次不倒扣参与者账本"
    if delta_cost is not None and delta_cost <= 0:
        return "成本没有正向变化，本次样本无效"
    return "本次样本无效"


def _replay_segment(
    segment: ReplaySegment,
    config: AppSettings,
    fallback_rate: Decimal | None,
) -> tuple[int, Decimal | None]:
    previous: Observation | None = None
    previous_snapshots: dict[int, ParticipantSnapshot] = {}
    rate_history: list[tuple[Decimal, Decimal]] = []
    latest_effective = fallback_rate
    has_valid_rate = False

    for observation in segment.observations:
        selected_total = max(
            ZERO,
            observation.raw_selected_total_cost - segment.total_baseline,
        )
        delta_percent = (
            observation.upstream_used_percent - previous.upstream_used_percent
            if previous is not None
            else None
        )
        delta_cost = (
            selected_total - previous.selected_total_cost
            if previous is not None
            else None
        )
        valid_sample = bool(
            selected_total > 0
            and observation.upstream_used_percent > 0
            and (
                previous is None
                or (
                    delta_percent is not None
                    and delta_percent > 0
                    and delta_cost is not None
                    and delta_cost > 0
                )
            )
        )
        sample_rate = (
            _quantize_rate(
                selected_total / observation.upstream_used_percent
            )
            if valid_sample
            else None
        )
        candidates = rate_history[-max(0, config.rate_history_samples - 1) :]
        if sample_rate is not None:
            candidates = [
                *candidates,
                (sample_rate, observation.upstream_used_percent),
            ]
        if candidates:
            effective_rate = _quantize_rate(
                _weighted_percentile(
                    candidates,
                    config.conservative_percentile,
                )
            )
            rate_source = "current_interval_samples"
        elif fallback_rate is not None:
            effective_rate = fallback_rate
            rate_source = "previous_interval_history"
        else:
            effective_rate = _quantize_rate(config.initial_usd_per_percent)
            rate_source = "initial_fallback"
        latest_effective = effective_rate

        observation.attribution_started_at = segment.started_at
        observation.selected_total_cost = selected_total
        observation.delta_percent = delta_percent
        observation.delta_cost = delta_cost
        observation.sample_usd_per_percent = sample_rate
        observation.effective_usd_per_percent = effective_rate
        observation.valid_sample = valid_sample
        observation.sample_note = _sample_note(
            previous,
            delta_percent,
            delta_cost,
            valid_sample,
            rate_source,
        )
        raw_window = dict(observation.raw_window)
        raw_window.pop("reset_candidate_status", None)
        raw_window.pop("previous_observation_id", None)
        raw_window.update(
            {
                "rate_method": RATE_METHOD,
                "rate_source": rate_source,
                "conservative_percentile": config.conservative_percentile,
                "rate_history_samples": config.rate_history_samples,
                "replay_segment_reason": segment.reason,
                "replay_decision": "included",
            }
        )
        observation.raw_window = raw_window
        observation.save(
            update_fields=[
                "attribution_started_at",
                "selected_total_cost",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "effective_usd_per_percent",
                "valid_sample",
                "sample_note",
                "raw_window",
            ]
        )
        if sample_rate is not None:
            has_valid_rate = True
            rate_history.append(
                (sample_rate, observation.upstream_used_percent)
            )

        snapshots = list(observation.participant_snapshots.all())
        participant_deltas: dict[int, Decimal] = {}
        for snapshot in snapshots:
            snapshot.selected_cost = max(
                ZERO,
                snapshot.raw_selected_cost
                - segment.participant_baselines.get(snapshot.participant_id, ZERO),
            )
            old = previous_snapshots.get(snapshot.participant_id)
            participant_deltas[snapshot.participant_id] = (
                snapshot.selected_cost - old.selected_cost
                if old is not None
                else snapshot.selected_cost
            )
        positive_total = sum(
            (max(ZERO, value) for value in participant_deltas.values()),
            ZERO,
        )
        attribution_total = (
            selected_total
            if previous is None
            else max(ZERO, delta_cost or ZERO)
        )
        denominator = max(attribution_total, positive_total)

        for snapshot in snapshots:
            old = previous_snapshots.get(snapshot.participant_id)
            participant_delta = participant_deltas[snapshot.participant_id]
            positive_delta = max(ZERO, participant_delta)
            charged_delta = ZERO
            if denominator > 0:
                if previous is None:
                    charged_delta = (
                        observation.upstream_used_percent
                        * positive_delta
                        / denominator
                    )
                elif valid_sample and delta_percent is not None:
                    charged_delta = (
                        delta_percent * positive_delta / denominator
                    )
            charged = max(
                ZERO,
                (old.charged_cycle_percent if old is not None else ZERO)
                + charged_delta,
            )
            remaining = max(
                ZERO,
                snapshot.participant.share_percent - charged,
            )
            recommended = (
                remaining * effective_rate * config.safety_factor
            ).quantize(CENT, rounding=ROUND_HALF_UP)
            balance = snapshot.current_balance_usd
            difference = (
                (recommended - balance).quantize(CENT, rounding=ROUND_HALF_UP)
                if balance is not None
                else None
            )
            exhausted = bool(
                balance is not None and balance <= config.limit_warning_usd
            )
            needs_update = bool(
                difference is not None
                and (
                    abs(difference) >= config.recommendation_change_usd
                    or (exhausted and remaining > 0)
                )
            )
            if remaining <= 0:
                reason = "本上游周期的百分比权益已用尽"
            elif exhausted:
                reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
            elif needs_update:
                reason = "当前用户余额与最新测算建议差异较大"
            else:
                reason = "当前用户余额无需调整"

            snapshot.delta_cost = (
                None if old is None else participant_delta
            )
            snapshot.charged_delta_percent = charged_delta.quantize(
                PCT_PRECISION,
                rounding=ROUND_HALF_UP,
            )
            snapshot.charged_cycle_percent = charged.quantize(
                PCT_PRECISION,
                rounding=ROUND_HALF_UP,
            )
            snapshot.remaining_share_percent = remaining.quantize(
                PCT_PRECISION,
                rounding=ROUND_HALF_UP,
            )
            snapshot.recommended_balance_usd = recommended
            snapshot.balance_difference_usd = difference
            snapshot.needs_manual_update = needs_update
            snapshot.reason = reason
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
                    "balance_difference_usd",
                    "needs_manual_update",
                    "reason",
                ],
            )
        previous = observation
        previous_snapshots = {
            snapshot.participant_id: snapshot for snapshot in snapshots
        }

    return (
        len(segment.observations),
        latest_effective if has_valid_rate else fallback_rate,
    )


def _replay_usage_samples(
    account_id: int,
    segments: list[ReplaySegment],
) -> None:
    if not segments:
        return
    samples = list(
        ParticipantUsageSample.objects.filter(account_id=account_id).order_by(
            "observed_at",
            "id",
        )
    )
    for sample in samples:
        segment = segments[0]
        for candidate in segments:
            if candidate.first_observed_at <= sample.observed_at:
                segment = candidate
            else:
                break
        sample.attribution_started_at = segment.started_at
        sample.selected_cost = max(
            ZERO,
            sample.raw_selected_cost
            - segment.participant_baselines.get(sample.participant_id, ZERO),
        )
    if samples:
        ParticipantUsageSample.objects.bulk_update(
            samples,
            ["attribution_started_at", "selected_cost"],
        )


def _update_participant_latest(segments: list[ReplaySegment]) -> None:
    if not segments:
        return
    latest = segments[-1].observations[-1]
    snapshots = list(latest.participant_snapshots.all())
    for snapshot in snapshots:
        snapshot.participant.latest_selected_cost = snapshot.selected_cost
        snapshot.participant.latest_balance_usd = snapshot.current_balance_usd
        snapshot.participant.last_checked_at = latest.observed_at
    if snapshots:
        Participant.objects.bulk_update(
            [snapshot.participant for snapshot in snapshots],
            ["latest_selected_cost", "latest_balance_usd", "last_checked_at"],
        )


@transaction.atomic
def rebuild_account(
    account_id: int,
    config: AppSettings | None = None,
) -> ReplayResult:
    """锁定并重放一个上游账号的全部原始观测。"""

    config = config or AppSettings.load()
    observations = list(
        Observation.objects.select_for_update()
        .filter(account_id=account_id)
        .prefetch_related("participant_snapshots__participant")
        .order_by("observed_at", "id")
    )
    if not observations:
        return ReplayResult(0, 0, 0, None)

    # 自动判定不是持久事实。每次先撤销旧自动判定，再用完整数据重新识别；
    # 管理员手动排除则保持不变，恢复过的记录由 force_included 明确覆盖检测。
    for observation in observations:
        if observation.exclusion_source == "automatic":
            observation.excluded_at = None
            observation.exclusion_source = ""
            observation.exclusion_reason = ""
            observation.save(
                update_fields=[
                    "excluded_at",
                    "exclusion_source",
                    "exclusion_reason",
                ]
            )

    candidates = [
        observation
        for observation in observations
        if observation.exclusion_source != "manual"
    ]
    segments, automatic = _infer_segments(candidates)
    if automatic:
        Observation.objects.bulk_update(
            automatic,
            [
                "excluded_at",
                "exclusion_source",
                "exclusion_reason",
                "attribution_started_at",
                "selected_total_cost",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "valid_sample",
                "sample_note",
                "raw_window",
            ],
        )

    rebuilt = 0
    fallback_rate: Decimal | None = None
    for segment in segments:
        count, fallback_rate = _replay_segment(
            segment,
            config,
            fallback_rate,
        )
        rebuilt += count

    _replay_usage_samples(account_id, segments)
    _update_participant_latest(segments)
    latest_id = (
        segments[-1].observations[-1].pk if segments else None
    )
    return ReplayResult(
        rebuilt_observations=rebuilt,
        automatic_exclusions=len(automatic),
        inferred_intervals=len(segments),
        latest_observation_id=latest_id,
    )


@transaction.atomic
def exclude_observation(
    observation: Observation,
    reason: str = "管理员手动排除",
) -> dict[str, int | bool | None]:
    """保留原始记录但从所有后续重放中忽略它。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_excluded = observation.exclusion_source == "manual"
    observation.excluded_at = timezone.now()
    observation.exclusion_source = "manual"
    observation.exclusion_reason = reason.strip()[:255] or "管理员手动排除"
    observation.force_included = False
    observation.valid_sample = False
    observation.sample_usd_per_percent = None
    observation.sample_note = f"已排除：{observation.exclusion_reason}"
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "force_included",
            "valid_sample",
            "sample_usd_per_percent",
            "sample_note",
        ]
    )
    replay = rebuild_account(observation.account_id)
    return {"already_excluded": already_excluded, **replay.as_dict()}


@transaction.atomic
def restore_observation(
    observation: Observation,
) -> dict[str, int | bool | None]:
    """恢复一条排除记录；若它本身回退，视为管理员确认的新边界。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_included = observation.excluded_at is None
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.force_included = True
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "force_included",
        ]
    )
    replay = rebuild_account(observation.account_id)
    observation.refresh_from_db()
    return {
        "already_included": already_included,
        "included": observation.excluded_at is None,
        **replay.as_dict(),
    }
