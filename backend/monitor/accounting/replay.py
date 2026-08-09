"""从不可变原始采样重放额度折算、重置边界和参与者归属。

系统永久保留全部原始采样，但日常只从最早受影响的区间起点向后重放。官方
``reset_at - window`` 是确定性边界；管理员指定的观测起点优先级更高。
排除、恢复或新增记录都不会改写不可能受影响的更早区间。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from .attribution import apply_participant_attribution
from .boundaries import (
    RATE_METHOD,
    RESET_ROLLBACK_TOLERANCE,
    infer_segments as _infer_segments,
    mark_automatic_exclusion as _mark_automatic_exclusion,
    manual_start_segment as _manual_start_segment,
    official_segment as _official_segment,
    official_start as _official_start,
    same_official_reset as _same_official_reset,
)
from .contracts import ReplayResult, ReplaySegment
from .rates import sample_note as _sample_note, select_rate
from ..fast_correction import FastCorrectionPrefix

from ..models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantUsageSample,
)

ZERO = Decimal("0")




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
        rate_decision = select_rate(
            selected_total=selected_total,
            interval_percent=interval_percent,
            delta_percent=delta_percent,
            delta_cost=delta_cost,
            has_previous=previous is not None,
            history=rate_history,
            history_samples=config.rate_history_samples,
            percentile=config.conservative_percentile,
            fallback_rate=fallback_rate,
            initial_rate=config.initial_usd_per_percent,
        )
        valid_sample = rate_decision.valid_sample
        sample_rate = rate_decision.sample_rate
        effective_rate = rate_decision.effective_rate
        rate_source = rate_decision.source
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
            has_previous=previous is not None,
            delta_percent=delta_percent,
            delta_cost=delta_cost,
            valid_sample=valid_sample,
            rate_source=rate_source,
        )
        attribution = apply_participant_attribution(
            observation=observation,
            previous_snapshots=previous_snapshots,
            has_previous=previous is not None,
            segment=segment,
            correction_prefix=correction_prefix,
            selected_total=selected_total,
            interval_percent=interval_percent,
            delta_percent=delta_percent,
            delta_cost=delta_cost,
            valid_sample=valid_sample,
            effective_rate=effective_rate,
            config=config,
        )
        snapshots = attribution.snapshots
        current_participant_ids = attribution.participant_ids
        participant_roster_changed = attribution.roster_changed
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


