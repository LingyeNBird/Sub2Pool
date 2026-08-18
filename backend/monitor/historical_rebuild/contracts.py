"""Stable local history-maintenance digests and errors."""
from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from ..accounting.contracts import ALGORITHM_VERSION
from ..fact_utils import (
    canonical_digest,
    canonical_object_digest,
    canonical_rows_digest,
)
from ..models import (
    Participant,
    AppSettings,
    HistoricalRebuildRun,
    MonitoredAccount,
    Observation,
    ObservationFastCorrection,
    ParticipantBalanceSample,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)

BUILD_ID = os.environ.get("PINCH_BUILD_ID", "1.0.0")


class HistoricalRebuildError(ValueError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class HistoricalRebuildConflict(HistoricalRebuildError):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def config_digest(
    config: AppSettings,
    account: MonitoredAccount,
) -> str:
    fields = (
        "timezone",
        "cost_basis",
        "weekly_quota_model",
        "fast_correction_enabled",
        "initial_usd_per_percent",
        "safety_factor",
        "daily_estimate_min_percent_span",
    )
    values = {field: getattr(config, field) for field in fields}
    values["account"] = {
        "external_account_id": account.external_account_id,
        "quota_query_mode": account.quota_query_mode,
        "enabled": account.enabled,
    }
    return canonical_digest(values)


def participant_policy_digest(_account: MonitoredAccount) -> str:
    return canonical_rows_digest(
        Participant.objects.order_by("id")
        .values(
            "id",
            "sub2api_user_id",
            "enabled",
            "share_percent",
            "is_owner",
        )
        .iterator(chunk_size=512)
    )


def source_fact_digest(account_id: int) -> str:
    return canonical_object_digest(
        row_sections={
            "points": UsageSamplePoint.objects.filter(account_id=account_id)
            .order_by("observed_at", "id")
            .values()
            .iterator(chunk_size=512),
            "observations": Observation.objects.filter(account_id=account_id)
            .order_by("observed_at", "id")
            .values()
            .iterator(chunk_size=512),
            "users": Sub2APIUserUsageSample.objects.filter(account_id=account_id)
            .order_by("observed_at", "sub2api_user_id", "id")
            .values()
            .iterator(chunk_size=512),
            "fast": ObservationFastCorrection.objects.filter(
                observation__account_id=account_id
            )
            .order_by("observation_id", "sub2api_user_id", "id")
            .values()
            .iterator(chunk_size=512),
            "participant_usage": ParticipantUsageSample.objects.filter(
                account_id=account_id
            )
            .order_by("observed_at", "participant_id", "id")
            .values()
            .iterator(chunk_size=512),
            "balances": ParticipantBalanceSample.objects.filter(
                point__account_id=account_id
            )
            .order_by("point_id", "participant_id", "provenance", "id")
            .values()
            .iterator(chunk_size=512),
            "snapshots": ParticipantSnapshot.objects.filter(
                observation__account_id=account_id
            )
            .order_by("observation_id", "participant_id", "id")
            .values()
            .iterator(chunk_size=512),
        }
    )


def plan_digest(run: HistoricalRebuildRun) -> str:
    return canonical_digest(
        {
            "id": str(run.id),
            "account_id": run.account_id,
            "base_revision": run.base_revision,
            "source_digest": run.source_digest,
            "algorithm_version": run.algorithm_version,
            "build_id": run.build_id,
            "config_digest": run.config_digest,
            "participant_policy_digest": run.participant_policy_digest,
            "expires_at": _iso(run.expires_at),
            "blockers": run.blockers,
        }
    )
