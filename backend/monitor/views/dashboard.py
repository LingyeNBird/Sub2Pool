"""Dashboard and global participant-balance operations."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .base import AdminAPIView, error, ok
from ..history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import (
    AppSettings,
    HistoryMaintenanceState,
    MonitoredAccount,
    Observation,
    Participant,
    ParticipantBalanceOperation,
    ParticipantBalanceOperationSource,
    ParticipantBalanceSample,
    ParticipantSnapshot,
)
from ..reporting import (
    FastCorrectionBreakdownPresenter,
    aggregate_recommendation,
    display_cycle_rates,
    iso,
    participant_data,
)


def _admin_url(value: str) -> str:
    """Expose only the configured origin, never an administrative path."""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
    return f"{parsed.scheme}://{authority}"


def _account_data(account: MonitoredAccount) -> dict:
    return {
        "id": account.id,
        "external_account_id": account.external_account_id,
        "name": account.name,
        "enabled": account.enabled,
        "quota_query_mode": account.quota_query_mode,
        "last_local_check_at": iso(account.last_local_check_at),
        "last_upstream_check_at": iso(account.last_upstream_check_at),
        "last_success_at": iso(account.last_success_at),
        "next_local_check_at": iso(account.next_local_check_at),
        "last_error": account.last_error,
    }


def _selected_account(request) -> tuple[list[MonitoredAccount], MonitoredAccount | None]:
    accounts = list(MonitoredAccount.objects.order_by("name", "external_account_id"))
    raw_account_id = request.query_params.get("account_id")
    if raw_account_id is None:
        selected = next((item for item in accounts if item.enabled), None)
        return accounts, selected
    try:
        account_id = int(raw_account_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("监控账号参数无效") from exc
    selected = next((item for item in accounts if item.id == account_id), None)
    if selected is None:
        raise ValueError("监控账号不存在")
    return accounts, selected


class DashboardView(AdminAPIView):
    def get(self, request):
        config = AppSettings.load()
        try:
            accounts, account = _selected_account(request)
        except ValueError as exc:
            return error(str(exc), 400)
        external_account_id = account.external_account_id if account else None
        cost_breakdowns = FastCorrectionBreakdownPresenter(
            config,
            external_account_id,
        )
        snapshot_stale = bool(
            account
            and account.last_upstream_check_at
            and timezone.now() - account.last_upstream_check_at
            >= timedelta(hours=config.stale_warning_hours)
        )
        observation = (
            Observation.objects.filter(
                account_id=external_account_id,
                excluded_at__isnull=True,
                attribution_started_at__isnull=False,
            )
            .prefetch_related("participant_snapshots__participant")
            .order_by("-observed_at", "-id")
            .first()
            if external_account_id is not None
            else None
        )
        participant_rows = [
            participant_data(item, config)
            for item in Participant.objects.filter(enabled=True).prefetch_related(
                "account_memberships__account"
            )
        ]
        selected_snapshots = []
        if account is not None:
            for participant in participant_rows:
                breakdown = next(
                    (
                        item
                        for item in participant["account_breakdowns"]
                        if item["account_id"] == account.id
                    ),
                    None,
                )
                if breakdown and breakdown["snapshot"]:
                    selected_snapshots.append(breakdown["snapshot"])
        total_charged = sum(
            (
                Decimal(str(item["charged_cycle_percent"]))
                for item in selected_snapshots
            ),
            Decimal("0"),
        )
        display_rate, raw_rate = (
            display_cycle_rates(observation, config)
            if observation
            else (None, None)
        )
        presented_estimated_percent = (
            observation.interval_used_percent
            if observation is not None
            and config.weekly_quota_model == "constant_average"
            else (
                observation.estimated_used_percent
                if observation is not None
                else Decimal("0")
            )
        )
        unattributed_used_percent = Decimal("0")
        if observation is not None:
            residual = observation.model_diagnostics.get(
                "residual_attributed_percent"
            )
            if config.weekly_quota_model == "time_varying" and residual is not None:
                unattributed_used_percent = Decimal(str(residual))
            else:
                unattributed_used_percent = max(
                    Decimal("0"),
                    presented_estimated_percent - total_charged,
                )

        actionable = [
            item
            for item in participant_rows
            if item["snapshot"]
            and item["snapshot"]["needs_manual_update"]
            and not item["snapshot"]["recommendation_applied"]
        ]
        data = {
            "configured": bool(
                config.sub2api_admin_token_encrypted and accounts
            ),
            "monitoring_enabled": config.monitoring_enabled,
            "accounts": [_account_data(item) for item in accounts],
            "selected_account_id": account.id if account else None,
            "last_local_check_at": iso(
                account.last_local_check_at if account else config.last_local_check_at
            ),
            "last_upstream_check_at": iso(
                account.last_upstream_check_at
                if account
                else config.last_upstream_check_at
            ),
            "snapshot_stale": snapshot_stale,
            "last_success_at": iso(
                account.last_success_at if account else config.last_success_at
            ),
            "last_error": account.last_error if account else config.last_error,
            "quota_query_mode": account.quota_query_mode if account else None,
            "sub2api_admin_url": _admin_url(config.sub2api_base_url),
            "fast_correction_enabled": config.fast_correction_enabled,
            "weekly_quota_model": config.weekly_quota_model,
            "cycle": None,
            "participants": actionable,
            "needs_manual_update_count": len(actionable),
        }
        if observation is not None:
            data["cycle"] = {
                "id": observation.id,
                "observed_at": iso(observation.observed_at),
                "starts_at": iso(observation.attribution_started_at),
                "resets_at": iso(observation.upstream_resets_at),
                "upstream_used_percent": float(observation.upstream_used_percent),
                "interval_used_percent": float(observation.interval_used_percent),
                "effective_usd_per_percent": (
                    float(display_rate) if display_rate is not None else None
                ),
                "selected_total_cost": float(observation.selected_total_cost),
                "selected_total_cost_breakdown": (
                    cost_breakdowns.for_observation(observation)
                ),
                "start_cost_breakdown": cost_breakdowns.zero(),
                "unattributed_used_percent": float(unattributed_used_percent),
                "sample_note": observation.sample_note,
                "snapshot_sampled_at": observation.raw_window.get("sampled_at"),
                "rate_calculated": (
                    raw_rate is not None
                    if config.weekly_quota_model == "constant_average"
                    else bool(observation.model_diagnostics)
                ),
                "estimated_used_percent": float(presented_estimated_percent),
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


def _acquire_guards(account_ids: set[int]) -> dict[int, LeaseGuard]:
    guards: dict[int, LeaseGuard] = {}
    try:
        for account_id in sorted(account_ids):
            guards[account_id] = LeaseGuard.acquire(
                account_id,
                ttl=timedelta(minutes=15),
                allow_pending_balance=True,
            )
        return guards
    except Exception:
        for guard in reversed(tuple(guards.values())):
            guard.release()
        raise


def _locked_states(
    guards: dict[int, LeaseGuard],
) -> dict[int, HistoryMaintenanceState]:
    states = {
        account_id: HistoryMaintenanceState.objects.select_for_update().get(
            account_id=account_id
        )
        for account_id in sorted(guards)
    }
    for account_id, guard in guards.items():
        guard.assert_owned(states[account_id])
    return states


def _validate_operation_sources(
    operation: ParticipantBalanceOperation,
    guards: dict[int, LeaseGuard],
    states: dict[int, HistoryMaintenanceState],
) -> list[ParticipantBalanceOperationSource]:
    sources = list(operation.sources.select_related("snapshot__observation", "account"))
    if not sources:
        raise BalanceOperationConflict("余额操作缺少账号来源")
    for source in sources:
        if source.account_external_id not in guards:
            raise BalanceOperationConflict("余额操作涉及的账号策略已变化，请重试")
        if states[source.account_external_id].fact_revision != source.base_revision:
            raise BalanceOperationConflict(
                "待对账余额操作的源事实 revision 已变化，已阻止自动提交"
            )
    return sources


def _prepare_balance_operation(
    *,
    participant_id: int,
    config: AppSettings,
    guards: dict[int, LeaseGuard],
) -> tuple[ParticipantBalanceOperation, bool]:
    with transaction.atomic():
        states = _locked_states(guards)
        participant = Participant.objects.select_for_update().get(
            pk=participant_id,
            enabled=True,
        )
        pending = (
            ParticipantBalanceOperation.objects.select_for_update()
            .exclude(state="committed")
            .filter(participant=participant)
            .order_by("created_at", "id")
            .first()
        )
        if pending is not None:
            _validate_operation_sources(pending, guards, states)
            return pending, False

        aggregate, snapshots = aggregate_recommendation(participant, config)
        if (
            aggregate is None
            or not aggregate["recommendation_complete"]
            or not aggregate["needs_manual_update"]
            or aggregate["recommended_balance_usd"] is None
        ):
            raise BalanceOperationConflict("该参与者尚无可应用的聚合额度建议")
        recommended = Decimal(str(aggregate["recommended_balance_usd"]))
        if recommended <= 0:
            raise BalanceOperationConflict(
                "Sub2API 原生余额调整接口不允许把余额设为 0，请前往管理后台手动处理"
            )
        accounts = {
            item.external_account_id: item
            for item in MonitoredAccount.objects.select_for_update().filter(
                external_account_id__in=guards,
                enabled=True,
            )
        }
        snapshot_by_account = {
            snapshot.observation.account_id: snapshot for snapshot in snapshots
        }
        contribution_by_account = {
            int(source["external_account_id"]): Decimal(
                str(source["contribution_usd"])
            )
            for source in aggregate["sources"]
            if source["contribution_usd"] is not None
        }
        expected_accounts = set(snapshot_by_account)
        if expected_accounts != set(guards) or expected_accounts != set(accounts):
            raise BalanceOperationConflict("参与者账号策略已变化，请刷新后重试")
        operation = ParticipantBalanceOperation.objects.create(
            participant=participant,
            sub2api_user_id=participant.sub2api_user_id,
            requested_balance_usd=recommended,
        )
        ParticipantBalanceOperationSource.objects.bulk_create(
            [
                ParticipantBalanceOperationSource(
                    operation=operation,
                    account=accounts[account_id],
                    account_external_id=account_id,
                    base_revision=states[account_id].fact_revision,
                    snapshot=snapshot_by_account[account_id],
                    contribution_usd=contribution_by_account[account_id],
                )
                for account_id in sorted(expected_accounts)
            ]
        )
        return operation, True


def _mutate_operation(
    operation_id,
    guards: dict[int, LeaseGuard],
    mutate,
) -> ParticipantBalanceOperation:
    with transaction.atomic():
        states = _locked_states(guards)
        operation = ParticipantBalanceOperation.objects.select_for_update().get(
            pk=operation_id
        )
        _validate_operation_sources(operation, guards, states)
        mutate(operation)
        return operation


def _record_balance_attempt(operation_id, guards):
    def mutate(operation):
        operation.attempt_count += 1
        operation.last_error = ""
        operation.save(update_fields=["attempt_count", "last_error", "updated_at"])

    return _mutate_operation(operation_id, guards, mutate)


def _mark_balance_reconciliation_required(operation_id, guards, message: str) -> None:
    def mutate(operation):
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

    _mutate_operation(operation_id, guards, mutate)


def _mark_balance_remote_confirmed(operation_id, guards, confirmed: Decimal) -> None:
    def mutate(operation):
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

    _mutate_operation(operation_id, guards, mutate)


def _commit_balance_operation(operation_id, guards) -> ParticipantBalanceOperation:
    with transaction.atomic():
        states = _locked_states(guards)
        operation = (
            ParticipantBalanceOperation.objects.select_for_update()
            .select_related("participant")
            .get(pk=operation_id)
        )
        sources = _validate_operation_sources(operation, guards, states)
        if operation.state == "committed":
            return operation
        if operation.state != "remote_confirmed":
            raise BalanceOperationConflict("上游余额尚未确认，不能提交本地余额事实")
        confirmed = operation.confirmed_balance_usd
        if confirmed is None:
            raise BalanceOperationConflict("上游确认余额事实缺失")
        participant = Participant.objects.select_for_update().get(
            pk=operation.participant_id,
            enabled=True,
            sub2api_user_id=operation.sub2api_user_id,
        )
        now = timezone.now()
        snapshot_ids = [source.snapshot_id for source in sources]
        snapshots = {
            snapshot.id: snapshot
            for snapshot in ParticipantSnapshot.objects.select_for_update()
            .select_related("observation")
            .filter(pk__in=snapshot_ids, participant=participant)
        }
        if set(snapshots) != set(snapshot_ids):
            raise BalanceOperationConflict("余额操作的账号快照已变化")
        for source in sources:
            snapshot = snapshots[source.snapshot_id]
            snapshot.current_balance_usd = confirmed
            snapshot.balance_difference_usd = Decimal("0")
            snapshot.needs_manual_update = False
            snapshot.recommendation_applied = True
            snapshot.reason = "已一键应用聚合建议余额"
            snapshot.save(
                update_fields=[
                    "current_balance_usd",
                    "balance_difference_usd",
                    "needs_manual_update",
                    "recommendation_applied",
                    "reason",
                ]
            )
            if snapshot.observation.sample_point_id is not None:
                ParticipantBalanceSample.objects.update_or_create(
                    point_id=snapshot.observation.sample_point_id,
                    participant=participant,
                    provenance="admin_recommendation",
                    defaults={"balance_usd": confirmed, "captured_at": now},
                )
            states[source.account_external_id].fact_revision += 1
            states[source.account_external_id].save(
                update_fields=["fact_revision", "updated_at"]
            )
        participant.latest_balance_usd = confirmed
        participant.last_checked_at = now
        participant.updated_at = now
        participant.save(
            update_fields=["latest_balance_usd", "last_checked_at", "updated_at"]
        )
        operation.state = "committed"
        operation.committed_at = now
        operation.last_error = ""
        operation.save(
            update_fields=["state", "committed_at", "last_error", "updated_at"]
        )
        return operation


class ApplyParticipantRecommendationView(AdminAPIView):
    """Apply one aggregate recommendation through an idempotent journal."""

    def post(self, _request, participant_id: int):
        participant = get_object_or_404(Participant, pk=participant_id, enabled=True)
        pending = (
            ParticipantBalanceOperation.objects.exclude(state="committed")
            .filter(participant=participant)
            .prefetch_related("sources")
            .order_by("created_at", "id")
            .first()
        )
        account_ids = set(
            MonitoredAccount.objects.filter(enabled=True).values_list(
                "external_account_id",
                flat=True,
            )
        )
        if pending is not None:
            account_ids.update(
                pending.sources.values_list("account_external_id", flat=True)
            )
        if not account_ids:
            return error("该参与者尚未加入启用的监控账号", 409)
        try:
            guards = _acquire_guards(account_ids)
        except LeaseBusyError as exc:
            return error(str(exc), 409)

        operation = None
        try:
            for guard in guards.values():
                guard.renew()
            operation, created = _prepare_balance_operation(
                participant_id=participant.id,
                config=AppSettings.load(),
                guards=guards,
            )
            if operation.state != "remote_confirmed":
                operation = _record_balance_attempt(operation.id, guards)
                try:
                    with Sub2APIClient(AppSettings.load()) as client:
                        confirmed = None
                        if not created:
                            for guard in guards.values():
                                guard.renew()
                            remote = client.user_balance(operation.sub2api_user_id)
                            if remote.balance == operation.requested_balance_usd:
                                confirmed = remote.balance
                        if confirmed is None:
                            for guard in guards.values():
                                guard.renew()
                            confirmed = client.set_user_balance_from_recommendation(
                                operation.sub2api_user_id,
                                operation.requested_balance_usd,
                            )
                except Sub2APIError as exc:
                    try:
                        _mark_balance_reconciliation_required(
                            operation.id,
                            guards,
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
                _mark_balance_remote_confirmed(
                    operation.id,
                    guards,
                    Decimal(str(confirmed)),
                )
            try:
                operation = _commit_balance_operation(operation.id, guards)
            except DatabaseError as exc:
                ParticipantBalanceOperation.objects.filter(pk=operation.id).update(
                    last_error=str(exc)
                )
                return error(
                    "上游余额已确认，本地提交待恢复；重试同一建议将幂等完成",
                    503,
                    {"operation_id": str(operation.id), "retryable": True},
                )
            return ok(
                {
                    "operation_id": str(operation.id),
                    "participant_id": operation.participant_id,
                    "sub2api_user_id": operation.sub2api_user_id,
                    "applied_balance_usd": float(operation.confirmed_balance_usd),
                    "account_count": operation.sources.count(),
                }
            )
        except (LeaseLostError, BalanceOperationConflict) as exc:
            return error(str(exc), 409)
        finally:
            for guard in reversed(tuple(guards.values())):
                guard.release()
