"""参与者资源 API。"""

from rest_framework import status
from ..api_auth import ReadOnlyAPIKeyAuthentication

from .base import AdminAPIView, PageAccessAPIView, error, ok
from ..access import visible_participants_for
from ..reporting import participant_data
from ..history_state import fenced_fact_write
from ..models import AppSettings, MonitoredAccount, PagePermission, Participant
from ..serializers import ParticipantWriteSerializer
from ..integrations.sub2api import Sub2APIClient, Sub2APIError

class _ParticipantHasSnapshots(RuntimeError):
    pass


class Sub2APIUserListView(AdminAPIView):
    def get(self, _request):
        try:
            with Sub2APIClient(AppSettings.load()) as client:
                users = client.list_users()
            # 用户名可能为空，仍必须用本次 Admin API 结果覆盖旧缓存；否则曾经
            # 错存的本地参与者名称会永久残留。邮箱用于空用户名时的稳定展示。
            metadata = {
                int(item["id"]): {
                    "username": str(item.get("username") or ""),
                    "email": str(item.get("email") or ""),
                }
                for item in users
                if item.get("id") is not None
            }
            cached = list(
                Participant.objects.filter(
                    sub2api_user_id__in=metadata,
                )
            )
            changed = []
            for participant in cached:
                current = metadata[participant.sub2api_user_id]
                if (
                    participant.sub2api_username != current["username"]
                    or participant.sub2api_email != current["email"]
                ):
                    participant.sub2api_username = current["username"]
                    participant.sub2api_email = current["email"]
                    changed.append(participant)
            if changed:
                Participant.objects.bulk_update(
                    changed,
                    ["sub2api_username", "sub2api_email"],
                )
        except (Sub2APIError, ValueError) as exc:
            return error(str(exc), status.HTTP_502_BAD_GATEWAY)
        return ok(users)



class ParticipantListView(PageAccessAPIView):
    required_page_permissions = (PagePermission.PARTICIPANTS,)

    def get(self, request):
        config = AppSettings.load()
        participants = visible_participants_for(
            request.user,
            Participant.objects.prefetch_related("account_memberships__account"),
        )
        return ok([participant_data(item, config) for item in participants])

    def post(self, request):
        serializer = ParticipantWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error("参与者校验失败", details=serializer.errors)
        participant = serializer.save()
        return ok(
            participant_data(participant, AppSettings.load()),
            status.HTTP_201_CREATED,
        )


class ReadOnlyParticipantListView(ParticipantListView):
    """External API-key view exposing only participant table data."""

    authentication_classes = [ReadOnlyAPIKeyAuthentication]
    http_method_names = ["get", "head", "options"]

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
        return ok(
            participant_data(serializer.save(), AppSettings.load())
        )

    def delete(self, _request, participant_id: int):
        participant = self._get_participant(participant_id)
        if participant is None:
            return error("参与者不存在", status.HTTP_404_NOT_FOUND)
        external_account_ids = list(
            MonitoredAccount.objects.values_list("external_account_id", flat=True)
        )
        try:
            with fenced_fact_write(external_account_ids):
                participant = Participant.objects.select_for_update().get(
                    pk=participant_id
                )
                if participant.snapshots.exists():
                    raise _ParticipantHasSnapshots
                participant.delete()
        except _ParticipantHasSnapshots:
            return error(
                "该参与者已有测算账本，不能删除；请改为停用",
                status.HTTP_409_CONFLICT,
            )

        return ok({"deleted": True})
