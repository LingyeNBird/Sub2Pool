"""Fail-closed audit of every canonical sampling point."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterable

from ..fact_utils import expected_user_digest
from ..models import (
    Observation,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)

ZERO = Decimal("0")
TOLERANCE = Decimal("0.000001")


@dataclass(frozen=True)
class AuditIssue:
    code: str
    severity: str
    message: str
    point_id: int | None = None
    dimension: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AuditResult:
    account_id: int
    point_count: int
    issues: tuple[AuditIssue, ...]

    @property
    def hard_blockers(self) -> tuple[AuditIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "hard")

    @property
    def warnings(self) -> tuple[AuditIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity != "hard")


def _different(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is not right
    return abs(left - right) > TOLERANCE


def _issue(
    issues: list[AuditIssue],
    code: str,
    message: str,
    point: UsageSamplePoint | None,
    *,
    severity: str = "hard",
    dimension: str | None = None,
) -> None:
    issues.append(
        AuditIssue(
            code=code,
            severity=severity,
            message=message,
            point_id=point.id if point else None,
            dimension=dimension,
        )
    )


def _nonnegative(
    issues: list[AuditIssue],
    point: UsageSamplePoint,
    values: Iterable[tuple[str, Decimal | None]],
    dimension: str,
) -> None:
    for field, value in values:
        if value is not None and value < ZERO:
            _issue(
                issues,
                "negative_cost",
                f"{field} 为负数",
                point,
                dimension=dimension,
            )


def audit_account(account_id: int) -> AuditResult:
    """Audit all observation and non-observation points without network access."""

    points = (
        UsageSamplePoint.objects.filter(account_id=account_id)
        .prefetch_related(
            "user_samples",
            "observations__fast_corrections",
            "participant_usage_samples",
            "balance_samples",
        )
        .order_by("observed_at", "id")
    )
    issues: list[AuditIssue] = []
    if not points.exists():
        _issue(issues, "no_points", "尚无可审计的采样点", None)

    previous: UsageSamplePoint | None = None
    previous_users: dict[int, Sub2APIUserUsageSample] = {}
    point_count = 0
    for point in points.iterator(chunk_size=128):
        point_count += 1
        users = list(point.user_samples.all())
        users_by_id = {row.sub2api_user_id: row for row in users}
        observations = list(point.observations.all())
        if len(observations) > 1:
            _issue(
                issues,
                "multiple_observations",
                "同一采样点关联了多条百分比观测",
                point,
            )
        observation = observations[0] if observations else None

        if point.window_started_at is not None and point.window_ended_at is not None:
            if point.window_started_at > point.window_ended_at:
                _issue(issues, "invalid_window", "查询窗口起点晚于终点", point)
            if point.window_ended_at != point.observed_at:
                _issue(
                    issues,
                    "window_observed_mismatch",
                    "查询窗口终点与采样时刻不一致",
                    point,
                )
        elif point.write_status == "complete":
            _issue(issues, "missing_window", "完整采样点缺少查询窗口", point)
        else:
            _issue(
                issues,
                "legacy_window_unknown",
                "旧采样点缺少可证明的查询窗口",
                point,
                severity="warning",
            )

        if point.write_status == "complete":
            if point.capture_started_at is None or point.capture_finished_at is None:
                _issue(
                    issues,
                    "missing_capture_bounds",
                    "完整采样点缺少 capture bounds",
                    point,
                )
            elif not (
                point.capture_started_at
                <= point.observed_at
                <= point.capture_finished_at
            ):
                _issue(
                    issues,
                    "invalid_capture_bounds",
                    "采样时刻不在 capture bounds 内",
                    point,
                )
            expected_ids = [row.sub2api_user_id for row in users]
            if point.expected_user_count != len(expected_ids):
                _issue(
                    issues,
                    "expected_user_count_mismatch",
                    "完整采样点的用户数量不匹配",
                    point,
                    dimension="user_cost",
                )
            if point.expected_user_digest != expected_user_digest(expected_ids):
                _issue(
                    issues,
                    "expected_user_digest_mismatch",
                    "完整采样点的用户集合摘要不匹配",
                    point,
                    dimension="user_cost",
                )
        else:
            _issue(
                issues,
                "legacy_completeness_unknown",
                "旧采样点没有 expected-user 原子写入证据",
                point,
                severity="warning",
                dimension="user_cost",
            )

        _nonnegative(
            issues,
            point,
            (
                ("account_standard_cost", point.account_standard_cost),
                ("account_actual_cost", point.account_actual_cost),
                ("interval_standard_cost", point.interval_standard_cost),
                ("interval_actual_cost", point.interval_actual_cost),
                ("residual_standard_cost", point.residual_standard_cost),
                ("residual_actual_cost", point.residual_actual_cost),
            ),
            "account_cost",
        )
        for user in users:
            _nonnegative(
                issues,
                point,
                (
                    ("user.total_standard_cost", user.total_standard_cost),
                    ("user.total_actual_cost", user.total_actual_cost),
                    ("user.interval_standard_cost", user.interval_standard_cost),
                    ("user.interval_actual_cost", user.interval_actual_cost),
                ),
                "user_cost",
            )
            if user.account_id != point.account_id or user.observed_at != point.observed_at:
                _issue(
                    issues,
                    "user_point_key_mismatch",
                    "用户成本行与采样点 natural key 不一致",
                    point,
                    dimension="user_cost",
                )
            if (
                point.window_started_at is not None
                and user.window_started_at != point.window_started_at
            ) or (
                point.window_ended_at is not None
                and user.window_ended_at != point.window_ended_at
            ) or (
                point.window_resets_at is not None
                and user.window_resets_at != point.window_resets_at
            ):
                _issue(
                    issues,
                    "user_window_mismatch",
                    "用户成本窗口与账号窗口不一致",
                    point,
                    dimension="user_cost",
                )
            previous_user = previous_users.get(user.sub2api_user_id)
            if (
                previous_user is not None
                and user.window_resets_at == previous_user.window_resets_at
            ):
                if user.interval_started_at != previous_user.observed_at:
                    _issue(
                        issues,
                        "user_interval_discontinuity",
                        "同一官方窗口的用户区间不连续",
                        point,
                        dimension="user_cost",
                    )
                for basis in ("standard", "actual"):
                    expected_interval = (
                        getattr(user, f"total_{basis}_cost")
                        - getattr(previous_user, f"total_{basis}_cost")
                    )
                    if _different(
                        getattr(user, f"interval_{basis}_cost"),
                        expected_interval,
                    ):
                        _issue(
                            issues,
                            "user_interval_total_mismatch",
                            f"用户 {basis} 区间不等于相邻累计量差值",
                            point,
                            dimension="user_cost",
                        )

        for basis in ("standard", "actual"):
            account_total = getattr(point, f"account_{basis}_cost")
            residual = getattr(point, f"residual_{basis}_cost")
            user_total = sum(
                (getattr(row, f"total_{basis}_cost") for row in users),
                ZERO,
            )
            if account_total is None:
                if point.write_status == "complete":
                    _issue(
                        issues,
                        "missing_account_total",
                        f"完整采样点缺少 {basis} 账号总量",
                        point,
                        dimension="account_cost",
                    )
                continue
            if residual is None:
                _issue(
                    issues,
                    "missing_residual",
                    f"账号与用户 {basis} 合计缺少显式 residual",
                    point,
                    severity=("hard" if point.write_status == "complete" else "warning"),
                    dimension="user_cost",
                )
            elif _different(account_total, user_total + residual):
                _issue(
                    issues,
                    "account_user_residual_mismatch",
                    f"账号总量不等于用户合计加 {basis} residual",
                    point,
                    dimension="user_cost",
                )
            account_interval = getattr(point, f"interval_{basis}_cost")
            user_intervals = [
                getattr(row, f"interval_{basis}_cost") for row in users
            ]
            if point.write_status == "complete" and (
                account_interval is None
                or any(value is None for value in user_intervals)
            ):
                _issue(
                    issues,
                    "missing_interval_total",
                    f"完整采样点缺少 {basis} 区间成本",
                    point,
                    dimension="user_cost",
                )
            elif account_interval is not None and all(
                value is not None for value in user_intervals
            ):
                previous_residual = (
                    getattr(previous, f"residual_{basis}_cost")
                    if previous is not None
                    and point.window_resets_at == previous.window_resets_at
                    else ZERO
                )
                if residual is not None and previous_residual is not None:
                    interval_residual = residual - previous_residual
                    if _different(
                        account_interval,
                        sum(user_intervals, ZERO) + interval_residual,
                    ):
                        _issue(
                            issues,
                            "interval_user_residual_mismatch",
                            f"账号区间不等于用户区间合计加 {basis} residual 变化",
                            point,
                            dimension="user_cost",
                        )

        if previous is not None and point.window_resets_at == previous.window_resets_at:
            if point.interval_started_at != previous.observed_at:
                _issue(
                    issues,
                    "interval_discontinuity",
                    "同一官方窗口的账号区间不连续",
                    point,
                    dimension="account_cost",
                )

        if observation is not None:
            if observation.account_id != point.account_id or observation.observed_at != point.observed_at:
                _issue(
                    issues,
                    "observation_point_key_mismatch",
                    "百分比观测与采样点 natural key 不一致",
                    point,
                )
            pairs = (
                (observation.total_standard_cost, point.account_standard_cost),
                (observation.total_actual_cost, point.account_actual_cost),
                (observation.interval_standard_cost, point.interval_standard_cost),
                (observation.interval_actual_cost, point.interval_actual_cost),
            )
            if any(_different(left, right) for left, right in pairs):
                _issue(
                    issues,
                    "observation_point_cost_mismatch",
                    "百分比观测与 canonical point 成本不一致",
                    point,
                    dimension="account_cost",
                )
            if (
                observation.upstream_resets_at != point.window_resets_at
                or observation.cost_window_started_at != point.window_started_at
                or observation.cost_window_ended_at != point.window_ended_at
                or observation.interval_cost_started_at
                != point.interval_started_at
            ):
                _issue(
                    issues,
                    "observation_point_window_mismatch",
                    "百分比观测与 canonical point 的官方窗口或区间坐标不一致",
                    point,
                    dimension="account_cost",
                )
            details = list(observation.fast_corrections.all())
            _nonnegative(
                issues,
                point,
                (
                    (
                        "fast_correction_standard_cost",
                        observation.fast_correction_standard_cost,
                    ),
                    (
                        "fast_correction_actual_cost",
                        observation.fast_correction_actual_cost,
                    ),
                ),
                "fast_cost",
            )
            for detail in details:
                _nonnegative(
                    issues,
                    point,
                    (
                        (
                            "fast_detail.fast_standard_cost",
                            detail.fast_standard_cost,
                        ),
                        (
                            "fast_detail.fast_actual_cost",
                            detail.fast_actual_cost,
                        ),
                        (
                            "fast_detail.standard_correction_cost",
                            detail.standard_correction_cost,
                        ),
                        (
                            "fast_detail.actual_correction_cost",
                            detail.actual_correction_cost,
                        ),
                    ),
                    "fast_cost",
                )
                if (
                    detail.request_count is not None
                    and detail.fast_request_count > detail.request_count
                ):
                    _issue(
                        issues,
                        "fast_detail_count_invalid",
                        "逐用户 FAST 请求数大于该用户全部请求数",
                        point,
                        dimension="request_count",
                    )
            standard = sum(
                (row.standard_correction_cost for row in details),
                ZERO,
            )
            actual = sum(
                (row.actual_correction_cost for row in details),
                ZERO,
            )
            if (
                details
                or observation.fast_correction_standard_cost is not None
                or observation.fast_correction_actual_cost is not None
            ) and (
                _different(
                    observation.fast_correction_standard_cost,
                    standard,
                )
                or _different(
                    observation.fast_correction_actual_cost,
                    actual,
                )
            ):
                _issue(
                    issues,
                    "fast_parent_cost_mismatch",
                    "FAST 父级修正与逐用户明细合计不一致",
                    point,
                    dimension="fast_cost",
                )
            if observation.fast_correction_request_count is not None and all(
                row.request_count is not None for row in details
            ):
                request_count = sum(
                    (row.request_count or 0 for row in details),
                    0,
                )
                if request_count != observation.fast_correction_request_count:
                    _issue(
                        issues,
                        "fast_parent_count_mismatch",
                        "FAST 父级请求数与逐用户明细合计不一致",
                        point,
                        dimension="request_count",
                    )
        previous = point
        previous_users.update(users_by_id)

    orphan_observations = Observation.objects.filter(
        account_id=account_id,
        sample_point__isnull=True,
    ).count()
    orphan_users = Sub2APIUserUsageSample.objects.filter(
        account_id=account_id,
        sample_point__isnull=True,
    ).count()
    orphan_participants = ParticipantUsageSample.objects.filter(
        account_id=account_id,
        sample_point__isnull=True,
    ).count()
    if orphan_observations or orphan_users or orphan_participants:
        _issue(
            issues,
            "orphan_source_rows",
            "存在未归入 canonical point 的历史源事实",
            None,
        )

    return AuditResult(
        account_id=account_id,
        point_count=point_count,
        issues=tuple(issues),
    )
