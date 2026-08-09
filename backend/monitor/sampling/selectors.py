"""采样流程使用的观测查询。"""

from ..models import Observation


def latest_included(account_id: int) -> Observation | None:
    return (
        Observation.objects.filter(
            account_id=account_id,
            excluded_at__isnull=True,
        )
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )


def latest_raw(account_id: int) -> Observation | None:
    return (
        Observation.objects.filter(account_id=account_id)
        .prefetch_related("participant_snapshots__participant")
        .order_by("-observed_at", "-id")
        .first()
    )


def has_pending_rollback(account_id: int) -> bool:
    return Observation.objects.filter(
        account_id=account_id,
        exclusion_source="automatic",
    ).exists()
