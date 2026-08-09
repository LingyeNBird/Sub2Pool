"""从不可变原始采样重放额度折算、重置边界和参与者归属。

系统永久保留全部原始采样，但日常只从最早受影响的区间起点向后重放。官方
``reset_at - window`` 是确定性边界；管理员指定的观测起点优先级更高。
排除、恢复或新增记录都不会改写不可能受影响的更早区间。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .fast_correction import FastCorrectionPrefix

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
RATE_METHOD = "boundary_suffix_replay_v3"
RESET_ROLLBACK_TOLERANCE = Decimal("0.1")
RESET_TIME_TOLERANCE = timedelta(minutes=5)


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


def _quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)


def _same_official_reset(left: datetime, right: datetime) -> bool:
    return abs(left - right) <= RESET_TIME_TOLERANCE


def _official_start(observation: Observation) -> datetime:
    return observation.upstream_resets_at - timedelta(
        seconds=observation.window_seconds
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

def _observed_baseline_segment(
    observation: Observation,
    *,
    reason: str,
    percent_baseline: Decimal,
) -> ReplaySegment:
    """复用“以真实观测建立基线”之后的全部区间初始化逻辑。"""

    return ReplaySegment(
        observations=[],
        started_at=observation.observed_at,
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason=reason,
        total_baseline=observation.raw_selected_total_cost,
        participant_baselines=_participant_raw_costs(observation),
        percent_baseline=percent_baseline,
    )


def _official_segment(observation: Observation) -> ReplaySegment:
    """建立官方窗口区间，并优先采用首个 0% 观测的累计成本基线。

    Sub2API 的聚合用量接口按自然日统计。官方窗口若在当天零点之后重置，
    该接口会把零点至重置时刻的旧周期成本一并返回。首个 0% 观测是能够
    直接确认的新周期零点，因此必须扣除它当时的累计成本；否则这段旧周期
    成本会被错误折算进新周期容量。
    """

    if observation.upstream_used_percent == ZERO:
        return _observed_baseline_segment(
            observation,
            reason="official_zero_observation",
            percent_baseline=ZERO,
        )
    return ReplaySegment(
        observations=[],
        started_at=_official_start(observation),
        first_observed_at=observation.observed_at,
        resets_at=observation.upstream_resets_at,
        reason="official_window",
        total_baseline=ZERO,
        participant_baselines={},
        percent_baseline=ZERO,
    )


def _manual_start_segment(observation: Observation) -> ReplaySegment:
    """管理员起点以该观测的累计成本和百分比作为新的零基线。"""

    return _observed_baseline_segment(
        observation,
        reason="manual_override",
        percent_baseline=observation.upstream_used_percent,
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
        observation.interval_used_percent = ZERO
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
    """按“管理员起点 > 官方窗口 > 异常检测”识别派生区间。

    官方重置时间没有变化时，百分比回退与七天窗口证据矛盾，不能再凭
    连续低点擅自建立新区间。此类低点保持自动排除，直到 reset_at
    变化或管理员明确把某个观测设为起点。
    """

    segments: list[ReplaySegment] = []
    automatic: list[Observation] = []
    current: ReplaySegment | None = None
    index = 0

    while index < len(observations):
        observation = observations[index]
        if observation.is_manual_start:
            if current is not None and current.observations:
                segments.append(current)
            current = _manual_start_segment(observation)
            current.observations.append(observation)
            index += 1
            continue

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

        low_run = [observation]
        scan = index + 1
        recovered = False
        while scan < len(observations):
            candidate = observations[scan]
            if candidate.is_manual_start or not _same_official_reset(
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
    *,
    previous_observation: Observation | None = None,
    rate_history_seed: list[tuple[Decimal, Decimal]] | None = None,
    correction_prefix: FastCorrectionPrefix,
) -> tuple[int, Decimal | None]:
    previous = previous_observation
    previous_snapshots = (
        {
            snapshot.participant_id: snapshot
            for snapshot in previous.participant_snapshots.all()
        }
        if previous is not None
        else {}
    )
    rate_history = list(rate_history_seed or [])
    latest_effective = fallback_rate
    has_valid_rate = False

    for observation in segment.observations:
        selected_total = max(
            ZERO,
            observation.raw_selected_total_cost
            - segment.total_baseline
            + correction_prefix.total_between(
                segment.started_at,
                observation,
            ),
        )
        interval_percent = max(
            ZERO,
            observation.upstream_used_percent - segment.percent_baseline,
        )
        delta_percent = (
            interval_percent - previous.interval_used_percent
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
            and interval_percent > 0
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
            _quantize_rate(selected_total / interval_percent)
            if valid_sample
            else None
        )
        previous_count = max(0, config.rate_history_samples - 1)
        history_start = max(0, len(rate_history) - previous_count)
        candidates = rate_history[history_start : len(rate_history)]
        if sample_rate is not None:
            candidates = [
                *candidates,
                (sample_rate, interval_percent),
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
        observation.interval_used_percent = interval_percent
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
        snapshots = list(observation.participant_snapshots.all())
        current_participant_ids = {
            snapshot.participant_id for snapshot in snapshots
        }
        previous_participant_ids = set(previous_snapshots)
        participant_roster_changed = bool(
            previous is not None
            and current_participant_ids != previous_participant_ids
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
        raw_window["participant_roster_ids"] = sorted(
            current_participant_ids
        )
        if participant_roster_changed:
            raw_window["participant_rebased"] = True
            raw_window["participant_rebase_reason"] = (
                "participant_snapshot_roster_changed"
            )
        else:
            raw_window.pop("participant_rebased", None)
            raw_window.pop("participant_rebase_reason", None)
        observation.raw_window = raw_window
        observation.save(
            update_fields=[
                "attribution_started_at",
                "selected_total_cost",
                "interval_used_percent",
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
            rate_history.append((sample_rate, interval_percent))

        participant_deltas: dict[int, Decimal | None] = {}
        for snapshot in snapshots:
            snapshot.selected_cost = max(
                ZERO,
                snapshot.raw_selected_cost
                - segment.participant_baselines.get(snapshot.participant_id, ZERO)
                + correction_prefix.user_between(
                    snapshot.participant.sub2api_user_id,
                    segment.started_at,
                    observation.observed_at,
                    observation_id=observation.id,
                ),
            )
            old = previous_snapshots.get(snapshot.participant_id)
            participant_deltas[snapshot.participant_id] = (
                snapshot.selected_cost - old.selected_cost
                if old is not None
                else None
            )

        if participant_roster_changed:
            # 参与者中途加入或退出时，旧观测没有完整的逐用户快照，不能把
            # 新参与者的整周期累计成本误当成“本次增量”。使用当前整周期
            # 累计成本重新分摊当前已用百分比，之后再恢复逐观测增量归属。
            participant_weights = {
                snapshot.participant_id: max(ZERO, snapshot.selected_cost)
                for snapshot in snapshots
            }
            attribution_total = selected_total
        else:
            participant_weights = {
                snapshot.participant_id: max(
                    ZERO,
                    (
                        snapshot.selected_cost
                        if previous is None
                        else (
                            participant_deltas[snapshot.participant_id]
                            or ZERO
                        )
                    ),
                )
                for snapshot in snapshots
            }
            attribution_total = (
                selected_total
                if previous is None
                else max(ZERO, delta_cost or ZERO)
            )
        positive_total = sum(participant_weights.values(), ZERO)
        denominator = max(attribution_total, positive_total)

        for snapshot in snapshots:
            old = previous_snapshots.get(snapshot.participant_id)
            participant_delta = participant_deltas[snapshot.participant_id]
            positive_delta = participant_weights[snapshot.participant_id]
            old_charged = (
                old.charged_cycle_percent if old is not None else ZERO
            )
            if participant_roster_changed:
                charged = (
                    interval_percent * positive_delta / denominator
                    if denominator > 0
                    else ZERO
                )
                charged_delta = charged - old_charged
            else:
                charged_delta = ZERO
                if denominator > 0:
                    if previous is None:
                        charged_delta = (
                            interval_percent * positive_delta / denominator
                        )
                    elif valid_sample and delta_percent is not None:
                        charged_delta = (
                            delta_percent * positive_delta / denominator
                        )
                charged = max(ZERO, old_charged + charged_delta)
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

            snapshot.delta_cost = participant_delta
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
    replay_from: datetime | None,
    correction_prefix: FastCorrectionPrefix,
) -> None:
    if not segments:
        return
    queryset = ParticipantUsageSample.objects.filter(account_id=account_id)
    if replay_from is not None:
        queryset = queryset.filter(observed_at__gte=replay_from)
    samples = list(queryset.order_by("observed_at", "id"))
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
            - segment.participant_baselines.get(sample.participant_id, ZERO)
            + correction_prefix.user_between(
                sample.participant.sub2api_user_id,
                segment.started_at,
                sample.observed_at,
            ),
        )
    if samples:
        ParticipantUsageSample.objects.bulk_update(
            samples,
            ["attribution_started_at", "selected_cost"],
        )


def _update_participant_latest(account_id: int) -> None:
    latest = (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
        )
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return
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


def _previous_included(observation: Observation) -> Observation | None:
    return (
        Observation.objects.filter(
            account_id=observation.account_id,
            excluded_at__isnull=True,
        )
        .filter(
            Q(observed_at__lt=observation.observed_at)
            | Q(
                observed_at=observation.observed_at,
                id__lt=observation.id,
            )
        )
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )

def _legacy_unrebased_roster_change(
    previous: Observation | None,
) -> Observation | None:
    """定位升级前遗漏的参与者集合变化，仅需对旧数据扫描一次。"""

    if (
        previous is None
        or previous.attribution_started_at is None
        or "participant_roster_ids" in previous.raw_window
    ):
        return None
    observations = list(
        Observation.objects.filter(
            account_id=previous.account_id,
            attribution_started_at=previous.attribution_started_at,
            excluded_at__isnull=True,
        )
        .filter(
            Q(observed_at__lt=previous.observed_at)
            | Q(
                observed_at=previous.observed_at,
                id__lte=previous.id,
            )
        )
        .prefetch_related("participant_snapshots")
        .order_by("observed_at", "id")
    )
    previous_ids: set[int] | None = None
    for observation in observations:
        current_ids = {
            snapshot.participant_id
            for snapshot in observation.participant_snapshots.all()
        }
        if (
            previous_ids is not None
            and current_ids != previous_ids
            and not observation.raw_window.get("participant_rebased")
        ):
            return observation
        previous_ids = current_ids
    return None

def _replay_anchor(
    observation: Observation,
    *,
    merge_previous: bool = False,
) -> datetime:
    """返回能覆盖本次变化、但不会多算更早稳定区间的最早时间。"""

    previous = _previous_included(observation)
    if merge_previous:
        if previous is None:
            return _official_start(observation)
        return (
            previous.attribution_started_at
            or (
                previous.observed_at
                if previous.is_manual_start
                else _official_start(previous)
            )
        )
    if observation.is_manual_start:
        return observation.observed_at
    if observation.attribution_started_at is not None:
        return observation.attribution_started_at
    if previous is not None and _same_official_reset(
        previous.upstream_resets_at,
        observation.upstream_resets_at,
    ):
        return previous.attribution_started_at or _official_start(observation)
    return _official_start(observation)


@transaction.atomic
def rebuild_account(
    account_id: int,
    config: AppSettings | None = None,
    *,
    replay_from: datetime | None = None,
) -> ReplayResult:
    """从最早受影响的边界向后重放；``None`` 仅供升级或修复时全量重放。"""

    config = config or AppSettings.load()
    queryset = Observation.objects.select_for_update().filter(
        account_id=account_id
    )
    if replay_from is not None:
        queryset = queryset.filter(observed_at__gte=replay_from)
    observations = list(
        queryset.prefetch_related(
            "participant_snapshots__participant"
        ).order_by("observed_at", "id")
    )
    if not observations:
        latest = (
            Observation.objects.filter(
                account_id=account_id,
                excluded_at__isnull=True,
            )
            .order_by("-observed_at", "-id")
            .first()
        )
        return ReplayResult(0, 0, 0, latest.pk if latest else None)

    fallback_rate: Decimal | None = None
    if replay_from is not None:
        preceding = (
            Observation.objects.filter(
                account_id=account_id,
                excluded_at__isnull=True,
                observed_at__lt=observations[0].observed_at,
            )
            .order_by("-observed_at", "-id")
            .first()
        )
        if preceding is not None:
            fallback_rate = preceding.effective_usd_per_percent

    reset_automatic: list[Observation] = []
    for observation in observations:
        if observation.exclusion_source == "automatic":
            observation.excluded_at = None
            observation.exclusion_source = ""
            observation.exclusion_reason = ""
            reset_automatic.append(observation)
    if reset_automatic:
        Observation.objects.bulk_update(
            reset_automatic,
            ["excluded_at", "exclusion_source", "exclusion_reason"],
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
                "interval_used_percent",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "valid_sample",
                "sample_note",
                "raw_window",
            ],
        )

    correction_prefix = FastCorrectionPrefix(account_id, config.cost_basis)
    rebuilt = 0
    for segment in segments:
        count, fallback_rate = _replay_segment(
            segment,
            config,
            fallback_rate,
            correction_prefix=correction_prefix,
        )
        rebuilt += count

    _replay_usage_samples(
        account_id,
        segments,
        replay_from,
        correction_prefix,
    )
    _update_participant_latest(account_id)
    latest = (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
        )
        .order_by("-observed_at", "-id")
        .first()
    )
    return ReplayResult(
        rebuilt_observations=rebuilt,
        automatic_exclusions=len(automatic),
        inferred_intervals=len(segments),
        latest_observation_id=latest.pk if latest else None,
    )


def _append_segment(
    observation: Observation,
    previous: Observation | None,
) -> ReplaySegment:
    """构造只含新增点的区间，同时复用既有区间的确定性基线。"""

    if observation.is_manual_start:
        segment = _manual_start_segment(observation)
    elif (
        previous is not None
        and previous.attribution_started_at is not None
        and _same_official_reset(
            previous.upstream_resets_at,
            observation.upstream_resets_at,
        )
    ):
        anchor = (
            Observation.objects.filter(
                account_id=observation.account_id,
                observed_at=previous.attribution_started_at,
                excluded_at__isnull=True,
            )
            .filter(
                Q(is_manual_start=True)
                | Q(upstream_used_percent=ZERO)
            )
            .prefetch_related("participant_snapshots__participant")
            .order_by("-is_manual_start", "id")
            .first()
        )
        if anchor is not None and anchor.is_manual_start:
            segment = _manual_start_segment(anchor)
        elif (
            anchor is not None
            and _same_official_reset(
                anchor.upstream_resets_at,
                observation.upstream_resets_at,
            )
        ):
            segment = _official_segment(anchor)
        else:
            segment = _official_segment(observation)
            segment.started_at = previous.attribution_started_at
    else:
        segment = _official_segment(observation)
    segment.observations = [observation]
    segment.first_observed_at = observation.observed_at
    return segment


def _rate_history_before(
    observation: Observation,
    previous: Observation | None,
    limit: int,
) -> list[tuple[Decimal, Decimal]]:
    if (
        previous is None
        or previous.attribution_started_at is None
        or limit <= 0
    ):
        return []
    rows = list(
        Observation.objects.filter(
            account_id=observation.account_id,
            attribution_started_at=previous.attribution_started_at,
            excluded_at__isnull=True,
            valid_sample=True,
            sample_usd_per_percent__isnull=False,
        )
        .filter(
            Q(observed_at__lt=observation.observed_at)
            | Q(
                observed_at=observation.observed_at,
                id__lt=observation.id,
            )
        )
        .order_by("-observed_at", "-id")[:limit]
    )
    rows.reverse()
    return [
        (row.sample_usd_per_percent, row.interval_used_percent)
        for row in rows
    ]


@transaction.atomic
def rebuild_observation_suffix(
    observation: Observation,
    config: AppSettings | None = None,
) -> ReplayResult:
    """新增末尾观测只计算自身；非末尾插入才退回到受影响区间重放。"""

    config = config or AppSettings.load()
    observation = (
        Observation.objects.select_for_update()
        .prefetch_related("participant_snapshots__participant")
        .get(pk=observation.pk)
    )
    later_exists = (
        Observation.objects.filter(account_id=observation.account_id)
        .filter(
            Q(observed_at__gt=observation.observed_at)
            | Q(
                observed_at=observation.observed_at,
                id__gt=observation.id,
            )
        )
        .exists()
    )
    if later_exists:
        return rebuild_account(
            observation.account_id,
            config,
            replay_from=_replay_anchor(observation),
        )

    previous = _previous_included(observation)
    legacy_roster_change = _legacy_unrebased_roster_change(previous)
    if legacy_roster_change is not None:
        return rebuild_account(
            observation.account_id,
            config,
            replay_from=_replay_anchor(legacy_roster_change),
        )
    same_official_window = bool(
        previous is not None
        and _same_official_reset(
            previous.upstream_resets_at,
            observation.upstream_resets_at,
        )
    )
    rollback = bool(
        same_official_window
        and not observation.is_manual_start
        and observation.upstream_used_percent + RESET_ROLLBACK_TOLERANCE
        < previous.upstream_used_percent
    )
    if rollback:
        _mark_automatic_exclusion(
            [observation],
            "百分比回退但官方重置时间未变化，等待官方窗口更新或管理员设置起点",
        )
        Observation.objects.bulk_update(
            [observation],
            [
                "excluded_at",
                "exclusion_source",
                "exclusion_reason",
                "attribution_started_at",
                "selected_total_cost",
                "interval_used_percent",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "valid_sample",
                "sample_note",
                "raw_window",
            ],
        )
        _update_participant_latest(observation.account_id)
        return ReplayResult(
            rebuilt_observations=0,
            automatic_exclusions=1,
            inferred_intervals=0,
            latest_observation_id=previous.pk,
        )

    continues_segment = bool(
        same_official_window
        and previous is not None
        and previous.attribution_started_at is not None
        and not observation.is_manual_start
    )
    segment = _append_segment(observation, previous)
    seed_previous = previous if continues_segment else None
    fallback_rate = (
        previous.effective_usd_per_percent if previous is not None else None
    )
    rate_history = _rate_history_before(
        observation,
        seed_previous,
        max(0, config.rate_history_samples - 1),
    )
    correction_prefix = FastCorrectionPrefix(
        observation.account_id,
        config.cost_basis,
    )
    rebuilt, _latest_rate = _replay_segment(
        segment,
        config,
        fallback_rate,
        previous_observation=seed_previous,
        rate_history_seed=rate_history,
        correction_prefix=correction_prefix,
    )
    _replay_usage_samples(
        observation.account_id,
        [segment],
        observation.observed_at,
        correction_prefix,
    )
    _update_participant_latest(observation.account_id)
    return ReplayResult(
        rebuilt_observations=rebuilt,
        automatic_exclusions=0,
        inferred_intervals=1,
        latest_observation_id=observation.pk,
    )


def rebuild_current_interval(
    account_id: int,
    config: AppSettings | None = None,
) -> tuple[ReplayResult, datetime | None]:
    """只重建当前归属区间的派生结果，并保留全部原始采样事实。"""

    latest = (
        Observation.objects.filter(account_id=account_id)
        .order_by("-observed_at", "-id")
        .first()
    )
    if latest is None:
        return rebuild_account(account_id, config), None

    replay_from = _replay_anchor(
        latest,
        merge_previous=latest.is_manual_start,
    )
    return (
        rebuild_account(
            account_id,
            config,
            replay_from=replay_from,
        ),
        replay_from,
    )


@transaction.atomic
def exclude_observation(
    observation: Observation,
    reason: str = "管理员手动排除",
) -> dict[str, int | bool | None]:
    """排除原始点，并从其原区间或被移除的手动边界之前重放。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    replay_from = _replay_anchor(
        observation,
        merge_previous=observation.is_manual_start,
    )
    already_excluded = observation.exclusion_source == "manual"
    observation.excluded_at = timezone.now()
    observation.exclusion_source = "manual"
    observation.exclusion_reason = reason.strip()[:255] or "管理员手动排除"
    observation.valid_sample = False
    observation.sample_usd_per_percent = None
    observation.sample_note = f"已排除：{observation.exclusion_reason}"
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "valid_sample",
            "sample_usd_per_percent",
            "sample_note",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    return {"already_excluded": already_excluded, **replay.as_dict()}


@transaction.atomic
def restore_observation(
    observation: Observation,
) -> dict[str, int | bool | None]:
    """恢复排除记录；若恢复后形成同窗口回退，则由管理员确认它是新起点。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_included = observation.excluded_at is None
    previous = _previous_included(observation)
    confirms_rollback = bool(
        not already_included
        and previous is not None
        and _same_official_reset(
            previous.upstream_resets_at,
            observation.upstream_resets_at,
        )
        and observation.upstream_used_percent + RESET_ROLLBACK_TOLERANCE
        < previous.upstream_used_percent
    )
    if confirms_rollback and not observation.is_manual_start:
        observation.is_manual_start = True
        observation.manual_start_reason = "管理员恢复同一官方窗口内的回退记录"
        observation.manual_start_set_at = timezone.now()
        replay_from = observation.observed_at
    else:
        replay_from = _replay_anchor(observation)
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.save(
        update_fields=[
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    observation.refresh_from_db()
    return {
        "already_included": already_included,
        "included": observation.excluded_at is None,
        "manual_start": observation.is_manual_start,
        **replay.as_dict(),
    }


@transaction.atomic
def set_manual_start(
    observation: Observation,
    reason: str = "",
) -> dict[str, int | bool | None]:
    """把一个真实观测点设为最高优先级零基线，并重放其后缀。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    already_set = observation.is_manual_start
    observation.is_manual_start = True
    observation.manual_start_reason = reason.strip()[:255]
    observation.manual_start_set_at = timezone.now()
    observation.excluded_at = None
    observation.exclusion_source = ""
    observation.exclusion_reason = ""
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
            "excluded_at",
            "exclusion_source",
            "exclusion_reason",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=observation.observed_at,
    )
    return {"already_set": already_set, **replay.as_dict()}


@transaction.atomic
def clear_manual_start(
    observation: Observation,
) -> dict[str, int | bool | None]:
    """取消人工边界，并从它之前的有效区间重新连接后续数据。"""

    observation = Observation.objects.select_for_update().get(pk=observation.pk)
    replay_from = _replay_anchor(observation, merge_previous=True)
    was_set = observation.is_manual_start
    observation.is_manual_start = False
    observation.manual_start_reason = ""
    observation.manual_start_set_at = None
    observation.save(
        update_fields=[
            "is_manual_start",
            "manual_start_reason",
            "manual_start_set_at",
        ]
    )
    replay = rebuild_account(
        observation.account_id,
        replay_from=replay_from,
    )
    return {"was_set": was_set, **replay.as_dict()}
