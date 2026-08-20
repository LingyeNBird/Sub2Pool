"""登录审计与 IP 封禁接口。"""

from django.shortcuts import get_object_or_404

from .base import AdminAPIView, PageAccessAPIView, error, ok
from ..reporting import iso
from .record_helpers import paginated_rows
from ..login_audit import request_addresses
from ..models import BlockedIPAddress, LoginEvent, PagePermission
from ..serializers import BlockedIPAddressSerializer


class BlockedIPAddressListView(PageAccessAPIView):
    """列出封禁项，并允许管理员从登录审计记录创建封禁。"""

    required_page_permissions = (PagePermission.LOGIN_RECORDS,)

    def get(self, _request):
        rows = BlockedIPAddress.objects.select_related("login_event")
        return ok(BlockedIPAddressSerializer(rows, many=True).data)

    def post(self, request):
        serializer = BlockedIPAddressSerializer(data=request.data)
        if not serializer.is_valid():
            return error("封禁参数无效", details=serializer.errors)

        address = str(serializer.validated_data["address"])
        source_type = serializer.validated_data["source_type"]
        request_ip, remote_ip = request_addresses(request._request)
        if source_type == "request" and address == request_ip:
            return error("不能封禁当前会话的服务器来源 IP")
        if source_type == "remote" and address == remote_ip:
            return error("不能封禁当前会话的直连地址")

        blocked = serializer.save()
        return ok(BlockedIPAddressSerializer(blocked).data, 201)


class BlockedIPAddressDetailView(AdminAPIView):
    """解除一个持久化 IP 封禁。"""

    def delete(self, _request, block_id: int):
        blocked = get_object_or_404(BlockedIPAddress, pk=block_id)
        blocked.delete()
        return ok()


class LoginEventListView(PageAccessAPIView):
    required_page_permissions = (PagePermission.LOGIN_RECORDS,)

    def get(self, request):
        queryset = LoginEvent.objects.all()
        rows, pagination = paginated_rows(request, queryset)
        return ok(
            {
                "success_count": queryset.filter(success=True).count(),
                "failure_count": queryset.filter(success=False).count(),
                "unique_request_ips": queryset.exclude(request_ip__isnull=True)
                .values("request_ip")
                .distinct()
                .count(),
                "items": [
                    {
                        "id": item.id,
                        "username": item.username,
                        "success": item.success,
                        "request_ip": item.request_ip,
                        "remote_ip": item.remote_ip,
                        "webrtc_supported": item.webrtc_supported,
                        "webrtc_ips": item.webrtc_ips,
                        "user_agent": item.user_agent,
                        "failure_reason": item.failure_reason,
                        "created_at": iso(item.created_at),
                    }
                    for item in rows
                ],
                "pagination": pagination,
            }
        )
