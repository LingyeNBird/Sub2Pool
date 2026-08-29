"""参与者资源 API。"""
from decimal import Decimal

from rest_framework import serializers, status

from ..access import (
    scope_participant_data,
    visible_account_ids,
    visible_participants_for,
)
from ..api_auth import APIKeyAuthentication
from ..history_state import fenced_fact_write
from ..integrations.sub2api import Sub2APIClient, Sub2APIError
from ..models import (
    AppSettings,
    MonitoredAccount,
    PagePermission,
    Participant,
    QuotaPool,
)
from ..reporting import participant_data
from ..serializers import (
    MonitoredAccountSerializer,
    ParticipantWriteSerializer,
    QuotaAllocationWriteSerializer,
)
from .base import AdminAPIView, PageAccessAPIView, error, ok


class _ParticipantHasSnapshots(RuntimeError):
    pass


def quota_allocation_data(user) -> dict:
    accounts = list(
        MonitoredAccount.objects.filter(provider="sub2api")
        .select_related("pool")
        .order_by(
            "pool__name",
            "pool_id",
            "name",
            "external_account_id",
        )
    )
    pools = list(
        QuotaPool.objects.filter(accounts__provider="sub2api")
        .prefetch_related(
            "accounts",
            "allocations__participant",
        )
        .distinct()
        .order_by("name", "id")
    )
    participants = list(
        visible_participants_for(
            user,
            Participant.objects.order_by("-is_owner", "id"),
        )
    )
    visible_participant_ids = {
        participant.id for participant in participants
    }
    return {
        "accounts": MonitoredAccountSerializer(accounts, many=True).data,
        "participants": [
            {
                "id": participant.id,
                "name": participant.name,
                "sub2api_user_id": participant.sub2api_user_id,
                "sub2api_username": participant.sub2api_username,
                "sub2api_email": participant.sub2api_email,
                "sub2api_identity": (
                    participant.sub2api_username
                    or participant.sub2api_email
                    or f"账号 {participant.sub2api_user_id}"
                ),
                "is_owner": participant.is_owner,
                "enabled": participant.enabled,
            }
            for participant in participants
        ],
        "pools": [
            {
                "id": pool.id,
                "name": pool.name,
                "contract_revision": pool.contract_revision,
                "account_ids": [
                    account.id
                    for account in sorted(
                        [
                            item
                            for item in pool.accounts.all()
                            if item.provider == "sub2api"
                        ],
                        key=lambda item: (
                            item.name,
                            item.external_account_id or 0,
                        ),
                    )
                ],
                "allocations": [
                    {
                        "participant_id": allocation.participant_id,
                        "share_percent": float(allocation.share_percent),
                    }
                    for allocation in pool.allocations.all()
                    if allocation.participant_id in visible_participant_ids
                ],
                "total_share_percent": float(
                    sum(
                        (
                            allocation.share_percent
                            for allocation in pool.allocations.all()
                            if allocation.participant_id
                            in visible_participant_ids
                        ),
                        Decimal("0"),
                    )
                ),
            }
            for pool in pools
        ],
    }


class QuotaAllocationView(PageAccessAPIView):
    required_page_permissions = (PagePermission.PARTICIPANTS,)

    def get(self, request):
        return ok(quota_allocation_data(request.user))

    def put(self, request):
        serializer = QuotaAllocationWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return error("分配方案校验失败", details=serializer.errors)
        external_account_ids = [
            account.fact_key
            for account in MonitoredAccount.objects.filter(
                provider="sub2api"
            ).order_by("external_account_id")
        ]
        settings_id = AppSettings.load().pk
        try:
            with fenced_fact_write(external_account_ids):
                AppSettings.objects.select_for_update().get(pk=settings_id)
                serializer.apply()
        except serializers.ValidationError as exc:
            return error("分配方案已过期", details=exc.detail)
        return ok(quota_allocation_data(request.user))


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
        allowed_account_ids = visible_account_ids(request.user)
        return ok(
            [
                scope_participant_data(
                    participant_data(item, config),
                    allowed_account_ids,
                )
                for item in participants
            ]
        )

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

    authentication_classes = [APIKeyAuthentication]
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
        external_account_ids = [
            account.fact_key for account in MonitoredAccount.objects.all()
        ]
        try:
            with fenced_fact_write(external_account_ids):
                participant = Participant.objects.select_for_update().get(
                    pk=participant_id
                )
                if participant.snapshots.exists():
                    raise _ParticipantHasSnapshots
                pool_ids = list(
                    participant.pool_allocations.order_by().values_list(
                        "pool_id",
                        flat=True,
                    )
                )
                participant.delete()
                QuotaPool.bump_contract_revisions(pool_ids)
        except _ParticipantHasSnapshots:
            return error(
                "该参与者已有测算账本，不能删除；请改为停用",
                status.HTTP_409_CONFLICT,
            )

        return ok({"deleted": True})
