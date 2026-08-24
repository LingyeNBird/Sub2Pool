"""Central page, participant, and account visibility rules."""

from collections.abc import Iterable

from django.db.models import QuerySet
from rest_framework.permissions import BasePermission

from .models import (
    ASSIGNABLE_PAGE_PERMISSIONS,
    MonitoredAccount,
    PagePermission,
    Participant,
    SystemUserPageAccess,
)


ALL_PAGE_PERMISSIONS = tuple(PagePermission.values)


def assigned_page_permissions_for(user) -> list[str]:
    """Return only page grants that an administrator can configure."""
    if user.is_staff:
        return list(ASSIGNABLE_PAGE_PERMISSIONS)
    prefetched = getattr(user, "_prefetched_objects_cache", {}).get(
        "page_accesses"
    )
    if prefetched is None:
        granted = set(
            SystemUserPageAccess.objects.filter(user=user).values_list(
                "page_code",
                flat=True,
            )
        )
    else:
        granted = {access.page_code for access in prefetched}
    return [
        page_code
        for page_code in ASSIGNABLE_PAGE_PERMISSIONS
        if page_code in granted
    ]


def page_permissions_for(user) -> list[str]:
    """Return effective page access, including automatic personal settings."""
    if user.is_staff:
        return list(ALL_PAGE_PERMISSIONS)
    assigned = set(assigned_page_permissions_for(user))
    return [
        page_code
        for page_code in ALL_PAGE_PERMISSIONS
        if page_code == PagePermission.SETTINGS or page_code in assigned
    ]


def has_page_permission(user, page_codes: str | Iterable[str]) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if isinstance(page_codes, str):
        page_codes = (page_codes,)
    else:
        page_codes = tuple(page_codes)
    if PagePermission.SETTINGS in page_codes:
        return True
    return SystemUserPageAccess.objects.filter(
        user=user,
        page_code__in=page_codes,
    ).exists()


def visible_participants_for(user, queryset: QuerySet | None = None) -> QuerySet:
    participants = queryset if queryset is not None else Participant.objects.all()
    if user.is_staff:
        return participants
    return participants.filter(authorized_users=user)


def visible_participant_ids(user) -> set[int] | None:
    if user.is_staff:
        return None
    return set(user.quota_participants.values_list("id", flat=True))


def visible_accounts_for(user, queryset: QuerySet | None = None) -> QuerySet:
    accounts = queryset if queryset is not None else MonitoredAccount.objects.all()
    if user.is_staff:
        return accounts
    return accounts.filter(authorized_users=user)


def visible_account_ids(user) -> set[int] | None:
    if user.is_staff:
        return None
    return set(user.visible_monitored_accounts.values_list("id", flat=True))


def scope_participant_data(
    data: dict,
    allowed_account_ids: set[int] | None,
) -> dict:
    """Remove account-scoped participant details the principal cannot view."""

    if allowed_account_ids is None:
        return data
    scoped = {
        **data,
        "account_breakdowns": [
            item
            for item in data["account_breakdowns"]
            if item["account_id"] in allowed_account_ids
        ],
        "pool_allocations": [],
    }
    for allocation in data["pool_allocations"]:
        account_ids = [
            account_id
            for account_id in allocation["account_ids"]
            if account_id in allowed_account_ids
        ]
        if account_ids:
            scoped["pool_allocations"].append(
                {**allocation, "account_ids": account_ids}
            )

    aggregate = data.get("snapshot")
    if aggregate is not None and any(
        source["account_id"] not in allowed_account_ids
        for source in aggregate["sources"]
    ):
        scoped["snapshot"] = None
    return scoped


class HasPageAccess(BasePermission):
    """Allow staff or a non-staff user holding any page code declared by a view."""

    message = "当前账号没有访问该页面的权限"

    def has_permission(self, request, view) -> bool:
        required = getattr(view, "required_page_permissions", ())
        return bool(required) and has_page_permission(request.user, required)
