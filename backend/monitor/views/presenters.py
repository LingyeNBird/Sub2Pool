"""把 ORM 对象转换成前端现有的稳定 JSON 结构。"""

from __future__ import annotations

from ..models import Participant, ParticipantSnapshot


def iso(value):
    return value.isoformat() if value else None


def snapshot_data(snapshot: ParticipantSnapshot) -> dict:
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": (
            snapshot.participant.name if hasattr(snapshot, "participant") else ""
        ),
        "selected_cost": float(snapshot.selected_cost),
        "delta_cost": (
            float(snapshot.delta_cost) if snapshot.delta_cost is not None else None
        ),
        "charged_delta_percent": float(snapshot.charged_delta_percent),
        "charged_cycle_percent": float(snapshot.charged_cycle_percent),
        "remaining_share_percent": float(snapshot.remaining_share_percent),
        "platform_weekly_usage_usd": (
            float(snapshot.platform_weekly_usage_usd)
            if snapshot.platform_weekly_usage_usd is not None
            else None
        ),
        "platform_weekly_limit_usd": (
            float(snapshot.platform_weekly_limit_usd)
            if snapshot.platform_weekly_limit_usd is not None
            else None
        ),
        "recommended_weekly_limit_usd": float(
            snapshot.recommended_weekly_limit_usd
        ),
        "recommendation_difference_usd": (
            float(snapshot.recommendation_difference_usd)
            if snapshot.recommendation_difference_usd is not None
            else None
        ),
        "needs_manual_update": snapshot.needs_manual_update,
        "reason": snapshot.reason,
    }


def latest_snapshot(participant: Participant) -> ParticipantSnapshot | None:
    return (
        participant.snapshots.select_related("observation", "observation__cycle")
        .order_by("-observation__observed_at")
        .first()
    )


def participant_data(participant: Participant) -> dict:
    snapshot = latest_snapshot(participant)
    return {
        "id": participant.id,
        "name": participant.name,
        "email": participant.email,
        "sub2api_user_id": participant.sub2api_user_id,
        "share_percent": float(participant.share_percent),
        "is_owner": participant.is_owner,
        "enabled": participant.enabled,
        "notes": participant.notes,
        "latest_weekly_usage_usd": (
            float(participant.latest_weekly_usage_usd)
            if participant.latest_weekly_usage_usd is not None
            else None
        ),
        "latest_weekly_limit_usd": (
            float(participant.latest_weekly_limit_usd)
            if participant.latest_weekly_limit_usd is not None
            else None
        ),
        "latest_selected_cost": (
            float(participant.latest_selected_cost)
            if participant.latest_selected_cost is not None
            else None
        ),
        "last_checked_at": iso(participant.last_checked_at),
        "snapshot": snapshot_data(snapshot) if snapshot else None,
    }


def bounded_query_int(request, name: str, default: int, maximum: int) -> int:
    try:
        return min(max(int(request.query_params.get(name, default)), 1), maximum)
    except (TypeError, ValueError):
        return default
