"""参与者资源 API。"""

from rest_framework import status

from .base import AdminAPIView, error, ok
from .presenters import participant_data
from ..models import AppSettings, Participant
from ..serializers import ParticipantWriteSerializer
from ..sub2api import Sub2APIClient, Sub2APIError


class Sub2APIUserListView(AdminAPIView):
    def get(self, _request):
        try:
            with Sub2APIClient(AppSettings.load()) as client:
                users = client.list_users()
            # 用户名是展示元数据；读取 Admin 用户列表时顺手刷新本地缓存，
            # 首页此后不需要为了显示名称再次请求 Sub2API。
            usernames = {
                int(item["id"]): str(item.get("username") or "")
                for item in users
                if item.get("username")
            }
            cached = list(
                Participant.objects.filter(
                    sub2api_user_id__in=usernames,
                )
            )
            changed = []
            for participant in cached:
                username = usernames[participant.sub2api_user_id]
                if participant.sub2api_username != username:
                    participant.sub2api_username = username
                    changed.append(participant)
            if changed:
                Participant.objects.bulk_update(changed, ["sub2api_username"])
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), status.HTTP_502_BAD_GATEWAY)
        return ok(users)

class ParticipantListView(AdminAPIView):
    def get(self, _request):
        return ok([participant_data(item) for item in Participant.objects.all()])

    def post(self, request):
        serializer = ParticipantWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error("参与者校验失败", details=serializer.errors)
        participant = serializer.save()
        return ok(participant_data(participant), status.HTTP_201_CREATED)


class ParticipantDetailView(AdminAPIView):
    def _get_participant(self, participant_id: int) -> Participant | None:
        try:
            return Participant.objects.get(pk=participant_id)
        except Participant.DoesNotExist:
            return None

    def put(self, request, participant_id: int):
        participant = self._get_participant(participant_id)
        if participant is None:
            return error("参与者不存在", status.HTTP_404_NOT_FOUND)
        # 保持旧接口兼容：PUT 可以只提交发生变化的字段。
        serializer = ParticipantWriteSerializer(
            participant,
            data=request.data,
            partial=True,
        )
        if not serializer.is_valid():
            return error("参与者校验失败", details=serializer.errors)
        return ok(participant_data(serializer.save()))

    def delete(self, _request, participant_id: int):
        participant = self._get_participant(participant_id)
        if participant is None:
            return error("参与者不存在", status.HTTP_404_NOT_FOUND)
        if participant.snapshots.exists():
            return error(
                "该参与者已有测算账本，不能删除；请改为停用",
                status.HTTP_409_CONFLICT,
            )
        participant.delete()
        return ok({"deleted": True})
