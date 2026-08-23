"""System-user identity and page/data-scope permission management."""

from django.contrib.auth import get_user_model
from rest_framework import status

from .base import AdminAPIView, PageAccessAPIView, error, ok
from ..access import (
    page_permissions_for,
    visible_account_ids,
    visible_participant_ids,
)
from ..models import PagePermission
from ..reporting import iso
from ..serializers import (
    SystemUserPermissionSerializer,
    SystemUserWriteSerializer,
)


User = get_user_model()


def system_user_data(
    user,
    allowed_participant_ids: set[int] | None = None,
    allowed_account_ids: set[int] | None = None,
) -> dict:
    participants = sorted(user.quota_participants.all(), key=lambda item: item.id)
    if allowed_participant_ids is not None:
        participants = [
            item for item in participants if item.id in allowed_participant_ids
        ]
    accounts = sorted(
        user.visible_monitored_accounts.all(),
        key=lambda item: (item.name, item.external_account_id),
    )
    if allowed_account_ids is not None:
        accounts = [item for item in accounts if item.id in allowed_account_ids]
    return {
        "id": user.id,
        "username": user.get_username(),
        "email": user.email,
        "is_active": user.is_active,
        "page_permissions": page_permissions_for(user),
        "participant_ids": [item.id for item in participants],
        "participant_names": [item.name for item in participants],
        "account_ids": [item.id for item in accounts],
        "account_names": [item.name for item in accounts],
        "last_login": iso(user.last_login),
        "date_joined": iso(user.date_joined),
    }


class SystemUserListView(PageAccessAPIView):
    required_page_permissions = (PagePermission.SYSTEM_USERS,)

    def get(self, request):
        users = (
            User.objects.filter(is_staff=False, is_superuser=False)
            .prefetch_related(
                "quota_participants",
                "page_accesses",
                "visible_monitored_accounts",
            )
            .order_by("username")
        )
        allowed_ids = visible_participant_ids(request.user)
        allowed_account_ids = visible_account_ids(request.user)
        return ok(
            [
                system_user_data(
                    user,
                    allowed_participant_ids=allowed_ids,
                    allowed_account_ids=allowed_account_ids,
                )
                for user in users
            ]
        )

    def post(self, request):
        serializer = SystemUserWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error("系统用户校验失败", details=serializer.errors)
        user = serializer.save()
        return ok(system_user_data(user), status.HTTP_201_CREATED)


class SystemUserDetailView(AdminAPIView):
    def _get_user(self, user_id: int):
        return (
            User.objects.filter(
                pk=user_id,
                is_staff=False,
                is_superuser=False,
            )
            .prefetch_related(
                "quota_participants",
                "page_accesses",
                "visible_monitored_accounts",
            )
            .first()
        )

    def patch(self, request, user_id: int):
        user = self._get_user(user_id)
        if user is None:
            return error("系统用户不存在", status.HTTP_404_NOT_FOUND)
        serializer = SystemUserWriteSerializer(
            user,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error("系统用户校验失败", details=serializer.errors)
        return ok(system_user_data(serializer.save()))

    def delete(self, _request, user_id: int):
        user = self._get_user(user_id)
        if user is None:
            return error("系统用户不存在", status.HTTP_404_NOT_FOUND)
        user.delete()
        return ok({"deleted": True})


class SystemUserPermissionView(AdminAPIView):
    def patch(self, request, user_id: int):
        user = (
            User.objects.filter(
                pk=user_id,
                is_staff=False,
                is_superuser=False,
            )
            .prefetch_related(
                "quota_participants",
                "page_accesses",
                "visible_monitored_accounts",
            )
            .first()
        )
        if user is None:
            return error("系统用户不存在", status.HTTP_404_NOT_FOUND)
        serializer = SystemUserPermissionSerializer(
            user,
            data=request.data,
        )
        if not serializer.is_valid():
            return error("系统用户权限校验失败", details=serializer.errors)
        return ok(system_user_data(serializer.save()))
