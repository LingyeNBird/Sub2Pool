from django.utils import timezone

from .base import AdminAPIView, error, ok
from ..announcements import ANNOUNCEMENTS, ANNOUNCEMENTS_BY_CODE
from ..models import AnnouncementRead, AppSettings
from ..upstream_pricing.service import pricing_state_payload


def _serialize_announcement(item, read_at):
    payload = {
        "code": item.code,
        "title": item.title,
        "published_at": item.published_at,
        "severity": item.severity,
        "paragraphs": list(item.paragraphs),
        "read": read_at is not None,
        "read_at": read_at.isoformat() if read_at is not None else None,
    }
    if item.code == "sub2api-upstream-pricing-2026-09-08":
        pricing = pricing_state_payload()
        status_text = {
            "pending": "尚未应用：请点击一键应用修正，选择目标分组并确认；也可在设置页自定义。",
            "applied": "最近一次上游配置已全部写入并读回确认。",
            "partial": "上游操作仅部分成功；未全部成功的账号不会标为已修正。",
            "failed": "上游配置未成功完成；不会自动重复覆盖，请查看错误后手动重试。",
            "reverted": "上游计费已撤回至接管前配置；不会自动重新应用，本地修正也不会恢复。",
        }[pricing["status"]]
        policy = pricing["policy"]
        fast = "、".join(
            f'{row["model_pattern"]} → {row["multiplier"]} 倍'
            for row in policy["fast_rules"]
        ) or "不接管"
        model = "、".join(
            f'{row["model_pattern"]} → {row["multiplier"]} 倍'
            for row in policy["model_rules"]
        ) or "不接管"
        long_context = {
            True: "启用", False: "关闭", None: "不接管",
        }[policy["long_context_pricing_enabled"]]
        payload["paragraphs"] = [
            status_text,
            f"目标策略：FAST {fast}；模型 {model}；长上下文阶梯计费{long_context}。",
            *payload["paragraphs"],
        ]
        if pricing["last_error"]:
            payload["paragraphs"].append(f'操作详情：{pricing["last_error"]}')
        payload.update(
            pricing_status=pricing["status"],
            can_revert=pricing["can_revert"],
            pricing_revision=pricing["revision"],
            can_apply_pricing=pricing["announcement_applied_at"] is None,
        )
        if pricing["status"] in {"partial", "failed"}:
            payload["severity"] = "warning"
    elif item.code == "auto-apply-recommendations-default-2026-09-08":
        payload["auto_apply_recommendations"] = AppSettings.load().auto_apply_recommendations
    return payload


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
