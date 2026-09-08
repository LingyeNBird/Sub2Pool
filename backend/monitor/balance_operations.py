"""Shared, fenced recommendation writes for manual and scheduled application."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import DatabaseError, transaction
from django.utils import timezone

from .history_state import LeaseBusyError, LeaseGuard, LeaseLostError
from .integrations.sub2api import Sub2APIClient, Sub2APIError
from .models import (
    AppSettings, HistoryMaintenanceState, MonitoredAccount, Participant,
    ParticipantBalanceOperation, ParticipantBalanceOperationSource,
    ParticipantBalanceSample, ParticipantSnapshot,
)
from .reporting import aggregate_recommendation

logger = logging.getLogger(__name__)


class BalanceApplicationError(RuntimeError):
    def __init__(self, message: str, status_code: int, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


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
        if recommended < 0:
            raise BalanceOperationConflict("聚合额度建议不能为负数")
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
        source_by_account = {
            int(source["external_account_id"]): source
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
                    contribution_usd=Decimal(
                        str(source_by_account[account_id]["contribution_usd"])
                    ),
                    share_percent=Decimal(
                        str(
                            source_by_account[account_id][
                                "contract_share_percent"
                            ]
                        )
                    ),
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
            snapshot.reason = "已应用聚合建议余额"
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


def apply_participant_recommendation(participant_id: int) -> ParticipantBalanceOperation:
    participant = Participant.objects.get(pk=participant_id, enabled=True)
    pending = (
        ParticipantBalanceOperation.objects.exclude(state="committed")
        .filter(participant=participant)
        .prefetch_related("sources")
        .order_by("created_at", "id")
        .first()
    )
    account_ids = set(
        MonitoredAccount.objects.filter(
            enabled=True,
            pool__allocations__participant=participant,
            pool__allocations__share_percent__gt=0,
        )
        .order_by()
        .values_list("external_account_id", flat=True)
        .distinct()
    )
    if pending is not None:
        account_ids.update(
            pending.sources.values_list("account_external_id", flat=True)
        )
    if not account_ids:
        raise BalanceApplicationError("该参与者尚未加入启用的监控账号", 409)
    guards = _acquire_guards(account_ids)

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
                    raise BalanceApplicationError(
                        "上游结果不确定，且本地对账状态暂时无法更新；请重试该额度建议",
                        503,
                        {"operation_id": str(operation.id)},
                    )
                raise BalanceApplicationError(
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
            raise BalanceApplicationError(
                "上游余额已确认，本地提交待恢复；重试同一建议将幂等完成",
                503,
                {"operation_id": str(operation.id), "retryable": True},
            )
        return operation

    except (LeaseLostError, BalanceOperationConflict) as exc:
        raise BalanceApplicationError(str(exc), 409)
    finally:
        for guard in reversed(tuple(guards.values())):
            guard.release()


def auto_apply_recommendations() -> dict:
    """Apply only actionable aggregate recommendations once per sampling cycle."""
    result = {"applied": 0, "failed": 0}
    config = AppSettings.load()
    if not config.auto_apply_recommendations or not config.monitoring_enabled:
        return result
    for participant in Participant.objects.filter(enabled=True):
        aggregate, _ = aggregate_recommendation(participant, config)
        if aggregate is None or not aggregate["needs_manual_update"]:
            continue
        try:
            apply_participant_recommendation(participant.id)
            result["applied"] += 1
        except LeaseBusyError:
            continue
        except BalanceApplicationError:
            result["failed"] += 1
            logger.exception("自动应用建议额度失败：participant_id=%s", participant.id)
    return result
