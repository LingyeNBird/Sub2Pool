"""通知审计查询接口。"""

from django.db.models import Q

from .base import PageAccessAPIView, error, ok
from ..access import visible_participants_for
from ..reporting import iso
from .record_helpers import paginated_rows, query_datetime
from ..models import NotificationEvent, PagePermission, Participant


class NotificationListView(PageAccessAPIView):
    required_page_permissions = (PagePermission.NOTIFICATIONS,)

    def get(self, request):
        queryset = NotificationEvent.objects.select_related("participant")
        if not request.user.is_staff:
            queryset = queryset.filter(
                Q(participant__authorized_users=request.user)
                | Q(participant__isnull=True)
            )
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
                        for item in visible_participants_for(request.user)
                    ],
                    "statuses": [
                        {"value": value, "label": label}
                        for value, label in NotificationEvent.STATUS_CHOICES
                    ],
                },
            }
        )
