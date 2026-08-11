"""从保留证据与可重取成本重放额度折算、重置边界和参与者归属。

日常重放不会覆盖来源成本，只从最早受影响的区间起点向后计算。显式历史
重建可先用请求日志替换成本事实，再调用本模块。官方 ``reset_at - window``
是确定性边界；管理员指定的观测起点优先级更高。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from .boundaries import (
    infer_segments as _infer_segments,
    mark_deferred_zero_observations,
    official_start as _official_start,
    same_official_reset as _same_official_reset,
)
from .contracts import ReplayResult, ReplaySegment
from .cost_ledger import normalize_cost_history
from .dynamic_attribution import replay_dynamic_segment
from ..fast_correction.prefix import FastCorrectionPrefix
from ..models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
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
    """兼容既有调用边界，内部始终重放完整粒子滤波区间。"""

    del previous_observation, rate_history_seed
    if not segment.observations:
        return 0, fallback_rate
    return replay_dynamic_segment(
        account_id=segment.observations[0].account_id,
        segment=segment,
        config=config,
        correction_prefix=correction_prefix,
        prior_rate=fallback_rate,
    )


def _replay_usage_samples(
    account_id: int,
    segments: list[ReplaySegment],
    replay_from: datetime | None,
    correction_prefix: FastCorrectionPrefix,
    cost_basis: str,
) -> None:
    if not segments:
        return
    queryset = ParticipantUsageSample.objects.select_related(
        "participant"
    ).filter(account_id=account_id)
    if replay_from is not None:
        queryset = queryset.filter(observed_at__gte=replay_from)
    samples = list(queryset.order_by("observed_at", "id"))
    normalized_by_key = {
        (row.sub2api_user_id, row.observed_at): row.normalized_cost(cost_basis)
        for row in Sub2APIUserUsageSample.objects.filter(
            account_id=account_id,
            observed_at__in=[sample.observed_at for sample in samples],
        )
    }
    for sample in samples:
        segment = None
        for candidate in segments:
            if candidate.first_observed_at <= sample.observed_at:
                segment = candidate
            else:
                break
        if segment is None:
            sample.attribution_started_at = None
            sample.selected_cost = ZERO
            continue
        sample.attribution_started_at = segment.started_at
        raw_cost = normalized_by_key.get(
            (sample.participant.sub2api_user_id, sample.observed_at),
            sample.raw_selected_cost,
        )
        baseline = segment.participant_baselines.get(
            sample.participant_id,
            ZERO,
        )
        if segment.reason in {"manual_override", "official_zero_observation"}:
            baseline = normalized_by_key.get(
                (
                    sample.participant.sub2api_user_id,
                    segment.first_observed_at,
                ),
                baseline,
            )
        sample.selected_cost = max(
            ZERO,
            raw_cost
            - baseline
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
    if (
        previous is not None
        and previous.upstream_used_percent == ZERO
        and not observation.is_manual_start
    ):
        return previous.attribution_started_at or previous.observed_at
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
    all_observations = list(
        Observation.objects.select_for_update()
        .filter(account_id=account_id)
        .prefetch_related("participant_snapshots__participant")
        .order_by("observed_at", "id")
    )
    normalize_cost_history(account_id, all_observations)
    observations = [
        observation
        for observation in all_observations
        if replay_from is None or observation.observed_at >= replay_from
    ]
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

    latest_rate: Decimal | None = None
    if replay_from is not None:
        preceding = (
            Observation.objects.filter(
                account_id=account_id,
                excluded_at__isnull=True,
                observed_at__lt=replay_from,
                effective_usd_per_percent__isnull=False,
            )
            .order_by("-observed_at", "-id")
            .first()
        )
        if preceding is not None:
            latest_rate = preceding.effective_usd_per_percent

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
    segments, automatic, deferred = _infer_segments(
        candidates,
        config.cost_basis,
    )
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
                "estimated_used_percent",
                "capacity_lower_usd",
                "capacity_upper_usd",
                "model_diagnostics",
                "valid_sample",
                "sample_note",
                "raw_window",
            ],
        )
    if deferred:
        mark_deferred_zero_observations(deferred)
        Observation.objects.bulk_update(
            deferred,
            [
                "attribution_started_at",
                "selected_total_cost",
                "interval_used_percent",
                "delta_percent",
                "delta_cost",
                "sample_usd_per_percent",
                "estimated_used_percent",
                "capacity_lower_usd",
                "capacity_upper_usd",
                "model_diagnostics",
                "valid_sample",
                "sample_note",
                "raw_window",
            ],
        )

    correction_prefix = FastCorrectionPrefix(account_id, config.cost_basis)
    rebuilt = 0
    for segment in segments:
        count, latest_rate = _replay_segment(
            segment,
            config,
            latest_rate,
            correction_prefix=correction_prefix,
        )
        rebuilt += count

    _replay_usage_samples(
        account_id,
        segments,
        replay_from,
        correction_prefix,
        config.cost_basis,
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






@transaction.atomic
def rebuild_observation_suffix(
    observation: Observation,
    config: AppSettings | None = None,
) -> ReplayResult:
    """粒子状态依赖完整区间；新增、插入和恢复都从当前区间起点重放。"""

    config = config or AppSettings.load()
    observation = Observation.objects.select_for_update().get(
        pk=observation.pk
    )
    return rebuild_account(
        observation.account_id,
        config,
        replay_from=_replay_anchor(observation),
    )


