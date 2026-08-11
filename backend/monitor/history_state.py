"""Monotonic fact revisions and fenced maintenance leases."""
from __future__ import annotations
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import HistoryMaintenanceState


class LeaseBusyError(RuntimeError):
    """Another source writer currently owns the account lease."""


class LeaseLostError(RuntimeError):
    """The caller's fence is no longer current and must not commit."""


def _locked_state(account_id: int) -> HistoryMaintenanceState:
    state = (
        HistoryMaintenanceState.objects.select_for_update()
        .filter(account_id=account_id)
        .first()
    )
    if state is not None:
        return state
    try:
        with transaction.atomic():
            return HistoryMaintenanceState.objects.create(account_id=account_id)
    except IntegrityError:
        return HistoryMaintenanceState.objects.select_for_update().get(
            account_id=account_id
        )


def current_fact_revision(account_id: int) -> int:
    state, _created = HistoryMaintenanceState.objects.get_or_create(
        account_id=account_id
    )
    return state.fact_revision


def bump_fact_revision(account_id: int) -> int:
    """Acquire the account fence and increment its source revision."""

    with fenced_fact_write([account_id]):
        pass
    return current_fact_revision(account_id)


@dataclass
class LeaseGuard:
    """A renewable lease whose monotonically increasing token fences old owners."""

    account_id: int
    owner: uuid.UUID
    token: int
    expires_at: object
    ttl: timedelta

    @classmethod
    def acquire(
        cls,
        account_id: int,
        *,
        ttl: timedelta = timedelta(minutes=15),
        allow_pending_balance: bool = False,
    ) -> "LeaseGuard":
        now = timezone.now()
        owner = uuid.uuid4()
        with transaction.atomic():
            state = None
            if account_id == 0:
                state = _locked_state(0)
                if (
                    state.lease_owner is not None
                    and state.lease_expires_at is not None
                    and state.lease_expires_at > now
                ):
                    raise LeaseBusyError(
                        "已有采集或历史维护任务正在运行"
                    )
                active_accounts = list(
                    HistoryMaintenanceState.objects.select_for_update()
                    .filter(
                        lease_owner__isnull=False,
                        lease_expires_at__gt=now,
                    )
                    .exclude(account_id=0)
                    .values_list("account_id", flat=True)
                )
                if active_accounts:
                    raise LeaseBusyError(
                        "已有账号采集或历史维护任务正在运行"
                    )
            else:
                global_state = (
                    HistoryMaintenanceState.objects.select_for_update()
                    .filter(account_id=0)
                    .first()
                )
                if (
                    global_state is not None
                    and global_state.lease_owner is not None
                    and global_state.lease_expires_at is not None
                    and global_state.lease_expires_at > now
                ):
                    raise LeaseBusyError("数据库导入正在运行")

            if not allow_pending_balance:
                from .models import ParticipantBalanceOperation

                pending = ParticipantBalanceOperation.objects.exclude(
                    state="committed"
                )
                if account_id:
                    pending = pending.filter(account_id=account_id)
                if pending.exists():
                    raise LeaseBusyError(
                        "存在待对账的上游余额操作，请先重试对应额度建议"
                    )

            if state is None:
                state = _locked_state(account_id)
            if (
                state.lease_owner is not None
                and state.lease_expires_at is not None
                and state.lease_expires_at > now
            ):
                raise LeaseBusyError("已有采集或历史维护任务正在运行")
            state.fence_token += 1
            state.lease_owner = owner
            state.lease_expires_at = now + ttl
            state.save(
                update_fields=[
                    "fence_token",
                    "lease_owner",
                    "lease_expires_at",
                    "updated_at",
                ]
            )
            return cls(
                account_id=account_id,
                owner=owner,
                token=state.fence_token,
                expires_at=state.lease_expires_at,
                ttl=ttl,
            )

    def renew(self) -> None:
        now = timezone.now()
        expires_at = now + self.ttl
        updated = HistoryMaintenanceState.objects.filter(
            account_id=self.account_id,
            lease_owner=self.owner,
            fence_token=self.token,
            lease_expires_at__gt=now,
        ).update(lease_expires_at=expires_at)
        if updated != 1:
            raise LeaseLostError("维护租约已过期或被新的任务接管")
        self.expires_at = expires_at

    def assert_owned(
        self,
        state: HistoryMaintenanceState | None = None,
    ) -> HistoryMaintenanceState:
        now = timezone.now()
        state = state or HistoryMaintenanceState.objects.get(
            account_id=self.account_id
        )
        if (
            state.lease_owner != self.owner
            or state.fence_token != self.token
            or state.lease_expires_at is None
            or state.lease_expires_at <= now
        ):
            raise LeaseLostError("维护租约已过期或 fencing token 不再有效")
        return state

    def release(self) -> None:
        HistoryMaintenanceState.objects.filter(
            account_id=self.account_id,
            lease_owner=self.owner,
            fence_token=self.token,
        ).update(lease_owner=None, lease_expires_at=None)


@contextmanager
def fenced_fact_write(
    account_ids: Iterable[int],
    *,
    ttl: timedelta = timedelta(minutes=15),
) -> Iterator[dict[int, LeaseGuard]]:
    """Serialize source mutations and bump each account revision atomically."""

    normalized = sorted(
        {
            int(account_id)
            for account_id in account_ids
            if account_id is not None
        }
    )
    guards: dict[int, LeaseGuard] = {}
    try:
        for account_id in normalized:
            guards[account_id] = LeaseGuard.acquire(account_id, ttl=ttl)
        with transaction.atomic():
            states = {
                account_id: HistoryMaintenanceState.objects.select_for_update().get(
                    account_id=account_id
                )
                for account_id in normalized
            }
            for account_id, guard in guards.items():
                guard.assert_owned(states[account_id])
            yield guards
            for account_id, guard in guards.items():
                state = states[account_id]
                guard.assert_owned(state)
                state.fact_revision += 1
                state.save(update_fields=["fact_revision", "updated_at"])
    finally:
        for guard in reversed(tuple(guards.values())):
            guard.release()
