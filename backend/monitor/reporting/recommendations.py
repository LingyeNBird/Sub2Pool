"""额度模型、参与者归属与余额建议的统一读取投影。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .common import iso
from ..models import AppSettings, Observation, Participant, ParticipantSnapshot


ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")
TRUNCATED_PERCENT_TAIL = Decimal("0.9")
HUNDRED = Decimal("100")


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
    """序列化持久化的时变归属结论。"""
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
        "recommended_balance_min_usd": None,
        "recommended_balance_max_usd": None,
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
    """读取未排除观测对应的最新参与者账本。"""
    return (
        participant.snapshots.select_related("observation")
        .filter(observation__excluded_at__isnull=True)
        .order_by("-observation__observed_at")
        .first()
    )


def _constant_average_recommendation_bounds(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
    *,
    selected_cost: Decimal,
    remaining_share_percent: Decimal,
) -> tuple[Decimal, Decimal]:
    """按截尾整数百分比反推容量区间，再换算参与者的剩余余额区间。"""
    observation = snapshot.observation
    used_percent_min = max(ZERO, observation.interval_used_percent)
    if used_percent_min <= 0:
        display_rate, _raw_rate = display_cycle_rates(observation, config)
        fallback = (
            remaining_share_percent * display_rate * config.safety_factor
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        return fallback, fallback

    # 上游显示 p% 时按截尾值处理，真实进度视为 [p, p + 0.9]。
    # 容量与进度互为反比，因此较大的进度端点产生较小的容量估计。
    used_percent_max = min(
        HUNDRED,
        used_percent_min + TRUNCATED_PERCENT_TAIL,
    )
    total_cost = max(ZERO, observation.selected_total_cost)
    capacity_min = total_cost * HUNDRED / used_percent_max
    capacity_max = total_cost * HUNDRED / used_percent_min
    share_ratio = snapshot.participant.share_percent / HUNDRED
    recommended_min = (
        max(ZERO, capacity_min * share_ratio - selected_cost)
        * config.safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    recommended_max = (
        max(ZERO, capacity_max * share_ratio - selected_cost)
        * config.safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    return recommended_min, recommended_max


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
    recommended_min, recommended_max = (
        _constant_average_recommendation_bounds(
            snapshot,
            config,
            selected_cost=selected_cost,
            remaining_share_percent=remaining,
        )
    )
    recommended = (
        (recommended_min + recommended_max) / Decimal("2")
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    balance = (
        snapshot.current_balance_usd
        if snapshot.current_balance_usd is not None
        else snapshot.participant.latest_balance_usd
    )
    if balance is None:
        difference = None
    elif balance < recommended_min:
        difference = (recommended_min - balance).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
    elif balance > recommended_max:
        difference = (recommended_max - balance).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
    else:
        difference = ZERO
    exhausted = bool(
        balance is not None and balance <= config.limit_warning_usd
    )
    needs_update = bool(
        difference is not None
        and (
            abs(difference) >= config.recommendation_change_usd
            or (exhausted and recommended_max > 0)
        )
    )
    if remaining <= 0:
        reason = "本上游周期的百分比权益已用尽"
    elif exhausted:
        reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
    elif needs_update:
        reason = "当前用户余额与平均恒定模型建议区间差异较大"
    else:
        reason = "当前用户余额处于建议区间内，无需调整"
    return {
        "selected_cost": selected_cost,
        "charged_cycle_percent": charged,
        "remaining_share_percent": remaining,
        "current_balance_usd": balance,
        "recommended_balance_usd": recommended,
        "recommended_balance_min_usd": recommended_min,
        "recommended_balance_max_usd": recommended_max,
        "balance_difference_usd": difference,
        "needs_manual_update": needs_update,
        "reason": reason,
    }


def display_snapshot_data(
    participant: Participant,
    config: AppSettings,
) -> dict | None:
    """按当前展示模型读取参与者归属与余额建议。"""
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
        "recommended_balance_min_usd": float(
            values["recommended_balance_min_usd"]
        ),
        "recommended_balance_max_usd": float(
            values["recommended_balance_max_usd"]
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
    """生成参与者列表和首页共用的稳定读取结构。"""
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
