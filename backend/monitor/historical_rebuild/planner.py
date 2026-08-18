"""Persistent immutable local audit-plan creation."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from ..accounting.contracts import ALGORITHM_VERSION
from ..history_state import current_fact_revision
from ..models import AppSettings, HistoricalRebuildRun, MonitoredAccount
from .audit import audit_account
from .contracts import (
    BUILD_ID,
    HistoricalRebuildError,
    config_digest,
    participant_policy_digest,
    plan_digest,
    source_fact_digest,
)

PLAN_TTL = timedelta(minutes=30)


def create_rebuild_plan(
    config: AppSettings,
    account: MonitoredAccount,
) -> HistoricalRebuildRun:
    """Freeze one zero-network full-history audit and replay plan."""

    account_id = account.external_account_id
    base_revision = current_fact_revision(account_id)
    initial_config_digest = config_digest(config, account)
    initial_policy_digest = participant_policy_digest(account)
    initial_source_digest = source_fact_digest(account_id)
    audit = audit_account(account_id)
    run = HistoricalRebuildRun.objects.create(
        account_id=account_id,
        state="generating",
        base_revision=base_revision,
        source_digest=initial_source_digest,
        algorithm_version=ALGORITHM_VERSION,
        build_id=BUILD_ID,
        config_digest=initial_config_digest,
        participant_policy_digest=initial_policy_digest,
        expires_at=timezone.now() + PLAN_TTL,
    )

    blockers = [issue.as_dict() for issue in audit.issues]
    config.refresh_from_db()
    account.refresh_from_db()
    stale_reasons = []
    if current_fact_revision(account_id) != base_revision:
        stale_reasons.append("fact_revision_changed")
    if config_digest(config, account) != initial_config_digest:
        stale_reasons.append("config_changed")
    if participant_policy_digest(account) != initial_policy_digest:
        stale_reasons.append("participant_policy_changed")
    if source_fact_digest(account_id) != initial_source_digest:
        stale_reasons.append("source_facts_changed")
    if stale_reasons:
        blockers.append(
            {
                "code": "plan_became_stale",
                "severity": "hard",
                "point_id": None,
                "message": "计划生成期间源事实或策略发生变化",
                "reasons": stale_reasons,
            }
        )
        state = "stale"
    elif any(item.get("severity") == "hard" for item in blockers):
        state = "blocked"
    else:
        state = "ready"

    run.blockers = blockers
    run.state = state
    run.save(update_fields=["blockers", "state"])
    run.plan_digest = plan_digest(run)
    run.save(update_fields=["plan_digest"])
    return run


def rebuild_plan_data(run: HistoricalRebuildRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "account_id": run.account_id,
        "state": run.state,
        "digest": run.plan_digest,
        "created_at": run.created_at.isoformat(),
        "expires_at": run.expires_at.isoformat(),
        "base_revision": run.base_revision,
        "result_revision": run.result_revision,
        "blockers": run.blockers,
        "replay_summary": run.replay_summary,
        "safe_to_apply": (
            run.state == "ready" and run.expires_at > timezone.now()
        ),
        "algorithm_version": run.algorithm_version,
        "build_id": run.build_id,
    }
