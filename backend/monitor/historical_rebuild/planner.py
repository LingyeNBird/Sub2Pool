"""Persistent immutable rebuild-plan creation."""
from __future__ import annotations
from dataclasses import dataclass, field

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import inspect
from typing import Any

from django.utils import timezone

from ..fact_utils import canonical_digest, expected_user_digest
from ..fast_correction.constants import FAST_EXTRA_FACTOR
from ..fast_correction.domain import money
from ..history_state import current_fact_revision
from ..integrations.sub2api import (
    Sub2APIClient,
    Sub2APIUsageLog,
    UsageLogScan,
)
from ..models import (
    AppSettings,
    HistoricalRebuildCoverage,
    HistoricalRebuildPatch,
    HistoricalRebuildRun,
    UsageSamplePoint,
)
from .audit import AuditIssue, audit_account
from .contracts import (
    ALGORITHM_VERSION,
    BUILD_ID,
    MODE_AUDIT_REPLAY,
    MODE_VERIFIED_REMOTE_REPAIR,
    REBUILD_MODES,
    SAFE_COVERAGE,
    FastDetailPayload,
    FastFactPayload,
    HistoricalRebuildError,
    ObservationCostPayload,
    UserCostPayload,
    config_digest,
    fast_fact_payload,
    observation_cost_payload,
    observable_digest,
    participant_policy_digest,
    plan_digest,
    source_fact_digest,
    user_cost_payload,
)

ZERO = Decimal("0")
PLAN_TTL = timedelta(minutes=30)
PATCHABLE_AUDIT_CODES = frozenset(
    {
        "negative_cost",
        "missing_account_total",
        "missing_residual",
        "account_user_residual_mismatch",
        "observation_point_cost_mismatch",
        "fast_parent_cost_mismatch",
        "fast_parent_count_mismatch",
        "expected_user_count_mismatch",
        "expected_user_digest_mismatch",
        "user_interval_discontinuity",
        "user_interval_total_mismatch",
        "interval_user_residual_mismatch",
        "missing_interval_total",
    }
)
DIMENSIONS = (
    "account_cost",
    "user_cost",
    "fast_cost",
    "request_count",
    "api_key",
)
@dataclass
class _UsageBucket:
    standard_cost: Decimal = ZERO
    actual_cost: Decimal = ZERO
    request_count: int = 0
    fast_request_count: int = 0
    fast_standard_cost: Decimal = ZERO
    fast_actual_cost: Decimal = ZERO

    def add(self, row: Sub2APIUsageLog) -> None:
        self.standard_cost += row.total_cost
        self.actual_cost += row.actual_cost
        self.request_count += 1
        if row.service_tier == "priority":
            self.fast_request_count += 1
            self.fast_standard_cost += row.total_cost
            self.fast_actual_cost += row.actual_cost

    def merge(self, other: "_UsageBucket") -> None:
        self.standard_cost += other.standard_cost
        self.actual_cost += other.actual_cost
        self.request_count += other.request_count
        self.fast_request_count += other.fast_request_count
        self.fast_standard_cost += other.fast_standard_cost
        self.fast_actual_cost += other.fast_actual_cost


@dataclass
class _ChunkAggregate:
    total: _UsageBucket = field(default_factory=_UsageBucket)
    users: dict[int, _UsageBucket] = field(default_factory=dict)

    def add(self, row: Sub2APIUsageLog) -> None:
        self.total.add(row)
        self.users.setdefault(row.user_id, _UsageBucket()).add(row)


@dataclass
class _PointAggregate:
    total: _UsageBucket = field(default_factory=_UsageBucket)
    interval: _UsageBucket = field(default_factory=_UsageBucket)
    users_total: dict[int, _UsageBucket] = field(default_factory=dict)
    users_interval: dict[int, _UsageBucket] = field(default_factory=dict)

    def merge_chunk(
        self,
        aggregate: _ChunkAggregate,
        *,
        is_interval: bool,
    ) -> None:
        self.total.merge(aggregate.total)
        for user_id, bucket in aggregate.users.items():
            self.users_total.setdefault(user_id, _UsageBucket()).merge(bucket)
        if not is_interval:
            return
        self.interval.merge(aggregate.total)
        for user_id, bucket in aggregate.users.items():
            self.users_interval.setdefault(user_id, _UsageBucket()).merge(bucket)


@dataclass(frozen=True)
class _CachedChunk:
    aggregate: _ChunkAggregate
    scan: UsageLogScan



def _status_priority(status: str) -> int:
    return {
        "verified": 5,
        "verified_empty": 5,
        "captured_local": 4,
        "policy_only": 3,
        "unknown": 2,
        "unavailable": 1,
    }.get(status, 0)


def _coverage_range(point: UsageSamplePoint) -> tuple[datetime, datetime]:
    started_at = point.window_started_at or point.interval_started_at
    if started_at is None or started_at >= point.observed_at:
        started_at = point.observed_at - timedelta(microseconds=1)
    return started_at, point.observed_at


def _local_coverage_status(
    point: UsageSamplePoint,
    dimension: str,
) -> tuple[str, str]:
    if dimension == "api_key":
        return "unavailable", "本地采样不保存逐请求 API Key 构成"
    if point.write_status != "complete":
        return "unknown", "旧数据没有原子写入完整性证据"
    if dimension == "request_count":
        observation = point.observations.order_by("id").first()
        if observation is None or observation.fast_correction_request_count is None:
            return "unknown", "本地采样未保存完整请求数"
    if dimension == "fast_cost":
        observation = point.observations.order_by("id").first()
        if observation is None:
            return "unavailable", "非百分比采样点没有 FAST 区间"
        if (
            observation.fast_correction_standard_cost is None
            or observation.fast_correction_actual_cost is None
        ):
            return "unknown", "该观测没有 FAST 成本事实"
    return "captured_local", ""


def _persist_local_coverage(
    run: HistoricalRebuildRun,
    points,
) -> None:
    rows: list[HistoricalRebuildCoverage] = []
    for point in points:
        started_at, ended_at = _coverage_range(point)
        for dimension in DIMENSIONS:
            status, blocker = _local_coverage_status(point, dimension)
            rows.append(
                HistoricalRebuildCoverage(
                    run=run,
                    point=point,
                    started_at=started_at,
                    ended_at=ended_at,
                    dimension=dimension,
                    status=status,
                    evidence_type="local_sampling_point",
                    evidence_digest=canonical_digest(
                        {
                            "point_id": point.id,
                            "fact_revision": point.fact_revision,
                            "write_status": point.write_status,
                            "dimension": dimension,
                        }
                    ),
                    blocker=blocker,
                )
            )
            if len(rows) >= 500:
                HistoricalRebuildCoverage.objects.bulk_create(rows)
                rows.clear()
    if rows:
        HistoricalRebuildCoverage.objects.bulk_create(rows)


def _chunks(
    started_at: datetime,
    ended_at: datetime,
    *,
    boundary: datetime | None = None,
):
    cursor = started_at
    daily_boundary = started_at + timedelta(days=1)
    boundary_pending = (
        boundary is not None and started_at < boundary < ended_at
    )
    while cursor < ended_at:
        candidates = [ended_at, daily_boundary]
        if boundary_pending and boundary is not None:
            candidates.append(boundary)
        next_cursor = min(value for value in candidates if value > cursor)
        yield cursor, next_cursor
        cursor = next_cursor
        if boundary_pending and cursor == boundary:
            boundary_pending = False
        if cursor == daily_boundary:
            daily_boundary += timedelta(days=1)


def _fallback_scan(
    client,
    *,
    account_id: int,
    started_at: datetime,
    ended_at: datetime,
    timezone_name: str,
) -> UsageLogScan:
    rows = tuple(
        client.usage_logs(
            account_id=account_id,
            started_at=started_at,
            ended_at=ended_at,
            timezone_name=timezone_name,
        )
    )
    return UsageLogScan(
        rows=rows,
        started_at=started_at,
        ended_at=ended_at,
        returned_total=len(rows),
        returned_pages=1,
        scanned_pages=1,
        out_of_range_count=0,
        scan_digest=canonical_digest(
            {
                "fallback": True,
                "ids": [row.id for row in rows],
                "started_at": started_at,
                "ended_at": ended_at,
            }
        ),
        evidence_type="legacy_client_rows_only",
        coverage=tuple(
            (dimension, "unavailable" if dimension == "api_key" else "policy_only")
            for dimension in DIMENSIONS
        ),
        expected_user_ids=None,
    )


def _scan_point(
    client,
    *,
    point: UsageSamplePoint,
    started_at: datetime,
    ended_at: datetime,
    timezone_name: str,
    cache: dict[tuple[int, datetime, datetime, str], _CachedChunk],
) -> tuple[
    _PointAggregate,
    dict[str, str],
    str,
    str,
    tuple[int, ...] | None,
]:
    interval_started_at = max(
        point.interval_started_at or started_at,
        started_at,
    )
    point_aggregate = _PointAggregate()
    scans: list[UsageLogScan] = []
    expected_users: tuple[int, ...] | None = None
    for chunk_start, chunk_end in _chunks(
        started_at,
        ended_at,
        boundary=interval_started_at,
    ):
        key = (point.account_id, chunk_start, chunk_end, timezone_name)
        cached = cache.get(key)
        if cached is None:
            aggregate = _ChunkAggregate()
            callback_count = 0

            def consume(row: Sub2APIUsageLog) -> None:
                nonlocal callback_count
                if row.account_id != point.account_id:
                    raise HistoricalRebuildError(
                        "请求日志 scan envelope 账号不匹配"
                    )
                if not chunk_start <= row.created_at < chunk_end:
                    raise HistoricalRebuildError(
                        "请求日志 scan envelope 行越界"
                    )
                callback_count += 1
                aggregate.add(row)

            scan_method = getattr(client, "usage_log_scan", None)
            if callable(scan_method):
                parameters = inspect.signature(scan_method).parameters.values()
                supports_streaming = any(
                    parameter.name == "row_consumer"
                    or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
                kwargs = {
                    "account_id": point.account_id,
                    "started_at": chunk_start,
                    "ended_at": chunk_end,
                    "timezone_name": timezone_name,
                }
                if supports_streaming:
                    try:
                        scan = scan_method(
                            **kwargs,
                            row_consumer=consume,
                            collect_rows=False,
                        )
                    except TypeError as exc:
                        message = str(exc)
                        if (
                            "unexpected keyword argument" not in message
                            or (
                                "row_consumer" not in message
                                and "collect_rows" not in message
                            )
                        ):
                            raise
                        aggregate = _ChunkAggregate()
                        callback_count = 0
                        supports_streaming = False
                        scan = scan_method(**kwargs)
                else:
                    scan = scan_method(**kwargs)
            else:
                supports_streaming = False
                scan = _fallback_scan(
                    client,
                    account_id=point.account_id,
                    started_at=chunk_start,
                    ended_at=chunk_end,
                    timezone_name=timezone_name,
                )
            if scan.started_at != chunk_start or scan.ended_at != chunk_end:
                raise HistoricalRebuildError(
                    "请求日志 scan envelope 区间不匹配"
                )
            if scan.out_of_range_count:
                raise HistoricalRebuildError(
                    "请求日志 scan envelope 包含越界行"
                )
            if callback_count == 0:
                for row in scan.rows:
                    consume(row)
            elif scan.rows:
                for row in scan.rows:
                    if (
                        row.account_id != point.account_id
                        or not chunk_start <= row.created_at < chunk_end
                    ):
                        raise HistoricalRebuildError(
                            "请求日志 scan envelope 行越界"
                        )
            cached = _CachedChunk(aggregate=aggregate, scan=scan)
            cache[key] = cached

        scan = cached.scan
        point_aggregate.merge_chunk(
            cached.aggregate,
            is_interval=chunk_start >= interval_started_at,
        )
        if scan.expected_user_ids is not None:
            current = tuple(sorted(set(scan.expected_user_ids)))
            if expected_users is None:
                expected_users = current
            elif expected_users != current:
                raise HistoricalRebuildError(
                    "验证证据的 expected-user 集合发生变化"
                )
        scans.append(scan)

    statuses: dict[str, str] = {}
    for dimension in DIMENSIONS:
        values = [scan.coverage_status(dimension) for scan in scans]
        unsafe = [value for value in values if value not in SAFE_COVERAGE]
        if unsafe:
            status = min(unsafe, key=_status_priority)
        else:
            status = (
                "verified"
                if point_aggregate.total.request_count
                else "verified_empty"
            )
        statuses[dimension] = status
    evidence_digest = canonical_digest(
        {
            "point_id": point.id,
            "scans": [scan.scan_digest for scan in scans],
            "request_count": point_aggregate.total.request_count,
            "expected_users": expected_users,
        }
    )
    evidence_type = "+".join(
        sorted({scan.evidence_type for scan in scans})
    )
    return (
        point_aggregate,
        statuses,
        evidence_type,
        evidence_digest,
        expected_users,
    )


def _coverage_blocker(dimension: str, status: str) -> str:
    if status in SAFE_COVERAGE or status == "out_of_scope":
        return ""
    if status == "policy_only":
        return "分页一致、exact_total 或查询天数不能证明历史未被删除"
    if status == "unavailable":
        return f"上游未提供 {dimension} 的独立覆盖证据"
    return f"{dimension} 覆盖未知"


def _build_point_patches(
    run: HistoricalRebuildRun,
    point: UsageSamplePoint,
    aggregate: _PointAggregate,
    expected_user_ids: tuple[int, ...],
    coverage_by_dimension: dict[str, HistoricalRebuildCoverage],
    sequence: int,
    cost_basis: str,
) -> tuple[list[HistoricalRebuildPatch], int]:
    existing_users = {
        row.sub2api_user_id: row
        for row in point.user_samples.order_by("sub2api_user_id", "id")
    }
    unexpected = set(existing_users) - set(expected_user_ids)
    if unexpected:
        raise HistoricalRebuildError(
            "验证证据未覆盖本地点中的额外用户，拒绝删除历史用户事实",
            {"point_id": point.id, "unexpected_user_ids": sorted(unexpected)},
        )
    unexpected_log_users = set(aggregate.users_total) - set(expected_user_ids)
    if unexpected_log_users:
        raise HistoricalRebuildError(
            "验证证据的请求日志包含 expected-user 集合外用户",
            {
                "point_id": point.id,
                "unexpected_log_user_ids": sorted(unexpected_log_users),
            },
        )
    if point.window_started_at is None or point.window_resets_at is None:
        raise HistoricalRebuildError("采样点缺少可替换成本窗口")
    interval_started_at = point.interval_started_at or point.window_started_at
    total_standard = aggregate.total.standard_cost
    total_actual = aggregate.total.actual_cost
    interval_standard = aggregate.interval.standard_cost
    interval_actual = aggregate.interval.actual_cost
    before_observation = observation_cost_payload(point)
    after_observation = ObservationCostPayload(
        point_id=point.id,
        fact_revision=run.base_revision + 1,
        observation_id=before_observation.observation_id,
        window_started_at=point.window_started_at,
        window_ended_at=point.observed_at,
        window_resets_at=point.window_resets_at,
        account_standard_cost=total_standard,
        account_actual_cost=total_actual,
        interval_started_at=interval_started_at,
        interval_standard_cost=interval_standard,
        interval_actual_cost=interval_actual,
        residual_standard_cost=ZERO,
        residual_actual_cost=ZERO,
        expected_user_count=len(expected_user_ids),
        expected_user_digest=expected_user_digest(expected_user_ids),
        write_status=point.write_status,
        reconciliation_status="reconciled",
        raw_selected_total_cost=(
            total_actual if cost_basis == "actual" else total_standard
        )
        if before_observation.observation_id is not None
        else None,
        total_standard_cost=(
            total_standard if before_observation.observation_id is not None else None
        ),
        total_actual_cost=(
            total_actual if before_observation.observation_id is not None else None
        ),
        cost_window_started_at=(
            point.window_started_at
            if before_observation.observation_id is not None
            else None
        ),
        cost_window_ended_at=(
            point.observed_at
            if before_observation.observation_id is not None
            else None
        ),
        observation_interval_started_at=(
            interval_started_at
            if before_observation.observation_id is not None
            else None
        ),
        observation_interval_standard_cost=(
            interval_standard
            if before_observation.observation_id is not None
            else None
        ),
        observation_interval_actual_cost=(
            interval_actual
            if before_observation.observation_id is not None
            else None
        ),
        interval_cost_source=(
            "verified_history_scan"
            if before_observation.observation_id is not None
            else ""
        ),
    )
    patches: list[HistoricalRebuildPatch] = []
    required = [
        coverage_by_dimension["account_cost"].id,
        coverage_by_dimension["user_cost"].id,
    ]
    observation = point.observations.order_by("id").first()
    if observation is not None:
        required.append(coverage_by_dimension["fast_cost"].id)
    if before_observation.as_json() != after_observation.as_json():
        patches.append(
            HistoricalRebuildPatch(
                run=run,
                sequence=sequence,
                kind="observation_cost",
                sample_point=point,
                observation=observation,
                natural_key={
                    "account_id": point.account_id,
                    "observed_at": point.observed_at.isoformat(),
                },
                before_payload=before_observation.as_json(),
                after_payload=after_observation.as_json(),
                required_coverage_ids=required,
            )
        )
        sequence += 1

    for user_id in expected_user_ids:
        existing = existing_users.get(user_id)
        before_user = user_cost_payload(existing) if existing else None
        total_bucket = aggregate.users_total.get(user_id, _UsageBucket())
        interval_bucket = aggregate.users_interval.get(
            user_id,
            _UsageBucket(),
        )
        total_user_standard = total_bucket.standard_cost
        total_user_actual = total_bucket.actual_cost
        interval_user_standard = interval_bucket.standard_cost
        interval_user_actual = interval_bucket.actual_cost
        after_user = UserCostPayload(
            sample_id=existing.id if existing else None,
            point_id=point.id,
            account_id=point.account_id,
            sub2api_user_id=user_id,
            username=existing.username if existing else "",
            email=existing.email if existing else "",
            observed_at=point.observed_at,
            window_started_at=point.window_started_at,
            window_ended_at=point.observed_at,
            window_resets_at=point.window_resets_at,
            total_standard_cost=total_user_standard,
            total_actual_cost=total_user_actual,
            interval_started_at=interval_started_at,
            interval_standard_cost=interval_user_standard,
            interval_actual_cost=interval_user_actual,
            interval_source="verified_history_scan",
        )
        if before_user is None or before_user.as_json() != after_user.as_json():
            patches.append(
                HistoricalRebuildPatch(
                    run=run,
                    sequence=sequence,
                    kind="user_cost",
                    sample_point=point,
                    user_sample=existing,
                    sub2api_user_id=user_id,
                    natural_key={
                        "account_id": point.account_id,
                        "observed_at": point.observed_at.isoformat(),
                        "sub2api_user_id": user_id,
                    },
                    before_payload=(before_user.as_json() if before_user else None),
                    after_payload=after_user.as_json(),
                    required_coverage_ids=[
                        coverage_by_dimension["user_cost"].id
                    ],
                )
            )
            sequence += 1

    if observation is not None:
        before_fast = fast_fact_payload(observation)
        correction_standard = ZERO
        correction_actual = ZERO
        count_verified = (
            coverage_by_dimension["request_count"].status in SAFE_COVERAGE
        )
        previous_details = {
            detail.sub2api_user_id: detail
            for detail in before_fast.details
        }
        details = []
        for user_id in sorted(aggregate.users_interval):
            bucket = aggregate.users_interval[user_id]
            previous_detail = previous_details.get(user_id)
            fast_standard = money(bucket.fast_standard_cost)
            fast_actual = money(bucket.fast_actual_cost)
            standard_correction = money(
                fast_standard * FAST_EXTRA_FACTOR
            )
            actual_correction = money(fast_actual * FAST_EXTRA_FACTOR)
            correction_standard += standard_correction
            correction_actual += actual_correction
            details.append(
                FastDetailPayload(
                    sub2api_user_id=user_id,
                    fast_request_count=bucket.fast_request_count,
                    request_count=(
                        bucket.request_count
                        if count_verified
                        else (
                            previous_detail.request_count
                            if previous_detail is not None
                            else None
                        )
                    ),
                    fast_standard_cost=fast_standard,
                    fast_actual_cost=fast_actual,
                    standard_correction_cost=standard_correction,
                    actual_correction_cost=actual_correction,
                )
            )
        after_fast = FastFactPayload(
            observation_id=observation.id,
            started_at=interval_started_at,
            request_count=(
                aggregate.interval.request_count
                if count_verified
                else before_fast.request_count
            ),
            standard_correction_cost=money(correction_standard),
            actual_correction_cost=money(correction_actual),
            details=tuple(details),
        )
        if before_fast.as_json() != after_fast.as_json():
            fast_required = [coverage_by_dimension["fast_cost"].id]
            if count_verified:
                fast_required.append(
                    coverage_by_dimension["request_count"].id
                )
            patches.append(
                HistoricalRebuildPatch(
                    run=run,
                    sequence=sequence,
                    kind="fast_fact",
                    sample_point=point,
                    observation=observation,
                    natural_key={"observation_id": observation.id},
                    before_payload=before_fast.as_json(),
                    after_payload=after_fast.as_json(),
                    required_coverage_ids=fast_required,
                )
            )
            sequence += 1
    return patches, sequence


def _remote_plan(
    run: HistoricalRebuildRun,
    config: AppSettings,
    points,
    *,
    client_factory,
) -> tuple[set[int], list[dict[str, Any]]]:
    horizon_started_at = timezone.now() - timedelta(
        days=config.sub2api_usage_log_query_horizon_days
    )
    safe_points: set[int] = set()
    blockers: list[dict[str, Any]] = []
    sequence = 1
    any_safe_coverage = False
    cache: dict[tuple[int, datetime, datetime, str], _CachedChunk] = {}
    with client_factory(config) as client:
        for point in points:
            started_at, ended_at = _coverage_range(point)
            within_requested_range = (
                (
                    run.requested_started_at is None
                    or started_at >= run.requested_started_at
                )
                and (
                    run.requested_ended_at is None
                    or ended_at <= run.requested_ended_at
                )
            )
            within_query_horizon = started_at >= horizon_started_at
            out_of_scope = (
                not within_requested_range or not within_query_horizon
            )
            if out_of_scope:
                statuses = {
                    dimension: "out_of_scope" for dimension in DIMENSIONS
                }
                evidence_type = (
                    "outside_requested_range"
                    if not within_requested_range
                    else "outside_query_horizon"
                )
                evidence_digest = canonical_digest(
                    {
                        "point_id": point.id,
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "evidence_type": evidence_type,
                    }
                )
                expected_users = None
                aggregate = _PointAggregate()
            elif point.window_started_at is None:
                statuses = {
                    dimension: (
                        "unavailable" if dimension == "api_key" else "unknown"
                    )
                    for dimension in DIMENSIONS
                }
                evidence_type = "unqueryable_legacy_point"
                evidence_digest = ""
                expected_users = None
                aggregate = _PointAggregate()
            else:
                (
                    aggregate,
                    statuses,
                    evidence_type,
                    evidence_digest,
                    expected_users,
                ) = _scan_point(
                    client,
                    point=point,
                    started_at=started_at,
                    ended_at=ended_at,
                    timezone_name=config.timezone,
                    cache=cache,
                )

            coverage_rows = []
            for dimension in DIMENSIONS:
                status = statuses[dimension]
                coverage_rows.append(
                    HistoricalRebuildCoverage.objects.create(
                        run=run,
                        point=point,
                        started_at=started_at,
                        ended_at=ended_at,
                        dimension=dimension,
                        status=status,
                        evidence_type=evidence_type,
                        evidence_digest=evidence_digest,
                        blocker=_coverage_blocker(dimension, status),
                    )
                )
            coverage_by_dimension = {
                row.dimension: row for row in coverage_rows
            }
            if out_of_scope:
                continue
            required_dimensions = ["account_cost", "user_cost"]
            if point.observations.exists():
                required_dimensions.append("fast_cost")
            coverage_safe = all(
                statuses[dimension] in SAFE_COVERAGE
                for dimension in required_dimensions
            )
            if coverage_safe:
                any_safe_coverage = True
            if not coverage_safe or expected_users is None:
                blockers.append(
                    {
                        "code": "coverage_not_verified",
                        "severity": "hard",
                        "point_id": point.id,
                        "message": (
                            "远端扫描缺少逐维独立覆盖证据"
                            if not coverage_safe
                            else "用户成本证据缺少 expected-user 集合"
                        ),
                    }
                )
                continue
            try:
                patches, sequence = _build_point_patches(
                    run,
                    point,
                    aggregate,
                    expected_users,
                    coverage_by_dimension,
                    sequence,
                    config.cost_basis,
                )
            except HistoricalRebuildError as exc:
                blockers.append(
                    {
                        "code": "candidate_generation_failed",
                        "severity": "hard",
                        "point_id": point.id,
                        "message": str(exc),
                    }
                )
                continue
            HistoricalRebuildPatch.objects.bulk_create(patches)
            safe_points.add(point.id)
    if not any_safe_coverage:
        blockers.append(
            {
                "code": "no_verified_remote_coverage",
                "severity": "hard",
                "point_id": None,
                "message": "没有任何事实组获得可安全应用的远端覆盖证明",
            }
        )
    return safe_points, blockers


def _audit_blockers(
    issues: tuple[AuditIssue, ...],
    *,
    mode: str,
    safe_points: set[int],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for issue in issues:
        payload = issue.as_dict()
        if (
            mode == MODE_VERIFIED_REMOTE_REPAIR
            and issue.severity == "hard"
            and issue.code in PATCHABLE_AUDIT_CODES
            and issue.point_id in safe_points
        ):
            payload["severity"] = "warning"
            payload["resolved_by_patch"] = True
        blockers.append(payload)
    return blockers


def _patch_summary(run: HistoricalRebuildRun) -> dict[str, int]:
    patch_counts = Counter(run.patches.values_list("kind", flat=True))
    return {
        "total": sum(patch_counts.values()),
        **{
            kind: patch_counts.get(kind, 0)
            for kind in ("observation_cost", "user_cost", "fast_fact")
        },
    }


def create_rebuild_plan(
    config: AppSettings,
    mode: str,
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    client_factory=None,
) -> HistoricalRebuildRun:
    """Create one immutable plan. Apply never repeats this work or calls upstream."""

    if mode not in REBUILD_MODES:
        raise HistoricalRebuildError("未知的历史维护模式")
    if not config.openai_account_id:
        raise HistoricalRebuildError("尚未配置 OpenAI 上游账号")
    if started_at is not None and ended_at is not None and started_at >= ended_at:
        raise HistoricalRebuildError("显式远端范围必须为非空半开区间")
    client_factory = client_factory or Sub2APIClient
    account_id = config.openai_account_id
    base_revision = current_fact_revision(account_id)
    initial_config_digest = config_digest(config)
    initial_policy_digest = participant_policy_digest()
    initial_source_digest = source_fact_digest(account_id)
    points = (
        UsageSamplePoint.objects.filter(account_id=account_id)
        .prefetch_related("observations__fast_corrections", "user_samples")
        .order_by("observed_at", "id")
    )
    cutoff = (
        UsageSamplePoint.objects.filter(account_id=account_id)
        .order_by("-observed_at", "-id")
        .values_list("observed_at", flat=True)
        .first()
    )
    audit = audit_account(account_id)
    run = HistoricalRebuildRun.objects.create(
        account_id=account_id,
        mode=mode,
        state="generating",
        base_revision=base_revision,
        cutoff=cutoff,
        requested_started_at=started_at,
        requested_ended_at=ended_at,
        source_digest=initial_source_digest,
        algorithm_version=ALGORITHM_VERSION,
        build_id=BUILD_ID,
        config_digest=initial_config_digest,
        participant_policy_digest=initial_policy_digest,
        expires_at=timezone.now() + PLAN_TTL,
        before_source_hash=initial_source_digest,
        before_observable_hash=observable_digest(account_id),
    )

    safe_points: set[int] = set()
    remote_blockers: list[dict[str, Any]] = []
    try:
        if mode == MODE_AUDIT_REPLAY:
            _persist_local_coverage(
                run,
                points.iterator(chunk_size=128),
            )
        else:
            safe_points, remote_blockers = _remote_plan(
                run,
                config,
                points.iterator(chunk_size=128),
                client_factory=client_factory,
            )
    except Exception as exc:
        run.state = "failed"
        run.blockers = [
            {
                "code": "plan_generation_failed",
                "severity": "hard",
                "point_id": None,
                "message": str(exc),
            }
        ]
        run.patch_summary = _patch_summary(run)
        run.save(update_fields=["state", "blockers", "patch_summary"])
        run.plan_digest = plan_digest(run)
        run.save(update_fields=["plan_digest"])
        raise

    blockers = _audit_blockers(
        audit.issues,
        mode=mode,
        safe_points=safe_points,
    ) + remote_blockers
    patch_summary = _patch_summary(run)

    config.refresh_from_db()
    stale_reasons = []
    if current_fact_revision(account_id) != base_revision:
        stale_reasons.append("fact_revision_changed")
    if config_digest(config) != initial_config_digest:
        stale_reasons.append("config_changed")
    if participant_policy_digest() != initial_policy_digest:
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
    run.patch_summary = patch_summary
    run.state = state
    run.save(update_fields=["blockers", "patch_summary", "state"])
    run.plan_digest = plan_digest(run)
    run.save(update_fields=["plan_digest"])
    return run


def can_rollback(run: HistoricalRebuildRun) -> bool:
    if run.state != "applied":
        return False
    latest = (
        HistoricalRebuildRun.objects.filter(
            account_id=run.account_id,
            state="applied",
        )
        .order_by("-applied_at", "-created_at")
        .first()
    )
    return latest is not None and latest.id == run.id


def rebuild_plan_data(run: HistoricalRebuildRun) -> dict[str, Any]:
    coverage = [
        {
            "id": row.id,
            "point_id": row.point_id,
            "started_at": row.started_at.isoformat(),
            "ended_at": row.ended_at.isoformat(),
            "dimension": row.dimension,
            "status": row.status,
            "evidence_type": row.evidence_type,
            "evidence_digest": row.evidence_digest,
            "blocker": row.blocker,
        }
        for row in run.coverage_rows.order_by("started_at", "dimension", "id")
    ]
    unknown = any(
        row["status"]
        not in SAFE_COVERAGE | {"captured_local", "out_of_scope"}
        for row in coverage
    )
    return {
        "id": str(run.id),
        "account_id": run.account_id,
        "mode": run.mode,
        "state": run.state,
        "digest": run.plan_digest,
        "created_at": run.created_at.isoformat(),
        "expires_at": run.expires_at.isoformat(),
        "base_revision": run.base_revision,
        "result_revision": run.result_revision,
        "rollback_revision": run.rollback_revision,
        "cutoff": run.cutoff.isoformat() if run.cutoff else None,
        "coverage": coverage,
        "blockers": run.blockers,
        "patch_summary": run.patch_summary,
        "safe_to_apply": (
            run.state == "ready" and run.expires_at > timezone.now()
        ),
        "unknown_coverage": unknown,
        "applied_with_unknown_coverage": (
            run.state in {"applied", "rolled_back"} and unknown
        ),
        "can_rollback": can_rollback(run),
        "rollback_boundary": "touched_source_then_deterministic_replay",
        "algorithm_version": run.algorithm_version,
        "build_id": run.build_id,
    }
