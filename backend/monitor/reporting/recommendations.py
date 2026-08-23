"""额度模型、参与者归属与余额建议的统一读取投影。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .common import iso
from ..models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantBalanceOperation,
    PoolParticipant,
)


ZERO = Decimal("0")
CENT = Decimal("0.01")
PCT_PRECISION = Decimal("0.00001")
TRUNCATED_PERCENT_TAIL = Decimal("0.9")
HUNDRED = Decimal("100")

def _overuse_values(
    *,
    share_percent: Decimal,
    charged_percent: Decimal,
    charged_lower: Decimal | None,
    charged_upper: Decimal | None,
) -> dict[str, Decimal | bool]:
    """用概率区间下界确认超用，避免把边界不确定性误报为违约。"""
    point = max(ZERO, charged_percent - share_percent)
    if charged_lower is None or charged_upper is None:
        return {
            "is_overused": point > ZERO,
            "overused_percent": point,
            "overused_percent_min": point,
            "overused_percent_max": point,
        }
    return {
        "is_overused": charged_lower > share_percent,
        "overused_percent": point,
        "overused_percent_min": max(ZERO, charged_lower - share_percent),
        "overused_percent_max": max(ZERO, charged_upper - share_percent),
    }


def _overuse_reason(values: dict[str, Decimal | bool]) -> str:
    return (
        "本上游周期已确认存在合同权益偏差，不再建议补充余额"
        if values["is_overused"]
        else ""
    )




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
    overuse = _overuse_values(
        share_percent=snapshot.share_percent,
        charged_percent=snapshot.charged_cycle_percent,
        charged_lower=snapshot.charged_percent_lower,
        charged_upper=snapshot.charged_percent_upper,
    )
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": (
            snapshot.participant.name if hasattr(snapshot, "participant") else ""
        ),
        "quota_pool_id": snapshot.quota_pool_id,
        "quota_pool_name": snapshot.quota_pool_name,
        "pool_contract_revision": snapshot.pool_contract_revision,
        "share_percent": float(snapshot.share_percent),
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
        "charged_percent_lower": (
            float(snapshot.charged_percent_lower)
            if snapshot.charged_percent_lower is not None
            else None
        ),
        "charged_percent_upper": (
            float(snapshot.charged_percent_upper)
            if snapshot.charged_percent_upper is not None
            else None
        ),
        "recommended_balance_min_usd": (
            float(snapshot.recommended_balance_min_usd)
            if snapshot.recommended_balance_min_usd is not None
            else None
        ),
        "recommended_balance_max_usd": (
            float(snapshot.recommended_balance_max_usd)
            if snapshot.recommended_balance_max_usd is not None
            else None
        ),
        "deterministic_balance_min_usd": (
            float(snapshot.deterministic_balance_min_usd)
            if snapshot.deterministic_balance_min_usd is not None
            else None
        ),
        "deterministic_balance_max_usd": (
            float(snapshot.deterministic_balance_max_usd)
            if snapshot.deterministic_balance_max_usd is not None
            else None
        ),
        "balance_difference_usd": (
            float(snapshot.balance_difference_usd)
            if snapshot.balance_difference_usd is not None
            else None
        ),
        "is_overused": bool(overuse["is_overused"]),
        "overused_percent": float(overuse["overused_percent"]),
        "overused_percent_min": float(overuse["overused_percent_min"]),
        "overused_percent_max": float(overuse["overused_percent_max"]),
        "needs_manual_update": snapshot.needs_manual_update,
        "recommendation_applied": snapshot.recommendation_applied,
        "reason": _overuse_reason(overuse) or snapshot.reason,
        "allocation_model": "time_varying",
    }


def latest_snapshot(
    participant: Participant,
    account: MonitoredAccount | None = None,
) -> ParticipantSnapshot | None:
    """Read the latest account fact collected for the participant's current user."""
    snapshots = participant.snapshots.select_related(
        "observation",
        "participant",
    ).filter(
        observation__excluded_at__isnull=True,
        source_sub2api_user_id=participant.sub2api_user_id,
    )
    if account is not None:
        snapshots = snapshots.filter(
            observation__account_id=account.external_account_id
        )
    return snapshots.order_by("-observation__observed_at", "-id").first()


def _constant_average_charged(
    snapshot: ParticipantSnapshot,
) -> Decimal:
    observation = snapshot.observation
    selected_cost = max(ZERO, snapshot.selected_cost)
    denominator = max(
        ZERO,
        observation.selected_total_cost,
        selected_cost,
    )
    return (
        observation.interval_used_percent * selected_cost / denominator
        if denominator > 0
        else ZERO
    ).quantize(PCT_PRECISION, rounding=ROUND_HALF_UP)


def _is_only_remaining_constant_participant(
    snapshot: ParticipantSnapshot,
) -> bool:
    siblings = list(
        snapshot.observation.participant_snapshots.select_related(
            "participant"
        )
    )
    if len(siblings) <= 1:
        return False
    remaining_ids = [
        item.participant_id
        for item in siblings
        if item.share_percent - _constant_average_charged(item)
        > ZERO
    ]
    return remaining_ids == [snapshot.participant_id]


def _constant_average_recommendation_bounds(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
    *,
    selected_cost: Decimal,
    remaining_share_percent: Decimal,
    safety_factor: Decimal,
) -> tuple[Decimal, Decimal]:
    """按截尾整数百分比反推容量区间，再换算参与者的剩余余额区间。"""
    observation = snapshot.observation
    used_percent_min = max(ZERO, observation.interval_used_percent)
    if used_percent_min <= 0:
        display_rate, _raw_rate = display_cycle_rates(observation, config)
        fallback = (
            remaining_share_percent * display_rate * safety_factor
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
    share_ratio = snapshot.share_percent / HUNDRED
    recommended_min = (
        max(ZERO, capacity_min * share_ratio - selected_cost)
        * safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    recommended_max = (
        max(ZERO, capacity_max * share_ratio - selected_cost)
        * safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    return recommended_min, recommended_max


def _constant_average_values(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
) -> dict:
    """用起点至当前的累计成本比例生成只读展示值，不改写时变账本。"""
    selected_cost = max(ZERO, snapshot.selected_cost)
    charged = _constant_average_charged(snapshot)
    remaining = max(
        ZERO,
        snapshot.share_percent - charged,
    ).quantize(PCT_PRECISION, rounding=ROUND_HALF_UP)
    only_remaining = _is_only_remaining_constant_participant(snapshot)
    safety_factor = Decimal("1") if only_remaining else config.safety_factor
    recommended_min, recommended_max = (
        _constant_average_recommendation_bounds(
            snapshot,
            config,
            selected_cost=selected_cost,
            remaining_share_percent=remaining,
            safety_factor=safety_factor,
        )
    )
    rights_exhausted = remaining <= ZERO
    if rights_exhausted:
        recommended_min = ZERO
        recommended_max = ZERO
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
    overuse = _overuse_values(
        share_percent=snapshot.share_percent,
        charged_percent=charged,
        charged_lower=None,
        charged_upper=None,
    )
    exhausted = bool(
        balance is not None and balance <= config.limit_warning_usd
    )
    if rights_exhausted:
        needs_update = bool(balance is not None and balance > ZERO)
    else:
        needs_update = bool(
            difference is not None
            and (
                abs(difference) >= config.recommendation_change_usd
                or (exhausted and recommended_max > 0)
            )
        )
    if overuse["is_overused"] and not rights_exhausted:
        needs_update = False
    if rights_exhausted:
        reason = (
            "百分比权益已用尽，建议清零 Sub2API 用户余额"
            if needs_update
            else "本上游周期的百分比权益已用尽"
        )
    elif overuse["is_overused"]:
        reason = _overuse_reason(overuse)
    elif exhausted:
        reason = "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
    elif only_remaining:
        reason = "其他参与者权益均已用尽，建议按完整剩余权益计算"
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
        "is_overused": overuse["is_overused"],
        "overused_percent": overuse["overused_percent"],
        "overused_percent_min": overuse["overused_percent_min"],
        "overused_percent_max": overuse["overused_percent_max"],
        "reason": reason,
    }


def _display_snapshot_data(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
) -> dict:
    if config.weekly_quota_model != "constant_average":
        return snapshot_data(snapshot)

    values = _constant_average_values(snapshot, config)
    return {
        "participant_id": snapshot.participant_id,
        "participant_name": snapshot.participant.name,
        "selected_cost": float(values["selected_cost"]),
        "delta_cost": None,
        "charged_delta_percent": 0.0,
        "charged_cycle_percent": float(values["charged_cycle_percent"]),
        "charged_percent_lower": None,
        "charged_percent_upper": None,
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
        "deterministic_balance_min_usd": None,
        "deterministic_balance_max_usd": None,
        "balance_difference_usd": (
            float(values["balance_difference_usd"])
            if values["balance_difference_usd"] is not None
            else None
        ),
        "is_overused": bool(values["is_overused"]),
        "overused_percent": float(values["overused_percent"]),
        "overused_percent_min": float(values["overused_percent_min"]),
        "overused_percent_max": float(values["overused_percent_max"]),
        "needs_manual_update": values["needs_manual_update"],
        "recommendation_applied": snapshot.recommendation_applied,
        "reason": values["reason"],
        "allocation_model": "constant_average",
    }


def display_snapshot_data(
    participant: Participant,
    config: AppSettings,
    account: MonitoredAccount | None = None,
) -> dict | None:
    """Read one account-specific participant ledger for display."""
    snapshot = latest_snapshot(participant, account)
    return _display_snapshot_data(snapshot, config) if snapshot is not None else None


def _account_breakdown_data(
    participant: Participant,
    account: MonitoredAccount,
    allocation: PoolParticipant | None,
    usage: AccountParticipant | None,
    config: AppSettings,
) -> tuple[dict, ParticipantSnapshot | None]:
    snapshot = (
        latest_snapshot(participant, account)
        if allocation is not None
        else None
    )
    displayed = (
        _display_snapshot_data(snapshot, config)
        if snapshot is not None
        else None
    )
    return (
        {
            "id": usage.id if usage is not None else None,
            "account_id": account.id,
            "external_account_id": account.external_account_id,
            "account_name": account.name,
            "account_enabled": account.enabled,
            "pool_id": account.pool_id,
            "pool_name": account.pool.name,
            "contract_share_percent": (
                float(allocation.share_percent)
                if allocation is not None
                else 0.0
            ),
            "allocated": allocation is not None,
            "latest_selected_cost": (
                float(usage.latest_selected_cost)
                if usage is not None and usage.latest_selected_cost is not None
                else None
            ),
            "last_checked_at": (
                iso(usage.last_checked_at) if usage is not None else None
            ),
            "snapshot": displayed,
        },
        snapshot,
    )


def _capacity_values(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    observation = snapshot.observation
    if config.weekly_quota_model == "constant_average":
        charged = _constant_average_charged(snapshot)
        used_percent_min = max(ZERO, observation.interval_used_percent)
        if used_percent_min <= ZERO:
            display_rate, _raw_rate = display_cycle_rates(observation, config)
            capacity_point = display_rate * HUNDRED
            capacity_min = capacity_point
            capacity_max = capacity_point
        else:
            used_percent_max = min(
                HUNDRED,
                used_percent_min + TRUNCATED_PERCENT_TAIL,
            )
            total_cost = max(ZERO, observation.selected_total_cost)
            capacity_min = total_cost * HUNDRED / used_percent_max
            capacity_max = total_cost * HUNDRED / used_percent_min
            capacity_point = (capacity_min + capacity_max) / Decimal("2")
        return (
            capacity_point,
            capacity_min,
            capacity_max,
            charged,
            charged,
            charged,
        )

    capacity_point = observation.effective_usd_per_percent * HUNDRED
    capacity_min = observation.capacity_lower_usd or capacity_point
    capacity_max = observation.capacity_upper_usd or capacity_point
    capacity_min, capacity_max = sorted((capacity_min, capacity_max))
    charged = snapshot.charged_cycle_percent
    charged_lower = (
        snapshot.charged_percent_lower
        if snapshot.charged_percent_lower is not None
        else charged
    )
    charged_upper = (
        snapshot.charged_percent_upper
        if snapshot.charged_percent_upper is not None
        else charged
    )
    charged_lower, charged_upper = sorted((charged_lower, charged_upper))
    return (
        capacity_point,
        capacity_min,
        capacity_max,
        charged,
        charged_lower,
        charged_upper,
    )


def _pool_source_values(
    snapshot: ParticipantSnapshot,
    config: AppSettings,
    share_percent: Decimal,
) -> dict[str, Decimal]:
    (
        capacity_point,
        capacity_min,
        capacity_max,
        charged,
        charged_lower,
        charged_upper,
    ) = _capacity_values(snapshot, config)
    remaining_point = share_percent - charged
    remaining_lower = share_percent - charged_upper
    remaining_upper = share_percent - charged_lower
    interval_products = (
        remaining_lower * capacity_min / HUNDRED,
        remaining_lower * capacity_max / HUNDRED,
        remaining_upper * capacity_min / HUNDRED,
        remaining_upper * capacity_max / HUNDRED,
    )
    return {
        "capacity_point": capacity_point,
        "charged": charged,
        "point": remaining_point * capacity_point / HUNDRED,
        "lower": min(interval_products),
        "upper": max(interval_products),
    }


def _pooled_safety_factor(
    participant: Participant,
    _accounts: list[MonitoredAccount],
    config: AppSettings,
) -> Decimal:
    candidates = list(
        Participant.objects.filter(
            enabled=True,
            pool_allocations__share_percent__gt=ZERO,
            pool_allocations__pool__accounts__enabled=True,
        )
        .distinct()
        .order_by("id")
    )
    if len(candidates) <= 1:
        return config.safety_factor
    remaining_ids = []
    for candidate in candidates:
        candidate_allocations = dict(
            candidate.pool_allocations.filter(
                share_percent__gt=ZERO,
                pool__accounts__enabled=True,
            )
            .distinct()
            .values_list("pool_id", "share_percent")
        )
        candidate_accounts = list(
            MonitoredAccount.objects.select_related("pool")
            .filter(enabled=True, pool_id__in=candidate_allocations)
            .order_by("id")
        )
        net = ZERO
        for account in candidate_accounts:
            snapshot = latest_snapshot(candidate, account)
            if snapshot is None:
                return config.safety_factor
            net += _pool_source_values(
                snapshot,
                config,
                candidate_allocations[account.pool_id],
            )["point"]
        if net > ZERO:
            remaining_ids.append(candidate.id)
    return (
        Decimal("1")
        if remaining_ids == [participant.id]
        else config.safety_factor
    )


def _allocate_contributions(
    sources: list[dict],
    *,
    net_key: str,
    output_key: str,
    total: Decimal,
) -> None:
    positive = [
        (source, max(ZERO, source[net_key]))
        for source in sources
        if source[net_key] is not None
    ]
    positive_total = sum((value for _source, value in positive), ZERO)
    if positive_total <= ZERO or total <= ZERO:
        for source, _value in positive:
            source[output_key] = ZERO
        return
    allocated = ZERO
    for index, (source, value) in enumerate(positive):
        contribution = (
            total - allocated
            if index == len(positive) - 1
            else (total * value / positive_total).quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
        )
        source[output_key] = contribution
        allocated += contribution


def _current_projection_applied(
    participant: Participant,
    source_snapshots: list[ParticipantSnapshot],
    sources: list[dict],
    recommended: Decimal,
    balance: Decimal | None,
) -> bool:
    if (
        not source_snapshots
        or not all(
            snapshot.recommendation_applied
            for snapshot in source_snapshots
        )
        or balance is None
    ):
        return False
    operation = (
        ParticipantBalanceOperation.objects.prefetch_related("sources")
        .filter(
            participant=participant,
            sub2api_user_id=participant.sub2api_user_id,
            state="committed",
        )
        .order_by("-committed_at", "-id")
        .first()
    )
    if (
        operation is None
        or operation.requested_balance_usd != recommended
        or operation.confirmed_balance_usd != balance
    ):
        return False
    snapshot_by_account = {
        snapshot.observation.account_id: snapshot.id
        for snapshot in source_snapshots
    }
    expected = {
        (
            int(source["external_account_id"]),
            snapshot_by_account[int(source["external_account_id"])],
            Decimal(str(source["contract_share_percent"])),
        )
        for source in sources
        if source["snapshot"] is not None
    }
    actual = {
        (
            source.account_external_id,
            source.snapshot_id,
            source.share_percent,
        )
        for source in operation.sources.all()
    }
    return actual == expected


def aggregate_recommendation(
    participant: Participant,
    config: AppSettings,
) -> tuple[dict | None, list[ParticipantSnapshot]]:
    """Sum the participant's current pool contracts into one global balance."""
    allocations = list(
        PoolParticipant.objects.select_related("pool")
        .filter(
            participant=participant,
            share_percent__gt=ZERO,
            pool__accounts__enabled=True,
        )
        .distinct()
        .order_by("pool__name", "pool_id")
    )
    allocation_by_pool_id = {
        allocation.pool_id: allocation for allocation in allocations
    }
    accounts = list(
        MonitoredAccount.objects.select_related("pool")
        .filter(
            enabled=True,
            pool_id__in=allocation_by_pool_id,
        )
        .order_by("pool__name", "pool_id", "name", "external_account_id")
    )
    if not participant.enabled or not accounts:
        return None, []

    pool_contracts = [
        {
            "pool_id": allocation.pool_id,
            "pool_name": allocation.pool.name,
            "share_percent": float(allocation.share_percent),
            "account_count": sum(
                1 for account in accounts if account.pool_id == allocation.pool_id
            ),
        }
        for allocation in allocations
    ]
    sources: list[dict] = []
    source_snapshots: list[ParticipantSnapshot] = []
    complete = True
    net_point = ZERO
    net_lower = ZERO
    net_upper = ZERO
    selected_cost = ZERO
    weighted_charged = ZERO
    total_capacity = ZERO
    for account in accounts:
        allocation = allocation_by_pool_id[account.pool_id]
        snapshot = latest_snapshot(participant, account)
        displayed = (
            _display_snapshot_data(snapshot, config)
            if snapshot is not None
            else None
        )
        source = {
            "account_id": account.id,
            "external_account_id": account.external_account_id,
            "account_name": account.name,
            "pool_id": account.pool_id,
            "pool_name": account.pool.name,
            "pool_contract_revision": account.pool.contract_revision,
            "contract_share_percent": float(allocation.share_percent),
            "snapshot": displayed,
            "net_position_usd": None,
            "net_position_min_usd": None,
            "net_position_max_usd": None,
            "contribution_usd": None,
            "contribution_min_usd": None,
            "contribution_max_usd": None,
        }
        if snapshot is None or displayed is None:
            complete = False
            sources.append(source)
            continue
        source_snapshots.append(snapshot)
        values = _pool_source_values(
            snapshot,
            config,
            allocation.share_percent,
        )
        source["net_position_usd"] = values["point"]
        source["net_position_min_usd"] = values["lower"]
        source["net_position_max_usd"] = values["upper"]
        net_point += values["point"]
        net_lower += values["lower"]
        net_upper += values["upper"]
        selected_cost += Decimal(str(displayed["selected_cost"]))
        weighted_charged += values["charged"] * values["capacity_point"]
        total_capacity += values["capacity_point"]
        sources.append(source)

    safety_factor = _pooled_safety_factor(participant, accounts, config)
    recommended = (
        max(ZERO, net_point) * safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    recommended_min = (
        max(ZERO, net_lower) * safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    recommended_max = (
        max(ZERO, net_upper) * safety_factor
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    recommended = min(recommended_max, max(recommended_min, recommended))
    if complete:
        _allocate_contributions(
            sources,
            net_key="net_position_usd",
            output_key="contribution_usd",
            total=recommended,
        )
        _allocate_contributions(
            sources,
            net_key="net_position_min_usd",
            output_key="contribution_min_usd",
            total=recommended_min,
        )
        _allocate_contributions(
            sources,
            net_key="net_position_max_usd",
            output_key="contribution_max_usd",
            total=recommended_max,
        )

    balance = participant.latest_balance_usd
    difference = None
    if complete and balance is not None:
        if balance < recommended_min:
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
    applied = bool(
        complete
        and _current_projection_applied(
            participant,
            source_snapshots,
            sources,
            recommended,
            balance,
        )
    )
    exhausted = bool(
        balance is not None and balance <= config.limit_warning_usd
    )
    needs_update = bool(
        complete
        and not applied
        and difference is not None
        and (
            abs(difference) >= config.recommendation_change_usd
            or (exhausted and recommended_max > ZERO)
        )
    )
    pooled_overused = bool(complete and net_upper < ZERO)
    charged_percent = (
        weighted_charged / total_capacity
        if complete and total_capacity > ZERO
        else ZERO
    )
    if not complete:
        reason = "至少一个已分配账号尚无当前用户的可用观测，已阻止全局余额调整"
    elif applied:
        reason = "该分配方案的建议已经应用"
    elif pooled_overused:
        reason = "参与者在所有已分配池合计后已确认超出合同权益，建议清零全局余额"
    elif needs_update:
        reason = "全局余额与所有已分配池的剩余权益区间差异较大"
    else:
        reason = "全局余额处于所有已分配池的合计建议区间内，无需调整"

    for source in sources:
        for key in (
            "net_position_usd",
            "net_position_min_usd",
            "net_position_max_usd",
            "contribution_usd",
            "contribution_min_usd",
            "contribution_max_usd",
        ):
            if source[key] is not None:
                source[key] = float(source[key])
    return (
        {
            "participant_id": participant.id,
            "participant_name": participant.name,
            "pool_allocations": pool_contracts,
            "selected_cost": float(selected_cost),
            "charged_cycle_percent": float(charged_percent),
            "current_balance_usd": float(balance) if balance is not None else None,
            "recommended_balance_usd": (
                float(recommended) if complete else None
            ),
            "recommended_balance_min_usd": (
                float(recommended_min) if complete else None
            ),
            "recommended_balance_max_usd": (
                float(recommended_max) if complete else None
            ),
            "balance_difference_usd": (
                float(difference) if difference is not None else None
            ),
            "is_overused": pooled_overused,
            "needs_manual_update": needs_update,
            "recommendation_applied": applied,
            "recommendation_complete": complete,
            "account_count": len(accounts),
            "pool_count": len(allocations),
            "reason": reason,
            "allocation_model": "partitioned_pool_sum",
            "sources": sources,
        },
        source_snapshots,
    )


def display_recommendation(
    participant: Participant,
    config: AppSettings,
) -> tuple[list[ParticipantSnapshot], Decimal | None]:
    """Return all source snapshots and the global pooled balance recommendation."""
    aggregate, snapshots = aggregate_recommendation(participant, config)
    if aggregate is None or aggregate["recommended_balance_usd"] is None:
        return snapshots, None
    return snapshots, Decimal(str(aggregate["recommended_balance_usd"]))


def participant_data(
    participant: Participant,
    config: AppSettings | None = None,
) -> dict:
    """Generate one participant identity plus its pool-specific contracts."""
    config = config or AppSettings.load()
    aggregate, _snapshots = aggregate_recommendation(participant, config)
    usage_by_account = {
        usage.account_id: usage
        for usage in participant.account_memberships.select_related(
            "account",
            "participant",
        )
    }
    allocations = list(
        participant.pool_allocations.select_related("pool").filter(
            share_percent__gt=ZERO
        )
    )
    allocation_by_pool_id = {
        allocation.pool_id: allocation for allocation in allocations
    }
    accounts = list(
        MonitoredAccount.objects.select_related("pool").order_by(
            "pool__name",
            "pool_id",
            "name",
            "external_account_id",
        )
    )
    account_breakdowns = []
    for account in accounts:
        row, _snapshot = _account_breakdown_data(
            participant,
            account,
            allocation_by_pool_id.get(account.pool_id),
            usage_by_account.get(account.id),
            config,
        )
        account_breakdowns.append(row)
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
        "pool_allocations": [
            {
                "pool_id": allocation.pool_id,
                "pool_name": allocation.pool.name,
                "share_percent": float(allocation.share_percent),
                "account_ids": [
                    account.id
                    for account in accounts
                    if account.pool_id == allocation.pool_id
                ],
            }
            for allocation in allocations
        ],
        "is_owner": participant.is_owner,
        "enabled": participant.enabled,
        "notes": participant.notes,
        "latest_balance_usd": (
            float(participant.latest_balance_usd)
            if participant.latest_balance_usd is not None
            else None
        ),
        "last_checked_at": iso(participant.last_checked_at),
        "account_breakdowns": account_breakdowns,
        "snapshot": aggregate,
    }
