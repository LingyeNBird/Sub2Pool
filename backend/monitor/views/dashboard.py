"""首页额度总览 API。"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404
from django.db import DatabaseError, transaction

from django.utils import timezone

from .base import AdminAPIView, error, ok
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    display_cycle_rates,
    display_recommendation,
    iso,
    participant_data,
)
from ..history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from ..models import (
    AppSettings,
    HistoryMaintenanceState,
    Observation,
    ParticipantBalanceSample,
    ParticipantBalanceOperation,
    Participant,
    ParticipantSnapshot,
)
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..replay import RATE_METHOD


def _admin_url(value: str) -> str:
    """只把已配置地址的来源暴露给浏览器，不附加任何管理后台路径。"""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = (
        f"{hostname}:{parsed.port}"
        if parsed.port is not None
        else hostname
    )
    return f"{parsed.scheme}://{authority}"


class DashboardView(AdminAPIView):
    def get(self, _request):
        config = AppSettings.load()
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            config.openai_account_id,
        )
        snapshot_stale = bool(
            config.last_upstream_check_at
            and timezone.now() - config.last_upstream_check_at
            >= timedelta(hours=config.stale_warning_hours)
        )
        observation = (
            Observation.objects.filter(
                account_id=config.openai_account_id,
                excluded_at__isnull=True,
                attribution_started_at__isnull=False,
            )
            .prefetch_related("participant_snapshots__participant")
            .order_by("-observed_at", "-id")
            .first()
            if config.openai_account_id
            else None
        )
        total_charged = Decimal("0")
        display_rate, raw_rate = (
            display_cycle_rates(observation, config)
            if observation
            else (None, None)
        )
        participant_rows = [
            participant_data(item, config)
            for item in Participant.objects.filter(enabled=True)
        ]
        total_charged = sum(
            (
                Decimal(str(item["snapshot"]["charged_cycle_percent"]))
                for item in participant_rows
                if item["snapshot"]
            ),
            Decimal("0"),
        )
        unattributed_used_percent = Decimal("0")
        presented_estimated_percent = (
            observation.interval_used_percent
            if (
                observation is not None
                and config.weekly_quota_model == "constant_average"
            )
            else (
                observation.estimated_used_percent
                if observation is not None
                else Decimal("0")
            )
        )
        if observation is not None:
            residual_attribution = observation.model_diagnostics.get(
                "residual_attributed_percent"
            )
            if (
                config.weekly_quota_model == "time_varying"
                and residual_attribution is not None
            ):
                unattributed_used_percent = Decimal(
                    str(residual_attribution)
                )
            else:
                unattributed_used_percent = max(
                    Decimal("0"),
                    presented_estimated_percent - total_charged,
                )
        data = {
            "configured": bool(
                config.sub2api_admin_token_encrypted and config.openai_account_id
            ),
            "monitoring_enabled": config.monitoring_enabled,
            "last_local_check_at": iso(config.last_local_check_at),
            "last_upstream_check_at": iso(config.last_upstream_check_at),
            "snapshot_stale": snapshot_stale,
            "last_success_at": iso(config.last_success_at),
            "last_error": config.last_error,
            "quota_query_mode": config.quota_query_mode,
            "sub2api_admin_url": _admin_url(config.sub2api_base_url),
            "fast_correction_enabled": config.fast_correction_enabled,
            "weekly_quota_model": config.weekly_quota_model,
            "cycle": None,
            "participants": [
                item
                for item in participant_rows
                if item["snapshot"]
                and item["snapshot"]["needs_manual_update"]
                and not item["snapshot"]["recommendation_applied"]
            ],
            "needs_manual_update_count": sum(
                1
                for item in participant_rows
                if item["snapshot"]
                and item["snapshot"]["needs_manual_update"]
            ),
        }
        if observation:
            data["cycle"] = {
                "id": observation.id,
                "observed_at": iso(observation.observed_at),
                "starts_at": iso(observation.attribution_started_at),
                "resets_at": iso(observation.upstream_resets_at),
                "upstream_used_percent": (
                    float(observation.upstream_used_percent) if observation else None
                ),
                "interval_used_percent": float(
                    observation.interval_used_percent
                ),
                "effective_usd_per_percent": (
                    float(display_rate) if display_rate is not None else None
                ),
                "selected_total_cost": (
                    float(observation.selected_total_cost) if observation else None
                ),
                "selected_total_cost_breakdown": (
                    cost_breakdowns.for_observation(observation)
                ),
                "start_cost_breakdown": cost_breakdowns.zero(),
                "unattributed_used_percent": float(
                    unattributed_used_percent
                ),
                "sample_note": observation.sample_note if observation else "",
                "snapshot_sampled_at": (
                    observation.raw_window.get("sampled_at") if observation else None
                ),
                "rate_calculated": (
                    raw_rate is not None
                    if config.weekly_quota_model == "constant_average"
                    else bool(observation.model_diagnostics)
                ),
                "estimated_used_percent": float(
                    presented_estimated_percent
                ),
                "capacity_lower_usd": (
                    float(observation.capacity_lower_usd)
                    if observation.capacity_lower_usd is not None
                    else None
                ),
                "capacity_upper_usd": (
                    float(observation.capacity_upper_usd)
                    if observation.capacity_upper_usd is not None
                    else None
                ),
                "model_diagnostics": observation.model_diagnostics,
            }
        return ok(data)


class BalanceOperationConflict(RuntimeError):
    pass


def _prepare_balance_operation(
    *,
    account_id: int,
    participant_id: int,
    config: AppSettings,
    guard: LeaseGuard,
) -> tuple[ParticipantBalanceOperation, bool]:
    with transaction.atomic():
        state = HistoryMaintenanceState.objects.select_for_update().get(
            account_id=account_id
        )
        guard.assert_owned(state)
        config.refresh_from_db()
        if config.openai_account_id != account_id:
            raise BalanceOperationConflict(
                "上游账号设置已变化，请刷新后重试"
            )
        pending = (
            ParticipantBalanceOperation.objects.select_for_update()
            .exclude(state="committed")
            .filter(account_id=account_id)
            .order_by("created_at", "id")
            .first()
        )
        if pending is not None:
            if pending.participant_id != participant_id:
                raise BalanceOperationConflict(
                    "另一个参与者存在待对账余额操作，请先重试对应建议"
                )
            if state.fact_revision != pending.base_revision:
                raise BalanceOperationConflict(
                    "待对账余额操作的源事实 revision 已变化，已阻止自动提交"
                )
            return pending, False

        participant = Participant.objects.select_for_update().get(
            pk=participant_id,
            enabled=True,
        )
        snapshot, recommended = display_recommendation(participant, config)
        if snapshot is None or recommended is None:
            raise BalanceOperationConflict("该参与者尚无可应用的额度建议")
        if snapshot.recommendation_applied:
            raise BalanceOperationConflict("该条额度建议已经应用")
        if recommended <= 0:
            raise BalanceOperationConflict(
                "Sub2API 原生余额调整接口不允许把余额设为 0，请前往管理后台手动处理"
            )
        operation = ParticipantBalanceOperation.objects.create(
            account_id=account_id,
            base_revision=state.fact_revision,
            participant=participant,
            snapshot=snapshot,
            sub2api_user_id=participant.sub2api_user_id,
            requested_balance_usd=recommended,
        )
        return operation, True


def _record_balance_attempt(
    operation_id,
    guard: LeaseGuard,
) -> ParticipantBalanceOperation:
    with transaction.atomic():
        state = HistoryMaintenanceState.objects.select_for_update().get(
            account_id=guard.account_id
        )
        guard.assert_owned(state)
        operation = ParticipantBalanceOperation.objects.select_for_update().get(
            pk=operation_id
        )
        operation.attempt_count += 1
        operation.last_error = ""
        operation.save(update_fields=["attempt_count", "last_error", "updated_at"])
        return operation


def _mark_balance_reconciliation_required(
    operation_id,
    guard: LeaseGuard,
    message: str,
) -> None:
    with transaction.atomic():
        state = HistoryMaintenanceState.objects.select_for_update().get(
            account_id=guard.account_id
        )
        guard.assert_owned(state)
        operation = ParticipantBalanceOperation.objects.select_for_update().get(
            pk=operation_id
        )
        operation.state = "reconciliation_required"
        operation.confirmed_balance_usd = None
        operation.remote_confirmed_at = None
        operation.last_error = message
        operation.save(
            update_fields=[
                "state",
                "confirmed_balance_usd",
                "remote_confirmed_at",
                "last_error",
                "updated_at",
            ]
        )


def _mark_balance_remote_confirmed(
    operation_id,
    guard: LeaseGuard,
    confirmed: Decimal,
) -> None:
    with transaction.atomic():
        state = HistoryMaintenanceState.objects.select_for_update().get(
            account_id=guard.account_id
        )
        guard.assert_owned(state)
        operation = ParticipantBalanceOperation.objects.select_for_update().get(
            pk=operation_id
        )
        operation.state = "remote_confirmed"
        operation.confirmed_balance_usd = confirmed
        operation.remote_confirmed_at = timezone.now()
        operation.last_error = ""
        operation.save(
            update_fields=[
                "state",
                "confirmed_balance_usd",
                "remote_confirmed_at",
                "last_error",
                "updated_at",
            ]
        )


def _commit_balance_operation(
    operation_id,
    guard: LeaseGuard,
) -> ParticipantBalanceOperation:
    with transaction.atomic():
        state = HistoryMaintenanceState.objects.select_for_update().get(
            account_id=guard.account_id
        )
        guard.assert_owned(state)
        operation = (
            ParticipantBalanceOperation.objects.select_for_update()
            .select_related("snapshot__observation", "participant")
            .get(pk=operation_id)
        )
        if operation.state == "committed":
            return operation
        if operation.state != "remote_confirmed":
            raise BalanceOperationConflict(
                "上游余额尚未确认，不能提交本地余额事实"
            )
        if state.fact_revision != operation.base_revision:
            raise BalanceOperationConflict(
                "余额操作创建后的源事实 revision 已变化，已阻止本地提交"
            )
        confirmed = operation.confirmed_balance_usd
        if confirmed is None:
            raise BalanceOperationConflict("上游确认余额事实缺失")
        participant = Participant.objects.select_for_update().get(
            pk=operation.participant_id,
            enabled=True,
            sub2api_user_id=operation.sub2api_user_id,
        )
        snapshot = ParticipantSnapshot.objects.select_for_update().get(
            pk=operation.snapshot_id,
            participant=participant,
        )
        snapshot.current_balance_usd = confirmed
        snapshot.balance_difference_usd = Decimal("0")
        snapshot.needs_manual_update = False
        snapshot.recommendation_applied = True
        snapshot.reason = "已一键应用建议余额"
        snapshot.save(
            update_fields=[
                "current_balance_usd",
                "balance_difference_usd",
                "needs_manual_update",
                "recommendation_applied",
                "reason",
            ]
        )
        now = timezone.now()
        point_id = snapshot.observation.sample_point_id
        if point_id is not None:
            ParticipantBalanceSample.objects.update_or_create(
                point_id=point_id,
                participant=participant,
                provenance="admin_recommendation",
                defaults={
                    "balance_usd": confirmed,
                    "captured_at": now,
                },
            )
        participant.latest_balance_usd = confirmed
        participant.last_checked_at = now
        participant.updated_at = now
        participant.save(
            update_fields=[
                "latest_balance_usd",
                "last_checked_at",
                "updated_at",
            ]
        )
        state.fact_revision += 1
        state.save(update_fields=["fact_revision", "updated_at"])
        operation.state = "committed"
        operation.committed_at = now
        operation.last_error = ""
        operation.save(
            update_fields=[
                "state",
                "committed_at",
                "last_error",
                "updated_at",
            ]
        )
        return operation


class ApplyParticipantRecommendationView(AdminAPIView):
    """Apply one recommendation through a durable, idempotent operation."""

    def post(self, _request, participant_id: int):
        participant = get_object_or_404(
            Participant,
            pk=participant_id,
            enabled=True,
        )
        config = AppSettings.load()
        account_id = config.openai_account_id
        if not account_id:
            return error("尚未配置 OpenAI 上游账号", 409)
        try:
            guard = LeaseGuard.acquire(
                account_id,
                ttl=timedelta(minutes=15),
                allow_pending_balance=True,
            )
        except LeaseBusyError as exc:
            return error(str(exc), 409)

        operation = None
        try:
            guard.renew()
            operation, created = _prepare_balance_operation(
                account_id=account_id,
                participant_id=participant.id,
                config=config,
                guard=guard,
            )
            if operation.state != "remote_confirmed":
                operation = _record_balance_attempt(operation.id, guard)
                try:
                    with Sub2APIClient(config) as client:
                        confirmed = None
                        if not created:
                            guard.renew()
                            remote = client.user_balance(
                                operation.sub2api_user_id
                            )
                            if (
                                remote.balance
                                == operation.requested_balance_usd
                            ):
                                confirmed = remote.balance
                        if confirmed is None:
                            guard.renew()
                            confirmed = (
                                client.set_user_balance_from_recommendation(
                                    operation.sub2api_user_id,
                                    operation.requested_balance_usd,
                                )
                            )
                except Sub2APIError as exc:
                    try:
                        _mark_balance_reconciliation_required(
                            operation.id,
                            guard,
                            str(exc),
                        )
                    except DatabaseError:
                        return error(
                            "上游结果不确定，且本地对账状态暂时无法更新；请重试该额度建议",
                            503,
                            {"operation_id": str(operation.id)},
                        )
                    return error(
                        str(exc),
                        502,
                        {
                            "operation_id": str(operation.id),
                            "reconciliation_required": True,
                        },
                    )
                try:
                    _mark_balance_remote_confirmed(
                        operation.id,
                        guard,
                        Decimal(str(confirmed)),
                    )
                    operation = _commit_balance_operation(
                        operation.id,
                        guard,
                    )
                except DatabaseError as exc:
                    ParticipantBalanceOperation.objects.filter(
                        pk=operation.id
                    ).update(last_error=str(exc))
                    return error(
                        "上游余额已确认，本地提交待恢复；重试同一建议将幂等完成",
                        503,
                        {
                            "operation_id": str(operation.id),
                            "retryable": True,
                        },
                    )
            else:
                try:
                    operation = _commit_balance_operation(
                        operation.id,
                        guard,
                    )
                except DatabaseError as exc:
                    ParticipantBalanceOperation.objects.filter(
                        pk=operation.id
                    ).update(last_error=str(exc))
                    return error(
                        "上游余额已确认，本地提交待恢复；重试同一建议将幂等完成",
                        503,
                        {
                            "operation_id": str(operation.id),
                            "retryable": True,
                        },
                    )
            confirmed = operation.confirmed_balance_usd
            return ok(
                {
                    "operation_id": str(operation.id),
                    "participant_id": operation.participant_id,
                    "sub2api_user_id": operation.sub2api_user_id,
                    "applied_balance_usd": float(confirmed),
                }
            )
        except (LeaseLostError, BalanceOperationConflict) as exc:
            return error(str(exc), 409)
        finally:
            guard.release()
