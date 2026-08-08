"""观测、通知和登录审计只读 API。"""

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .base import AdminAPIView, error, ok
from .presenters import bounded_query_int, iso, snapshot_data
from ..login_audit import request_addresses
from ..models import (
    BlockedIPAddress,
    LoginEvent,
    NotificationEvent,
    Observation,
    Participant,
)
from ..serializers import BlockedIPAddressSerializer
from ..replay import exclude_observation, restore_observation


def query_datetime(request, name: str):
    """读取 ISO 日期时间查询参数；无时区值按 Django 当前时区解释。"""
    value = request.query_params.get(name)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"{name} 不是有效的日期时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def paginated_rows(request, queryset, default_size: int = 20) -> tuple[list, dict]:
    """统一记录接口的分页结构，防止审计表随数据库增长而一次性加载。"""
    page_size = bounded_query_int(request, "page_size", default_size, 100)
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return list(page.object_list), {
        "page": page.number,
        "page_size": page_size,
        "total": paginator.count,
        "total_pages": paginator.num_pages,
    }


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
        queryset = Observation.objects.prefetch_related(
            "participant_snapshots__participant"
        )
        try:
            observed_from = query_datetime(request, "from")
            observed_to = query_datetime(request, "to")
        except ValueError as exc:
            return error(str(exc), 400)
        if observed_from:
            queryset = queryset.filter(observed_at__gte=observed_from)
        if observed_to:
            queryset = queryset.filter(observed_at__lte=observed_to)

        source = request.query_params.get("source")
        valid_sources = {value for value, _label in Observation.SOURCE_CHOICES}
        if source:
            if source not in valid_sources:
                return error("来源筛选值无效", 400)
            queryset = queryset.filter(source=source)

        query_mode = request.query_params.get("query_mode")
        if query_mode:
            if query_mode not in {"passive", "direct"}:
                return error("查询方式筛选值无效", 400)
            if query_mode == "passive":
                queryset = queryset.filter(raw_window__query_mode="passive")
            else:
                # 旧观测尚未写 query_mode 时，展示层一直将它解释为上游直查。
                queryset = queryset.filter(
                    Q(raw_window__query_mode="direct")
                    | ~Q(raw_window__has_key="query_mode")
                )

        total = queryset.count()
        excluded_count = queryset.filter(excluded_at__isnull=False).count()
        valid_count = queryset.filter(valid_sample=True).count()
        passive_count = queryset.filter(raw_window__query_mode="passive").count()
        rows, pagination = paginated_rows(request, queryset)
        result = []
        for item in rows:
            result.append(
                {
                    "id": item.id,
                    "observed_at": iso(item.observed_at),
                    "source": item.source,
                    "account_id": item.account_id,
                    "attribution_started_at": iso(item.attribution_started_at),
                    "upstream_resets_at": iso(item.upstream_resets_at),
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
                    "rate_method": item.raw_window.get(
                        "rate_method",
                        "incremental_legacy",
                    ),
                    "query_mode": item.raw_window.get("query_mode", "direct"),
                    "snapshot_sampled_at": item.raw_window.get("sampled_at"),
                    "excluded": item.excluded_at is not None,
                    "excluded_at": iso(item.excluded_at),
                    "exclusion_reason": item.exclusion_reason,
                    "exclusion_source": item.exclusion_source,
                    "participants": [
                        snapshot_data(snapshot)
                        for snapshot in item.participant_snapshots.all()
                    ],
                }
            )
        return ok(
            {
                "items": result,
                "pagination": pagination,
                "summary": {
                    "total": total,
                    "valid_count": valid_count,
                    "passive_count": passive_count,
                    "excluded_count": excluded_count,
                },
            }
        )


class ObservationExclusionView(AdminAPIView):
    """排除一条校准记录，并按剩余原始数据重放全部派生结果。"""

    def post(self, request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        reason = request.data.get("reason", "管理员手动排除")
        if not isinstance(reason, str):
            return error("排除原因格式无效", 400)
        result = exclude_observation(observation, reason)
        return ok(result)


class ObservationRestoreView(AdminAPIView):
    """恢复一条排除记录，并立即重放该账号的全部原始观测。"""

    def post(self, _request, observation_id: int):
        observation = get_object_or_404(Observation, pk=observation_id)
        return ok(restore_observation(observation))


class NotificationListView(AdminAPIView):
    def get(self, request):
        queryset = NotificationEvent.objects.select_related("participant")
        try:
            created_from = query_datetime(request, "from")
            created_to = query_datetime(request, "to")
        except ValueError as exc:
            return error(str(exc), 400)
        if created_from:
            queryset = queryset.filter(created_at__gte=created_from)
        if created_to:
            queryset = queryset.filter(created_at__lte=created_to)

        event_type = request.query_params.get("event_type")
        valid_types = {value for value, _label in NotificationEvent.TYPE_CHOICES}
        if event_type:
            if event_type not in valid_types:
                return error("通知类型筛选值无效", 400)
            queryset = queryset.filter(event_type=event_type)

        participant = request.query_params.get("participant")
        if participant:
            if participant == "system":
                queryset = queryset.filter(participant__isnull=True)
            else:
                try:
                    participant_id = int(participant)
                except ValueError:
                    return error("参与者筛选值无效", 400)
                queryset = queryset.filter(participant_id=participant_id)

        subject = request.query_params.get("subject", "").strip()
        if subject:
            queryset = queryset.filter(subject__icontains=subject)

        event_status = request.query_params.get("status")
        valid_statuses = {
            value for value, _label in NotificationEvent.STATUS_CHOICES
        }
        if event_status:
            if event_status not in valid_statuses:
                return error("通知状态筛选值无效", 400)
            queryset = queryset.filter(status=event_status)

        total = queryset.count()
        sent_count = queryset.filter(status="sent").count()
        failed_count = queryset.filter(status="failed").count()
        rows, pagination = paginated_rows(request, queryset)
        return ok(
            {
                "items": [
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
                ],
                "pagination": pagination,
                "summary": {
                    "total": total,
                    "sent_count": sent_count,
                    "failed_count": failed_count,
                },
                "filter_options": {
                    "types": [
                        {"value": value, "label": label}
                        for value, label in NotificationEvent.TYPE_CHOICES
                    ],
                    "participants": [
                        {"id": item.id, "name": item.name}
                        for item in Participant.objects.all()
                    ],
                    "statuses": [
                        {"value": value, "label": label}
                        for value, label in NotificationEvent.STATUS_CHOICES
                    ],
                },
            }
        )


class LoginEventListView(AdminAPIView):
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
