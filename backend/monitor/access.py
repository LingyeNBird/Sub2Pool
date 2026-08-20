"""Central page and participant visibility rules."""

from collections.abc import Iterable

from django.db.models import QuerySet
from rest_framework.permissions import BasePermission

from .models import PagePermission, Participant, SystemUserPageAccess


ALL_PAGE_PERMISSIONS = tuple(PagePermission.values)


def page_permissions_for(user) -> list[str]:
    if user.is_staff:
        return list(ALL_PAGE_PERMISSIONS)
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
    return [page_code for page_code in ALL_PAGE_PERMISSIONS if page_code in granted]


def has_page_permission(user, page_codes: str | Iterable[str]) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if isinstance(page_codes, str):
        page_codes = (page_codes,)
    return SystemUserPageAccess.objects.filter(
        user=user,
        page_code__in=tuple(page_codes),
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


class HasPageAccess(BasePermission):
    """Allow staff or a non-staff user holding any page code declared by a view."""

    message = "当前账号没有访问该页面的权限"

    def has_permission(self, request, view) -> bool:
        required = getattr(view, "required_page_permissions", ())
        return bool(required) and has_page_permission(request.user, required)
