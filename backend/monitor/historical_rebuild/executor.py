"""Zero-network typed patch application with fencing and atomic replay."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..accounting.contracts import ALGORITHM_VERSION
from ..history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from ..models import (
    AppSettings,
    HistoricalRebuildCoverage,
    HistoricalRebuildRun,
    HistoryMaintenanceState,
    Observation,
    ObservationFastCorrection,
    ParticipantAPIUsageSnapshot,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from ..replay import rebuild_account
from .audit import audit_account
from .contracts import (
    BUILD_ID,
    SAFE_COVERAGE,
    FastFactPayload,
    HistoricalRebuildConflict,
    HistoricalRebuildError,
    ObservationCostPayload,
    UserCostPayload,
    config_digest,
    participant_policy_digest,
    plan_digest,
    source_fact_digest,
    validate_patch_payload,
)
from .planner import rebuild_plan_data


def _assert_patch_coverage(patch) -> None:
    ids = patch.required_coverage_ids
    if not isinstance(ids, list) or any(not isinstance(item, int) for item in ids):
        raise HistoricalRebuildError("补丁 required_coverage_ids 无效")
    rows = list(
        HistoricalRebuildCoverage.objects.filter(
            run=patch.run,
            id__in=ids,
        )
    )
    if len(rows) != len(set(ids)) or any(
        row.status not in SAFE_COVERAGE for row in rows
    ):
        raise HistoricalRebuildConflict("补丁缺少逐维 verified coverage")


def _apply_observation_cost(
    patch,
    payload: ObservationCostPayload,
) -> None:
    if payload.point_id != patch.sample_point_id:
        raise HistoricalRebuildError("observation_cost point natural key 不匹配")
    point = UsageSamplePoint.objects.select_for_update().get(pk=payload.point_id)
    point.fact_revision = payload.fact_revision
    point.window_started_at = payload.window_started_at
    point.window_ended_at = payload.window_ended_at
    point.window_resets_at = payload.window_resets_at
    point.account_standard_cost = payload.account_standard_cost
    point.account_actual_cost = payload.account_actual_cost
    point.interval_started_at = payload.interval_started_at
    point.interval_standard_cost = payload.interval_standard_cost
    point.interval_actual_cost = payload.interval_actual_cost
    point.residual_standard_cost = payload.residual_standard_cost
    point.residual_actual_cost = payload.residual_actual_cost
    point.expected_user_count = payload.expected_user_count
    point.expected_user_digest = payload.expected_user_digest
    point.write_status = payload.write_status
    point.reconciliation_status = payload.reconciliation_status
    point.save(
        update_fields=[
            "fact_revision",
            "window_started_at",
            "window_ended_at",
            "window_resets_at",
            "account_standard_cost",
            "account_actual_cost",
            "interval_started_at",
            "interval_standard_cost",
            "interval_actual_cost",
            "residual_standard_cost",
            "residual_actual_cost",
            "expected_user_count",
            "expected_user_digest",
            "write_status",
            "reconciliation_status",
        ]
    )
    if payload.observation_id is None:
        if patch.observation_id is not None:
            raise HistoricalRebuildError("observation_cost observation natural key 不匹配")
        return
    if payload.observation_id != patch.observation_id:
        raise HistoricalRebuildError("observation_cost observation natural key 不匹配")
    observation = Observation.objects.select_for_update().get(
        pk=payload.observation_id
    )
    observation.raw_selected_total_cost = payload.raw_selected_total_cost
    observation.total_standard_cost = payload.total_standard_cost
    observation.total_actual_cost = payload.total_actual_cost
    observation.cost_window_started_at = payload.cost_window_started_at
    observation.cost_window_ended_at = payload.cost_window_ended_at
    observation.interval_cost_started_at = payload.observation_interval_started_at
    observation.interval_standard_cost = payload.observation_interval_standard_cost
    observation.interval_actual_cost = payload.observation_interval_actual_cost
    observation.interval_cost_source = payload.interval_cost_source
    observation.save(
        update_fields=[
            "raw_selected_total_cost",
            "total_standard_cost",
            "total_actual_cost",
            "cost_window_started_at",
            "cost_window_ended_at",
            "interval_cost_started_at",
            "interval_standard_cost",
            "interval_actual_cost",
            "interval_cost_source",
        ]
    )


def _apply_user_cost(patch, payload: UserCostPayload) -> None:
    if (
        payload.point_id != patch.sample_point_id
        or payload.sub2api_user_id != patch.sub2api_user_id
    ):
        raise HistoricalRebuildError("user_cost natural key 不匹配")
    sample = None
    if payload.sample_id is not None:
        if patch.user_sample_id != payload.sample_id:
            raise HistoricalRebuildError("user_cost sample id 不匹配")
        sample = Sub2APIUserUsageSample.objects.select_for_update().get(
            pk=payload.sample_id
        )
    else:
        if patch.user_sample_id is not None:
            raise HistoricalRebuildError("user_cost insert natural key 不匹配")
        sample = Sub2APIUserUsageSample.objects.select_for_update().filter(
            account_id=payload.account_id,
            observed_at=payload.observed_at,
            sub2api_user_id=payload.sub2api_user_id,
        ).first()
        if sample is not None:
            raise HistoricalRebuildConflict("待插入的用户成本 natural key 已存在")
        sample = Sub2APIUserUsageSample()
    sample.sample_point_id = payload.point_id
    sample.account_id = payload.account_id
    sample.sub2api_user_id = payload.sub2api_user_id
    sample.username = payload.username
    sample.email = payload.email
    sample.observed_at = payload.observed_at
    sample.window_started_at = payload.window_started_at
    sample.window_ended_at = payload.window_ended_at
    sample.window_resets_at = payload.window_resets_at
    sample.total_standard_cost = payload.total_standard_cost
    sample.total_actual_cost = payload.total_actual_cost
    sample.interval_started_at = payload.interval_started_at
    sample.interval_standard_cost = payload.interval_standard_cost
    sample.interval_actual_cost = payload.interval_actual_cost
    sample.interval_source = payload.interval_source
    sample.normalized_standard_cost = None
    sample.normalized_actual_cost = None
    sample.save()


def _apply_fast_fact(patch, payload: FastFactPayload) -> None:
    if payload.observation_id != patch.observation_id:
        raise HistoricalRebuildError("fast_fact natural key 不匹配")
    observation = Observation.objects.select_for_update().get(
        pk=payload.observation_id
    )
    observation.fast_correction_started_at = payload.started_at
    observation.fast_correction_request_count = payload.request_count
    observation.fast_correction_standard_cost = payload.standard_correction_cost
    observation.fast_correction_actual_cost = payload.actual_correction_cost
    observation.save(
        update_fields=[
            "fast_correction_started_at",
            "fast_correction_request_count",
            "fast_correction_standard_cost",
            "fast_correction_actual_cost",
        ]
    )
    observation.fast_corrections.all().delete()
    ObservationFastCorrection.objects.bulk_create(
        [
            ObservationFastCorrection(
                observation=observation,
                sub2api_user_id=detail.sub2api_user_id,
                fast_request_count=detail.fast_request_count,
                request_count=detail.request_count,
                fast_standard_cost=detail.fast_standard_cost,
                fast_actual_cost=detail.fast_actual_cost,
                standard_correction_cost=detail.standard_correction_cost,
                actual_correction_cost=detail.actual_correction_cost,
            )
            for detail in payload.details
        ]
    )


def apply_typed_payload(patch, raw_payload) -> None:
    if patch.schema_version != 1:
        raise HistoricalRebuildError("不支持的 patch schema_version")
    payload = validate_patch_payload(patch.kind, raw_payload)
    if isinstance(payload, ObservationCostPayload):
        _apply_observation_cost(patch, payload)
    elif isinstance(payload, UserCostPayload):
        _apply_user_cost(patch, payload)
    elif isinstance(payload, FastFactPayload):
        _apply_fast_fact(patch, payload)
    else:
        raise HistoricalRebuildError("未知 typed patch payload")


def _mark_stale(run: HistoricalRebuildRun, reason: str) -> None:
    blockers = list(run.blockers)
    blockers.append(
        {
            "code": "apply_stale",
            "severity": "hard",
            "point_id": None,
            "message": reason,
        }
    )
    HistoricalRebuildRun.objects.filter(
        pk=run.pk,
        state="ready",
    ).update(state="stale", blockers=blockers)


def _validate_ready_plan(
    run: HistoricalRebuildRun,
    digest: str,
    config: AppSettings,
    state: HistoryMaintenanceState,
) -> None:
    if run.state == "applied":
        if (
            digest == run.plan_digest
            and plan_digest(run) == run.plan_digest
        ):
            return
        raise HistoricalRebuildConflict(
            "计划已经应用且 digest 或 patch journal 不匹配"
        )
    if run.state != "ready":
        raise HistoricalRebuildConflict("计划当前不可应用", rebuild_plan_data(run))
    if not digest or digest != run.plan_digest:
        raise HistoricalRebuildConflict("计划 digest 不匹配")
    if run.expires_at <= timezone.now():
        raise HistoricalRebuildConflict("计划已过期，请重新创建")
    if plan_digest(run) != run.plan_digest:
        raise HistoricalRebuildConflict("计划内容已发生变化")
    if state.fact_revision != run.base_revision:
        raise HistoricalRebuildConflict("源事实 revision 已变化")
    if source_fact_digest(run.account_id) != run.source_digest:
        raise HistoricalRebuildConflict("源事实已被原地修改")
    if config_digest(config) != run.config_digest:
        raise HistoricalRebuildConflict("影响重放的系统设置已变化")
    if participant_policy_digest() != run.participant_policy_digest:
        raise HistoricalRebuildConflict("参与者策略已变化")
    if run.algorithm_version != ALGORITHM_VERSION or run.build_id != BUILD_ID:
        raise HistoricalRebuildConflict("算法或部署版本与计划不一致")


def apply_rebuild_plan(run_id, digest: str) -> HistoricalRebuildRun:
    """Apply an immutable plan without constructing any Sub2API client."""

    run = HistoricalRebuildRun.objects.get(pk=run_id)
    if (
        run.state == "applied"
        and digest == run.plan_digest
        and plan_digest(run) == run.plan_digest
    ):
        return run
    try:
        guard = LeaseGuard.acquire(
            run.account_id,
            ttl=timedelta(minutes=30),
        )
    except LeaseBusyError as exc:
        raise HistoricalRebuildConflict(str(exc)) from exc
    stale_reason: str | None = None
    try:
        with transaction.atomic():
            run = HistoricalRebuildRun.objects.select_for_update().get(pk=run_id)
            state = HistoryMaintenanceState.objects.select_for_update().get(
                account_id=run.account_id
            )
            guard.assert_owned(state)
            config = AppSettings.objects.select_for_update().get(pk=1)
            try:
                _validate_ready_plan(run, digest, config, state)
            except HistoricalRebuildConflict as exc:
                if run.state == "ready":
                    stale_reason = str(exc)
                raise
            if run.state == "applied":
                return run
            run.state = "applying"
            run.save(update_fields=["state"])
            for patch in run.patches.select_for_update().order_by("sequence"):
                _assert_patch_coverage(patch)
                apply_typed_payload(patch, patch.after_payload)

            audited = audit_account(run.account_id)
            if audited.hard_blockers:
                raise HistoricalRebuildConflict(
                    "staged-after 源事实未通过全点审计",
                    {"blockers": [item.as_dict() for item in audited.hard_blockers]},
                )
            ParticipantAPIUsageSnapshot.objects.filter(
                account_id=run.account_id
            ).delete()
            replay = rebuild_account(run.account_id, config, guard=guard)
            audited_after_replay = audit_account(run.account_id)
            if audited_after_replay.hard_blockers:
                raise HistoricalRebuildConflict(
                    "重放后源事实不变量失败",
                    {
                        "blockers": [
                            item.as_dict()
                            for item in audited_after_replay.hard_blockers
                        ]
                    },
                )
            guard.assert_owned(state)
            state.fact_revision += 1
            state.save(update_fields=["fact_revision", "updated_at"])
            run.state = "applied"
            run.result_revision = state.fact_revision
            run.applied_at = timezone.now()
            summary = dict(run.patch_summary)
            summary["replay"] = replay.as_dict()
            run.patch_summary = summary
            run.save(
                update_fields=[
                    "state",
                    "result_revision",
                    "applied_at",
                    "patch_summary",
                ]
            )
        return run
    except HistoricalRebuildConflict:
        if stale_reason is not None:
            current = HistoricalRebuildRun.objects.get(pk=run_id)
            _mark_stale(current, stale_reason)
        raise
    except LeaseLostError as exc:
        raise HistoricalRebuildConflict(str(exc)) from exc
    finally:
        guard.release()
