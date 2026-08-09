"""管理员维护普通系统用户及其可见参与者范围。"""

from django.contrib.auth import get_user_model
from rest_framework import status

from .base import AdminAPIView, error, ok
from ..reporting import iso
from ..serializers import SystemUserWriteSerializer


User = get_user_model()


def system_user_data(user) -> dict:
    participants = list(user.quota_participants.order_by("-is_owner", "id"))
    return {
        "id": user.id,
        "username": user.get_username(),
        "email": user.email,
        "is_active": user.is_active,
        "participant_ids": [item.id for item in participants],
        "participant_names": [item.name for item in participants],
        "last_login": iso(user.last_login),
        "date_joined": iso(user.date_joined),
    }


class SystemUserListView(AdminAPIView):
    def get(self, _request):
        users = (
            User.objects.filter(is_staff=False, is_superuser=False)
            .prefetch_related("quota_participants")
            .order_by("username")
        )
        return ok([system_user_data(user) for user in users])

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
            .prefetch_related("quota_participants")
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
