"""Business rollback for the latest applied history-maintenance run."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..accounting.contracts import ALGORITHM_VERSION
from ..history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from ..models import (
    AppSettings,
    HistoricalRebuildRun,
    HistoryMaintenanceState,
    ParticipantAPIUsageSnapshot,
    Sub2APIUserUsageSample,
)
from ..replay import rebuild_account
from .audit import audit_account
from .contracts import (
    BUILD_ID,
    HistoricalRebuildConflict,
    config_digest,
    fast_fact_payload,
    observable_digest,
    observation_cost_payload,
    participant_policy_digest,
    source_fact_digest,
    plan_digest,
    user_cost_payload,
)
from .executor import apply_typed_payload


def _current_payload(patch) -> dict | None:
    if patch.kind == "observation_cost":
        patch.sample_point.refresh_from_db()
        return observation_cost_payload(patch.sample_point).as_json()
    if patch.kind == "user_cost":
        current_sample = Sub2APIUserUsageSample.objects.filter(
            account_id=patch.sample_point.account_id,
            observed_at=patch.sample_point.observed_at,
            sub2api_user_id=patch.sub2api_user_id,
        ).first()
        if current_sample is None:
            return None
        current = user_cost_payload(current_sample).as_json()
        if patch.before_payload is None:
            current["sample_id"] = None
        return current
    if patch.kind == "fast_fact":
        patch.observation.refresh_from_db()
        return fast_fact_payload(patch.observation).as_json()
    raise HistoricalRebuildConflict("未知回滚 patch kind")


def _assert_current_matches_after(patch) -> None:
    current = _current_payload(patch)
    if current is None:
        raise HistoricalRebuildConflict("待回滚的用户成本行已不存在")
    if current != patch.after_payload:
        raise HistoricalRebuildConflict(
            f"待回滚 {patch.kind} source fact 在 apply 后又被修改，拒绝覆盖",
            {"natural_key": patch.natural_key},
        )


def _assert_current_matches_before(patch) -> None:
    current = _current_payload(patch)
    if patch.kind == "user_cost" and patch.before_payload is None:
        if current is not None:
            raise HistoricalRebuildConflict("新增用户成本行未被完整移除")
        return
    if current != patch.before_payload:
        raise HistoricalRebuildConflict(
            f"回滚未恢复 {patch.kind} typed before-image",
            {"natural_key": patch.natural_key},
        )


def _restore_patch(patch) -> None:
    _assert_current_matches_after(patch)
    if patch.kind == "user_cost" and patch.before_payload is None:
        deleted, _ = Sub2APIUserUsageSample.objects.filter(
            account_id=patch.sample_point.account_id,
            observed_at=patch.sample_point.observed_at,
            sub2api_user_id=patch.sub2api_user_id,
        ).delete()
        if deleted != 1:
            raise HistoricalRebuildConflict("待回滚的新增用户成本行不唯一")
        return
    if patch.before_payload is None:
        raise HistoricalRebuildConflict("补丁缺少 required before-image")
    apply_typed_payload(patch, patch.before_payload)


def rollback_rebuild_plan(run_id) -> HistoricalRebuildRun:
    run = HistoricalRebuildRun.objects.get(pk=run_id)
    try:
        guard = LeaseGuard.acquire(
            run.account_id,
            ttl=timedelta(minutes=30),
        )
    except LeaseBusyError as exc:
        raise HistoricalRebuildConflict(str(exc)) from exc
    try:
        with transaction.atomic():
            run = HistoricalRebuildRun.objects.select_for_update().get(pk=run_id)
            state = HistoryMaintenanceState.objects.select_for_update().get(
                account_id=run.account_id
            )
            if plan_digest(run) != run.plan_digest:
                raise HistoricalRebuildConflict(
                    "计划内容已发生变化：patch journal digest 不匹配"
                )
            if run.state == "rolled_back":
                return run
            latest = (
                HistoricalRebuildRun.objects.select_for_update()
                .filter(
                    account_id=run.account_id,
                    state="applied",
                )
                .order_by("-applied_at", "-created_at")
                .first()
            )
            if latest is None or latest.id != run.id:
                raise HistoricalRebuildConflict(
                    "只能按 applied 栈逆序回滚最近一次维护"
                )
            guard.assert_owned(state)
            config = AppSettings.objects.select_for_update().get(pk=1)
            if run.algorithm_version != ALGORITHM_VERSION or run.build_id != BUILD_ID:
                raise HistoricalRebuildConflict("当前算法或部署版本不满足业务回滚承诺")
            if config_digest(config) != run.config_digest:
                raise HistoricalRebuildConflict("影响重放的系统设置已变化，不能业务回滚")
            if participant_policy_digest() != run.participant_policy_digest:
                raise HistoricalRebuildConflict("参与者策略已变化，不能业务回滚")
            no_later_source_writes = (
                state.fact_revision == run.result_revision
                and run.patches.exists()
            )
            patches = list(
                run.patches.select_for_update().order_by("-sequence")
            )
            for patch in patches:
                _restore_patch(patch)
            for patch in patches:
                _assert_current_matches_before(patch)

            audited = audit_account(run.account_id)
            if audited.hard_blockers:
                raise HistoricalRebuildConflict(
                    "回滚后的 source fact 未通过全点审计",
                    {"blockers": [item.as_dict() for item in audited.hard_blockers]},
                )
            ParticipantAPIUsageSnapshot.objects.filter(
                account_id=run.account_id
            ).delete()
            rebuild_account(run.account_id, config, guard=guard)
            if no_later_source_writes:
                restored_source = source_fact_digest(run.account_id)
                restored_observable = observable_digest(run.account_id)
                if restored_source != run.before_source_hash:
                    raise HistoricalRebuildConflict("source before hash 未能恢复")
                if restored_observable != run.before_observable_hash:
                    raise HistoricalRebuildConflict("API 可观察 before hash 未能恢复")
            guard.assert_owned(state)
            state.fact_revision += 1
            state.save(update_fields=["fact_revision", "updated_at"])
            run.state = "rolled_back"
            run.rollback_revision = state.fact_revision
            run.rolled_back_at = timezone.now()
            run.save(
                update_fields=[
                    "state",
                    "rollback_revision",
                    "rolled_back_at",
                ]
            )
        return run
    except LeaseLostError as exc:
        raise HistoricalRebuildConflict(str(exc)) from exc
    finally:
        guard.release()
