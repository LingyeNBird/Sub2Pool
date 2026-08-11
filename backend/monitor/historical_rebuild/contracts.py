"""Stable contracts, digests, and strict typed patch payloads."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
from typing import Any

from django.utils.dateparse import parse_datetime

from ..accounting.contracts import ALGORITHM_VERSION
from ..fact_utils import (
    canonical_digest,
    canonical_object_digest,
    canonical_rows_digest,
)
from ..models import (
    AppSettings,
    HistoricalRebuildRun,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantBalanceSample,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)

MODE_AUDIT_REPLAY = "audit_replay"
MODE_VERIFIED_REMOTE_REPAIR = "verified_remote_repair"
REBUILD_MODES = frozenset({MODE_AUDIT_REPLAY, MODE_VERIFIED_REMOTE_REPAIR})
SAFE_COVERAGE = frozenset({"verified", "verified_empty"})
BUILD_ID = os.environ.get("PINCH_BUILD_ID", "1.0.0")


class HistoricalRebuildError(ValueError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class HistoricalRebuildConflict(HistoricalRebuildError):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _money(value: Decimal | None) -> str | None:
    return (
        format(value.quantize(Decimal("0.000001")), "f")
        if value is not None
        else None
    )


def _decimal(value: Any, field: str, *, nullable: bool = True) -> Decimal | None:
    if value is None and nullable:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HistoricalRebuildError(f"补丁字段 {field} 不是有效金额") from exc


def _datetime(value: Any, field: str, *, nullable: bool = True) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise HistoricalRebuildError(f"补丁字段 {field} 不是时间字符串")
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        raise HistoricalRebuildError(f"补丁字段 {field} 不是带时区时间")
    return parsed


def _require_exact(payload: Any, fields: frozenset[str], kind: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or frozenset(payload) != fields:
        raise HistoricalRebuildError(f"{kind} 补丁字段集合与 schema_version 不匹配")
    return payload


@dataclass(frozen=True)
class ObservationCostPayload:
    point_id: int
    fact_revision: int
    observation_id: int | None
    window_started_at: datetime | None
    window_ended_at: datetime | None
    window_resets_at: datetime | None
    account_standard_cost: Decimal | None
    account_actual_cost: Decimal | None
    interval_started_at: datetime | None
    interval_standard_cost: Decimal | None
    interval_actual_cost: Decimal | None
    residual_standard_cost: Decimal | None
    residual_actual_cost: Decimal | None
    expected_user_count: int | None
    expected_user_digest: str
    write_status: str
    reconciliation_status: str
    raw_selected_total_cost: Decimal | None
    total_standard_cost: Decimal | None
    total_actual_cost: Decimal | None
    cost_window_started_at: datetime | None
    cost_window_ended_at: datetime | None
    observation_interval_started_at: datetime | None
    observation_interval_standard_cost: Decimal | None
    observation_interval_actual_cost: Decimal | None
    interval_cost_source: str

    FIELDS = frozenset(
        {
            "point_id",
            "fact_revision",
            "observation_id",
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
            "raw_selected_total_cost",
            "total_standard_cost",
            "total_actual_cost",
            "cost_window_started_at",
            "cost_window_ended_at",
            "observation_interval_started_at",
            "observation_interval_standard_cost",
            "observation_interval_actual_cost",
            "interval_cost_source",
        }
    )

    @classmethod
    def from_json(cls, raw: Any) -> "ObservationCostPayload":
        payload = _require_exact(raw, cls.FIELDS, "observation_cost")
        point_id = int(payload["point_id"])
        observation_id = payload["observation_id"]
        if observation_id is not None:
            observation_id = int(observation_id)
        expected_count = payload["expected_user_count"]
        if expected_count is not None:
            expected_count = int(expected_count)
            if expected_count < 0:
                raise HistoricalRebuildError("expected_user_count 不能为负数")
        write_status = str(payload["write_status"])
        if write_status not in {"complete", "legacy_unknown"}:
            raise HistoricalRebuildError("write_status 无效")
        reconciliation = str(payload["reconciliation_status"])
        if reconciliation not in {"reconciled", "residual", "unknown", "conflict"}:
            raise HistoricalRebuildError("reconciliation_status 无效")
        return cls(
            point_id=point_id,
            fact_revision=int(payload["fact_revision"]),
            observation_id=observation_id,
            window_started_at=_datetime(payload["window_started_at"], "window_started_at"),
            window_ended_at=_datetime(payload["window_ended_at"], "window_ended_at"),
            window_resets_at=_datetime(payload["window_resets_at"], "window_resets_at"),
            account_standard_cost=_decimal(payload["account_standard_cost"], "account_standard_cost"),
            account_actual_cost=_decimal(payload["account_actual_cost"], "account_actual_cost"),
            interval_started_at=_datetime(payload["interval_started_at"], "interval_started_at"),
            interval_standard_cost=_decimal(payload["interval_standard_cost"], "interval_standard_cost"),
            interval_actual_cost=_decimal(payload["interval_actual_cost"], "interval_actual_cost"),
            residual_standard_cost=_decimal(payload["residual_standard_cost"], "residual_standard_cost"),
            residual_actual_cost=_decimal(payload["residual_actual_cost"], "residual_actual_cost"),
            expected_user_count=expected_count,
            expected_user_digest=str(payload["expected_user_digest"]),
            write_status=write_status,
            reconciliation_status=reconciliation,
            raw_selected_total_cost=_decimal(payload["raw_selected_total_cost"], "raw_selected_total_cost"),
            total_standard_cost=_decimal(payload["total_standard_cost"], "total_standard_cost"),
            total_actual_cost=_decimal(payload["total_actual_cost"], "total_actual_cost"),
            cost_window_started_at=_datetime(payload["cost_window_started_at"], "cost_window_started_at"),
            cost_window_ended_at=_datetime(payload["cost_window_ended_at"], "cost_window_ended_at"),
            observation_interval_started_at=_datetime(payload["observation_interval_started_at"], "observation_interval_started_at"),
            observation_interval_standard_cost=_decimal(payload["observation_interval_standard_cost"], "observation_interval_standard_cost"),
            observation_interval_actual_cost=_decimal(payload["observation_interval_actual_cost"], "observation_interval_actual_cost"),
            interval_cost_source=str(payload["interval_cost_source"]),
        )

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        for field in (
            "window_started_at",
            "window_ended_at",
            "window_resets_at",
            "interval_started_at",
            "cost_window_started_at",
            "cost_window_ended_at",
            "observation_interval_started_at",
        ):
            data[field] = _iso(data[field])
        for field in (
            "account_standard_cost",
            "account_actual_cost",
            "interval_standard_cost",
            "interval_actual_cost",
            "residual_standard_cost",
            "residual_actual_cost",
            "raw_selected_total_cost",
            "total_standard_cost",
            "total_actual_cost",
            "observation_interval_standard_cost",
            "observation_interval_actual_cost",
        ):
            data[field] = _money(data[field])
        return data


@dataclass(frozen=True)
class UserCostPayload:
    sample_id: int | None
    point_id: int
    account_id: int
    sub2api_user_id: int
    username: str
    email: str
    observed_at: datetime
    window_started_at: datetime | None
    window_ended_at: datetime | None
    window_resets_at: datetime
    total_standard_cost: Decimal
    total_actual_cost: Decimal
    interval_started_at: datetime | None
    interval_standard_cost: Decimal | None
    interval_actual_cost: Decimal | None
    interval_source: str

    FIELDS = frozenset(
        {
            "sample_id",
            "point_id",
            "account_id",
            "sub2api_user_id",
            "username",
            "email",
            "observed_at",
            "window_started_at",
            "window_ended_at",
            "window_resets_at",
            "total_standard_cost",
            "total_actual_cost",
            "interval_started_at",
            "interval_standard_cost",
            "interval_actual_cost",
            "interval_source",
        }
    )

    @classmethod
    def from_json(cls, raw: Any) -> "UserCostPayload":
        payload = _require_exact(raw, cls.FIELDS, "user_cost")
        sample_id = payload["sample_id"]
        return cls(
            sample_id=int(sample_id) if sample_id is not None else None,
            point_id=int(payload["point_id"]),
            account_id=int(payload["account_id"]),
            sub2api_user_id=int(payload["sub2api_user_id"]),
            username=str(payload["username"]),
            email=str(payload["email"]),
            observed_at=_datetime(payload["observed_at"], "observed_at", nullable=False),
            window_started_at=_datetime(payload["window_started_at"], "window_started_at"),
            window_ended_at=_datetime(payload["window_ended_at"], "window_ended_at"),
            window_resets_at=_datetime(payload["window_resets_at"], "window_resets_at", nullable=False),
            total_standard_cost=_decimal(payload["total_standard_cost"], "total_standard_cost", nullable=False),
            total_actual_cost=_decimal(payload["total_actual_cost"], "total_actual_cost", nullable=False),
            interval_started_at=_datetime(payload["interval_started_at"], "interval_started_at"),
            interval_standard_cost=_decimal(payload["interval_standard_cost"], "interval_standard_cost"),
            interval_actual_cost=_decimal(payload["interval_actual_cost"], "interval_actual_cost"),
            interval_source=str(payload["interval_source"]),
        )

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        for field in (
            "observed_at",
            "window_started_at",
            "window_ended_at",
            "window_resets_at",
            "interval_started_at",
        ):
            data[field] = _iso(data[field])
        for field in (
            "total_standard_cost",
            "total_actual_cost",
            "interval_standard_cost",
            "interval_actual_cost",
        ):
            data[field] = _money(data[field])
        return data


@dataclass(frozen=True)
class FastDetailPayload:
    sub2api_user_id: int
    fast_request_count: int
    request_count: int | None
    fast_standard_cost: Decimal
    fast_actual_cost: Decimal
    standard_correction_cost: Decimal
    actual_correction_cost: Decimal

    @classmethod
    def from_json(cls, raw: Any) -> "FastDetailPayload":
        fields = frozenset(cls.__dataclass_fields__)
        payload = _require_exact(raw, fields, "fast_detail")
        request_count = payload["request_count"]
        return cls(
            sub2api_user_id=int(payload["sub2api_user_id"]),
            fast_request_count=int(payload["fast_request_count"]),
            request_count=int(request_count) if request_count is not None else None,
            fast_standard_cost=_decimal(payload["fast_standard_cost"], "fast_standard_cost", nullable=False),
            fast_actual_cost=_decimal(payload["fast_actual_cost"], "fast_actual_cost", nullable=False),
            standard_correction_cost=_decimal(payload["standard_correction_cost"], "standard_correction_cost", nullable=False),
            actual_correction_cost=_decimal(payload["actual_correction_cost"], "actual_correction_cost", nullable=False),
        )

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        for field in (
            "fast_standard_cost",
            "fast_actual_cost",
            "standard_correction_cost",
            "actual_correction_cost",
        ):
            data[field] = _money(data[field])
        return data


@dataclass(frozen=True)
class FastFactPayload:
    observation_id: int
    started_at: datetime | None
    request_count: int | None
    standard_correction_cost: Decimal | None
    actual_correction_cost: Decimal | None
    details: tuple[FastDetailPayload, ...]

    FIELDS = frozenset(
        {
            "observation_id",
            "started_at",
            "request_count",
            "standard_correction_cost",
            "actual_correction_cost",
            "details",
        }
    )

    @classmethod
    def from_json(cls, raw: Any) -> "FastFactPayload":
        payload = _require_exact(raw, cls.FIELDS, "fast_fact")
        details = payload["details"]
        if not isinstance(details, list):
            raise HistoricalRebuildError("fast_fact details 必须为数组")
        request_count = payload["request_count"]
        return cls(
            observation_id=int(payload["observation_id"]),
            started_at=_datetime(payload["started_at"], "started_at"),
            request_count=int(request_count) if request_count is not None else None,
            standard_correction_cost=_decimal(payload["standard_correction_cost"], "standard_correction_cost"),
            actual_correction_cost=_decimal(payload["actual_correction_cost"], "actual_correction_cost"),
            details=tuple(FastDetailPayload.from_json(item) for item in details),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "started_at": _iso(self.started_at),
            "request_count": self.request_count,
            "standard_correction_cost": _money(self.standard_correction_cost),
            "actual_correction_cost": _money(self.actual_correction_cost),
            "details": [item.as_json() for item in self.details],
        }


def observation_cost_payload(point: UsageSamplePoint) -> ObservationCostPayload:
    observation = point.observations.order_by("id").first()
    return ObservationCostPayload(
        point_id=point.id,
        fact_revision=point.fact_revision,
        observation_id=observation.id if observation else None,
        window_started_at=point.window_started_at,
        window_ended_at=point.window_ended_at,
        window_resets_at=point.window_resets_at,
        account_standard_cost=point.account_standard_cost,
        account_actual_cost=point.account_actual_cost,
        interval_started_at=point.interval_started_at,
        interval_standard_cost=point.interval_standard_cost,
        interval_actual_cost=point.interval_actual_cost,
        residual_standard_cost=point.residual_standard_cost,
        residual_actual_cost=point.residual_actual_cost,
        expected_user_count=point.expected_user_count,
        expected_user_digest=point.expected_user_digest,
        write_status=point.write_status,
        reconciliation_status=point.reconciliation_status,
        raw_selected_total_cost=(observation.raw_selected_total_cost if observation else None),
        total_standard_cost=(observation.total_standard_cost if observation else None),
        total_actual_cost=(observation.total_actual_cost if observation else None),
        cost_window_started_at=(observation.cost_window_started_at if observation else None),
        cost_window_ended_at=(observation.cost_window_ended_at if observation else None),
        observation_interval_started_at=(observation.interval_cost_started_at if observation else None),
        observation_interval_standard_cost=(observation.interval_standard_cost if observation else None),
        observation_interval_actual_cost=(observation.interval_actual_cost if observation else None),
        interval_cost_source=(observation.interval_cost_source if observation else ""),
    )


def user_cost_payload(sample: Sub2APIUserUsageSample) -> UserCostPayload:
    return UserCostPayload(
        sample_id=sample.id,
        point_id=sample.sample_point_id,
        account_id=sample.account_id,
        sub2api_user_id=sample.sub2api_user_id,
        username=sample.username,
        email=sample.email,
        observed_at=sample.observed_at,
        window_started_at=sample.window_started_at,
        window_ended_at=sample.window_ended_at,
        window_resets_at=sample.window_resets_at,
        total_standard_cost=sample.total_standard_cost,
        total_actual_cost=sample.total_actual_cost,
        interval_started_at=sample.interval_started_at,
        interval_standard_cost=sample.interval_standard_cost,
        interval_actual_cost=sample.interval_actual_cost,
        interval_source=sample.interval_source,
    )


def fast_fact_payload(observation: Observation) -> FastFactPayload:
    return FastFactPayload(
        observation_id=observation.id,
        started_at=observation.fast_correction_started_at,
        request_count=observation.fast_correction_request_count,
        standard_correction_cost=observation.fast_correction_standard_cost,
        actual_correction_cost=observation.fast_correction_actual_cost,
        details=tuple(
            FastDetailPayload(
                sub2api_user_id=row.sub2api_user_id,
                fast_request_count=row.fast_request_count,
                request_count=row.request_count,
                fast_standard_cost=row.fast_standard_cost,
                fast_actual_cost=row.fast_actual_cost,
                standard_correction_cost=row.standard_correction_cost,
                actual_correction_cost=row.actual_correction_cost,
            )
            for row in observation.fast_corrections.order_by(
                "sub2api_user_id", "id"
            )
        ),
    )


def validate_patch_payload(kind: str, payload: Any):
    if kind == "observation_cost":
        return ObservationCostPayload.from_json(payload)
    if kind == "user_cost":
        return UserCostPayload.from_json(payload)
    if kind == "fast_fact":
        return FastFactPayload.from_json(payload)
    raise HistoricalRebuildError("未知 typed patch kind")


def config_digest(config: AppSettings) -> str:
    fields = (
        "openai_account_id",
        "timezone",
        "cost_basis",
        "weekly_quota_model",
        "fast_correction_enabled",
        "initial_usd_per_percent",
        "safety_factor",
        "daily_estimate_min_percent_span",
        "sub2api_base_url",
        "sub2api_usage_log_query_horizon_days",
    )
    return canonical_digest({field: getattr(config, field) for field in fields})


def participant_policy_digest() -> str:
    return canonical_rows_digest(
        Participant.objects.order_by("id")
        .values(
            "id",
            "sub2api_user_id",
            "share_percent",
            "enabled",
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
            "users": Sub2APIUserUsageSample.objects.filter(
                account_id=account_id
            )
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


def observable_digest(account_id: int) -> str:
    return canonical_object_digest(
        scalars={"source": source_fact_digest(account_id)},
        row_sections={
            "participants": Participant.objects.order_by("id")
            .values(
                "id",
                "latest_balance_usd",
                "latest_selected_cost",
                "last_checked_at",
            )
            .iterator(chunk_size=512)
        },
    )


def plan_digest(run: HistoricalRebuildRun) -> str:
    return canonical_object_digest(
        scalars={
            "id": str(run.id),
            "account_id": run.account_id,
            "mode": run.mode,
            "base_revision": run.base_revision,
            "cutoff": _iso(run.cutoff),
            "requested_started_at": _iso(run.requested_started_at),
            "requested_ended_at": _iso(run.requested_ended_at),
            "source_digest": run.source_digest,
            "algorithm_version": run.algorithm_version,
            "build_id": run.build_id,
            "config_digest": run.config_digest,
            "participant_policy_digest": run.participant_policy_digest,
            "expires_at": _iso(run.expires_at),
            "blockers": run.blockers,
        },
        row_sections={
            "coverage": run.coverage_rows.order_by("id")
            .values(
                "point_id",
                "started_at",
                "ended_at",
                "dimension",
                "status",
                "evidence_type",
                "evidence_digest",
                "blocker",
            )
            .iterator(chunk_size=512),
            "patches": run.patches.order_by("sequence")
            .values(
                "sequence",
                "kind",
                "sample_point_id",
                "observation_id",
                "user_sample_id",
                "sub2api_user_id",
                "natural_key",
                "schema_version",
                "before_payload",
                "after_payload",
                "required_coverage_ids",
            )
            .iterator(chunk_size=512),
        },
    )
