"""Zero-network local audit-plan application with fencing."""
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
)
from ..replay import rebuild_account
from .audit import audit_account
from .contracts import (
    BUILD_ID,
    HistoricalRebuildConflict,
    config_digest,
    participant_policy_digest,
    plan_digest,
    source_fact_digest,
)
from .planner import rebuild_plan_data


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
        if digest == run.plan_digest:
            return
        raise HistoricalRebuildConflict("计划已经应用且 digest 不匹配")
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
    """Apply one immutable local audit plan without upstream access."""

    run = HistoricalRebuildRun.objects.get(pk=run_id)
    if run.state == "applied" and digest == run.plan_digest:
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

            audited = audit_account(run.account_id)
            if audited.hard_blockers:
                raise HistoricalRebuildConflict(
                    "源事实未通过全点审计",
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
            run.replay_summary = replay.as_dict()
            run.save(
                update_fields=[
                    "state",
                    "result_revision",
                    "applied_at",
                    "replay_summary",
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
