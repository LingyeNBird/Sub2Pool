"""把 ORM 对象转换成前端现有的稳定 JSON 结构。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from ..models import AppSettings, Observation, Participant, ParticipantSnapshot


def iso(value):
    return value.isoformat() if value else None


ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")


def display_cycle_rates(
    observation: Observation,
    config: AppSettings,
) -> tuple[Decimal, Decimal | None]:
    """返回展示模型采用的美元/1%，以及周期累计端点的原始美元/1%。"""
    used_percent = observation.interval_used_percent
    raw_rate = (
        observation.selected_total_cost / used_percent
        if used_percent > 0
        else None
    )
    if (
        config.weekly_quota_model == "constant_average"
        and raw_rate is not None
    ):
        return raw_rate, raw_rate
    return observation.effective_usd_per_percent, raw_rate


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
        "current_balance_usd": (
            float(snapshot.current_balance_usd)
            if snapshot.current_balance_usd is not None
            else None
        ),
        "recommended_balance_usd": (
            float(snapshot.recommended_balance_usd)
            if snapshot.recommended_balance_usd is not None
            else None
        ),
        "balance_difference_usd": (
            float(snapshot.balance_difference_usd)
            if snapshot.balance_difference_usd is not None
            else None
        ),
        "needs_manual_update": snapshot.needs_manual_update,
        "recommendation_applied": snapshot.recommendation_applied,
        "reason": snapshot.reason,
        "allocation_model": "time_varying",
    }


def latest_snapshot(participant: Participant) -> ParticipantSnapshot | None:
    return (
        participant.snapshots.select_related("observation")
        .filter(observation__excluded_at__isnull=True)
        .order_by("-observation__observed_at")
        .first()
    )

def _constant_average_values(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
) -> dict:
    """用起点至当前的累计成本比例生成只读展示值，不改写时变账本。"""

    observation = snapshot.observation
    selected_cost = max(ZERO, snapshot.selected_cost)
    denominator = max(
        ZERO,
        observation.selected_total_cost,
        selected_cost,
    )
    charged = (
        observation.interval_used_percent * selected_cost / denominator
        if denominator > 0
        else ZERO
    ).quantize(PCT_PRECISION, rounding=ROUND_HALF_UP)
    remaining = max(
        ZERO,
        snapshot.participant.share_percent - charged,
    ).quantize(PCT_PRECISION, rounding=ROUND_HALF_UP)
    display_rate, _raw_rate = display_cycle_rates(observation, config)
    recommended = (
        remaining * display_rate * config.safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    balance = (
        snapshot.current_balance_usd
        if snapshot.current_balance_usd is not None
        else snapshot.participant.latest_balance_usd
    )
    difference = (
        (recommended - balance).quantize(CENT, rounding=ROUND_HALF_UP)
        if balance is not None
        else None
    )
    exhausted = bool(
        balance is not None and balance <= config.limit_warning_usd
    )
    needs_update = bool(
        difference is not None
        and (
            abs(difference) >= config.recommendation_change_usd
            or (exhausted and remaining > 0)
        )
    )
    if remaining <= 0:
        reason = "本上游周期的百分比权益已用尽"
    elif exhausted:
        reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
    elif needs_update:
        reason = "当前用户余额与平均恒定模型建议差异较大"
    else:
        reason = "当前用户余额无需调整"
    return {
        "selected_cost": selected_cost,
        "charged_cycle_percent": charged,
        "remaining_share_percent": remaining,
        "current_balance_usd": balance,
        "recommended_balance_usd": recommended,
        "balance_difference_usd": difference,
        "needs_manual_update": needs_update,
        "reason": reason,
    }


def display_snapshot_data(
    participant: Participant,
    config: AppSettings,
) -> dict | None:
    snapshot = latest_snapshot(participant)
    if snapshot is None:
        return None
    if config.weekly_quota_model != "constant_average":
        return snapshot_data(snapshot)

    values = _constant_average_values(snapshot, config)
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": participant.name,
        "selected_cost": float(values["selected_cost"]),
        "delta_cost": None,
        "charged_delta_percent": 0.0,
        "charged_cycle_percent": float(values["charged_cycle_percent"]),
        "remaining_share_percent": float(
            values["remaining_share_percent"]
        ),
        "current_balance_usd": (
            float(values["current_balance_usd"])
            if values["current_balance_usd"] is not None
            else None
        ),
        "recommended_balance_usd": float(
            values["recommended_balance_usd"]
        ),
        "balance_difference_usd": (
            float(values["balance_difference_usd"])
            if values["balance_difference_usd"] is not None
            else None
        ),
        "needs_manual_update": values["needs_manual_update"],
        "recommendation_applied": snapshot.recommendation_applied,
        "reason": values["reason"],
        "allocation_model": "constant_average",
    }


def display_recommendation(
    participant: Participant,
    config: AppSettings,
) -> tuple[ParticipantSnapshot | None, Decimal | None]:
    """返回当前展示模型对应的建议值，供显式一键设置使用。"""

    snapshot = latest_snapshot(participant)
    if snapshot is None:
        return None, None
    if config.weekly_quota_model == "constant_average":
        values = _constant_average_values(snapshot, config)
        return snapshot, values["recommended_balance_usd"]
    return snapshot, snapshot.recommended_balance_usd


def participant_data(
    participant: Participant,
    config: AppSettings | None = None,
) -> dict:
    config = config or AppSettings.load()
    snapshot = display_snapshot_data(participant, config)
    return {
        "id": participant.id,
        "name": participant.name,
        "email": participant.email,
        "sub2api_user_id": participant.sub2api_user_id,
        "sub2api_username": participant.sub2api_username,
        "sub2api_email": participant.sub2api_email,
        "sub2api_identity": (
            participant.sub2api_username
            or participant.sub2api_email
            or f"账号 {participant.sub2api_user_id}"
        ),
        "share_percent": float(participant.share_percent),
        "is_owner": participant.is_owner,
        "enabled": participant.enabled,
        "notes": participant.notes,
        "latest_balance_usd": (
            float(participant.latest_balance_usd)
            if participant.latest_balance_usd is not None
            else None
        ),
        "latest_selected_cost": (
            snapshot["selected_cost"]
            if snapshot is not None
            else (
                float(participant.latest_selected_cost)
                if participant.latest_selected_cost is not None
                else None
            )
        ),
        "last_checked_at": iso(participant.last_checked_at),
        "snapshot": snapshot,
    }


def bounded_query_int(request, name: str, default: int, maximum: int) -> int:
    try:
        return min(max(int(request.query_params.get(name, default)), 1), maximum)
    except (TypeError, ValueError):
        return default
