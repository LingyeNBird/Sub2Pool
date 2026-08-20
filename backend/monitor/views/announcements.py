from django.utils import timezone

from .base import AdminAPIView, error, ok
from ..announcements import ANNOUNCEMENTS, ANNOUNCEMENTS_BY_CODE
from ..models import AnnouncementRead


def _serialize_announcement(item, read_at):
    return {
        "code": item.code,
        "title": item.title,
        "published_at": item.published_at,
        "severity": item.severity,
        "paragraphs": list(item.paragraphs),
        "read": read_at is not None,
        "read_at": read_at.isoformat() if read_at is not None else None,
    }


class AnnouncementListView(AdminAPIView):
    """Return release announcements with per-admin read state."""

    def get(self, request):
        reads = dict(
            AnnouncementRead.objects.filter(user=request.user).values_list(
                "announcement_code",
                "read_at",
            )
        )
        items = [
            _serialize_announcement(item, reads.get(item.code))
            for item in ANNOUNCEMENTS
        ]
        return ok(
            {
                "items": items,
                "unread_count": sum(not item["read"] for item in items),
            }
        )


class AnnouncementReadView(AdminAPIView):
    """Mark one known announcement as read for the current admin."""

    def post(self, request, announcement_code: str):
        item = ANNOUNCEMENTS_BY_CODE.get(announcement_code)
        if item is None:
            return error("公告不存在", 404)
        read, _ = AnnouncementRead.objects.get_or_create(
            user=request.user,
            announcement_code=item.code,
            defaults={"read_at": timezone.now()},
        )
        return ok(_serialize_announcement(item, read.read_at))
