"""观测、通知和登录审计只读 API。"""

from django.shortcuts import get_object_or_404

from .base import AdminAPIView, error, ok
from .presenters import bounded_query_int, iso, snapshot_data
from ..login_audit import request_addresses
from ..models import (
    BlockedIPAddress,
    LoginEvent,
    NotificationEvent,
    Observation,
)
from ..serializers import BlockedIPAddressSerializer


class BlockedIPAddressListView(AdminAPIView):
    """列出封禁项，并允许管理员从登录审计记录创建封禁。"""

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


class ObservationListView(AdminAPIView):
    def get(self, request):
        limit = bounded_query_int(request, "limit", 50, 200)
        rows = Observation.objects.select_related("cycle").prefetch_related(
            "participant_snapshots__participant"
        )[:limit]
        result = []
        for item in rows:
            result.append(
                {
                    "id": item.id,
                    "observed_at": iso(item.observed_at),
                    "source": item.source,
                    "cycle_id": item.cycle_id,
                    "cycle_resets_at": iso(item.cycle.resets_at),
                    "upstream_used_percent": float(item.upstream_used_percent),
                    "selected_total_cost": float(item.selected_total_cost),
                    "delta_percent": (
                        float(item.delta_percent)
                        if item.delta_percent is not None
                        else None
                    ),
                    "delta_cost": (
                        float(item.delta_cost)
                        if item.delta_cost is not None
                        else None
                    ),
                    "sample_usd_per_percent": (
                        float(item.sample_usd_per_percent)
                        if item.sample_usd_per_percent is not None
                        else None
                    ),
                    "effective_usd_per_percent": float(
                        item.effective_usd_per_percent
                    ),
                    "valid_sample": item.valid_sample,
                    "sample_note": item.sample_note,
                    "rate_method": item.raw_window.get("rate_method", "incremental_legacy"),
                    "query_mode": item.raw_window.get("query_mode", "direct"),
                    "snapshot_sampled_at": item.raw_window.get("sampled_at"),
                    "participants": [
                        snapshot_data(snapshot)
                        for snapshot in item.participant_snapshots.all()
                    ],
                }
            )
        return ok(result)


class NotificationListView(AdminAPIView):
    def get(self, request):
        limit = bounded_query_int(request, "limit", 100, 300)
        rows = NotificationEvent.objects.select_related("participant")[:limit]
        return ok(
            [
                {
                    "id": item.id,
                    "event_type": item.event_type,
                    "event_type_label": item.get_event_type_display(),
                    "severity": item.severity,
                    "participant_name": (
                        item.participant.name if item.participant else None
                    ),
                    "recipient": item.recipient,
                    "subject": item.subject,
                    "body": item.body,
                    "status": item.status,
                    "status_label": item.get_status_display(),
                    "error": item.error,
                    "created_at": iso(item.created_at),
                    "sent_at": iso(item.sent_at),
                }
                for item in rows
            ]
        )


class LoginEventListView(AdminAPIView):
    def get(self, request):
        limit = bounded_query_int(request, "limit", 100, 300)
        queryset = LoginEvent.objects.all()
        rows = list(queryset[:limit])
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
            }
        )
