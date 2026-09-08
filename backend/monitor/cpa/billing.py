"""Read-time, replayable billing projections. Never writes source facts or balances."""

from bisect import bisect_left, bisect_right
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from statistics import median
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.db.models import Q

from ..access import visible_accounts_for
from ..accounting.boundaries import same_official_reset
from ..models import CPAUsageEvent, Observation
from ..reporting.recommendations import _capacity_values
from .capacity_estimate import particle_capacity_estimate
from .participants import coverage_data, event_owner
from .usage import cpa_event_cost

ZERO = Decimal(0)


def billing_bounds(anchor, zone, now):
    """Retain the original anchor day after short months; compare UTC durations."""
    tz = ZoneInfo(zone)
    local = now.astimezone(tz)

    def boundary(y, m):
        return datetime(
            y, m, min(anchor.day, monthrange(y, m)[1]), tzinfo=tz
        ).astimezone(dt_timezone.utc)

    anchor_start = datetime.combine(anchor, datetime.min.time(), tz).astimezone(
        dt_timezone.utc
    )
    if now < anchor_start:
        next_month = anchor.year * 12 + anchor.month
        return anchor_start, boundary(next_month // 12, next_month % 12 + 1)

    index = local.year * 12 + local.month - 1
    start = boundary(index // 12, index % 12 + 1)
    if now < start:
        index -= 1
        start = boundary(index // 12, index % 12 + 1)
    return max(
        start,
        datetime.combine(anchor, datetime.min.time(), tz).astimezone(dt_timezone.utc),
    ), boundary((index + 1) // 12, (index + 1) % 12 + 1)


def fraction(start, end, left, right):
    return Decimal(
        str(max(0, (min(end, right) - max(start, left)).total_seconds()))
    ) / Decimal(str((end - start).total_seconds()))


def cycle_rows(account, config, now):
    groups = []
    for obs in Observation.objects.filter(
        account_id=account.fact_key, excluded_at__isnull=True, observed_at__lte=now
    ).order_by("observed_at", "id"):
        if not obs.upstream_resets_at or not obs.window_seconds:
            continue
        if not groups or not same_official_reset(
            groups[-1][-1].upstream_resets_at, obs.upstream_resets_at
        ):
            groups.append([])
        groups[-1].append(obs)
    rows = []
    for group in groups:
        latest = group[-1]
        start = latest.upstream_resets_at - timedelta(seconds=latest.window_seconds)
        reliable = None
        # Prefix sums price each request once per cycle, even when many stale
        # observations are skipped after a late event or a catalog adjustment.
        times, amounts, unknowns = [], [ZERO], [0]
        if any(o.valid_sample or particle_capacity_estimate(o) for o in group):
            for event in (
                CPAUsageEvent.objects.filter(
                    account=account,
                    occurred_at__gte=start,
                    occurred_at__lte=latest.observed_at,
                )
                .order_by("occurred_at", "id")
                .iterator(chunk_size=1000)
            ):
                cost, unknown = cpa_event_cost(event, config)
                times.append(event.occurred_at)
                amounts.append(amounts[-1] + cost)
                unknowns.append(unknowns[-1] + int(unknown))
        details = None
        for obs in reversed(group):
            posterior = particle_capacity_estimate(obs)
            if not posterior and not (
                obs.valid_sample
                and obs.interval_used_percent == obs.upstream_used_percent
            ):
                continue
            index = bisect_right(times, obs.observed_at)
            base = 0
            if posterior and obs.attribution_started_at:
                reason = obs.raw_window.get("replay_segment_reason")
                observed_baseline = (
                    reason
                    in {
                        "manual_override",
                        "official_zero_observation",
                        "provider_collection_baseline",
                    }
                    or obs.interval_used_percent != obs.upstream_used_percent
                )
                base = (bisect_right if observed_baseline else bisect_left)(
                    times, obs.attribution_started_at
                )
            if unknowns[index] - unknowns[base] or abs(
                amounts[index] - amounts[base] - obs.selected_total_cost
            ) > Decimal("0.00001"):
                continue
            if posterior and config.weekly_quota_model != "constant_average":
                # Already computed by the same deterministic filter as the trajectory
                # page. Do not reject it because the official week has collection gaps.
                capacity = Decimal(str(posterior["capacity_usd"]))
                details = posterior
            else:
                if posterior:
                    # The average model also uses only the valid connected segment.
                    if not obs.valid_sample or obs.interval_used_percent <= 0:
                        continue
                elif not coverage_data(account, start, obs.observed_at)["complete"]:
                    continue
                snap = SimpleNamespace(
                    observation=obs,
                    selected_cost=ZERO,
                    charged_cycle_percent=ZERO,
                    charged_percent_lower=None,
                    charged_percent_upper=None,
                )
                capacity = _capacity_values(snap, config)[0]
                details = dict(
                    source="quota_model",
                    capacity_usd=float(capacity),
                    lower_usd=None,
                    upper_usd=None,
                    prior_only=False,
                    as_of=obs.observed_at.isoformat(),
                )
            if capacity > 0:
                reliable = (obs, capacity)
                break
        rows.append(
            dict(
                start=start,
                end=latest.upstream_resets_at,
                natural_end=latest.upstream_resets_at,
                window=latest.window_seconds,
                observation=latest,
                reliable=reliable,
                capacity_estimate=details if reliable else None,
            )
        )
    for i, row in enumerate(rows[:-1]):
        row["end"] = min(row["end"], rows[i + 1]["start"])
    return [r for r in rows if r["end"] > r["start"]]


def contract_at(contracts, at):
    return next((c for c in reversed(contracts) if c.effective_at <= at), None)


def billing_summary(user, account, config, now, bindings, members):
    pool = account.pool
    result = dict(
        configured=bool(pool.cpa_billing_anchor),
        anchor_date=str(pool.cpa_billing_anchor) if pool.cpa_billing_anchor else None,
        timezone=pool.cpa_billing_timezone,
        started_at=None,
        ended_at=None,
        generated_at=now.isoformat(),
        capacity_usd=None,
        actual_capacity_usd=None,
        future_capacity_usd=None,
        expired_usd=None,
        available_usd=None,
        usage_usd=0.0,
        unattributed_usd=0.0,
        other_members_usd=0.0,
        unallocated_usd=None,
        reasons=[],
        cycles=[],
        members=[],
    )
    if not pool.cpa_billing_anchor:
        return result
    start, end = billing_bounds(pool.cpa_billing_anchor, pool.cpa_billing_timezone, now)
    result.update(started_at=start.isoformat(), ended_at=end.isoformat())
    if start >= end or start > now:
        result["reasons"] = ["账期尚未开始"]
        return result
    accounts = (
        visible_accounts_for(user)
        .filter(provider="cpa")
        .filter(Q(pool=pool) | Q(cpa_contracts__pool_id_at_capture=pool.id))
        .distinct()
    )
    usage, entitlement, completed_usage, completed_entitlement = (
        defaultdict(lambda: ZERO) for _ in range(4)
    )
    actual = future = expired = available = reserve = ZERO
    future_known = True
    capacity_known = True
    contracts_known = True
    reasons = set()
    for selected in accounts:
        contracts = list(selected.cpa_contracts.order_by("effective_at", "id"))
        cycles = cycle_rows(selected, config, now)
        event_start = min([start, *[r["start"] for r in cycles if r["end"] > start]])
        events = list(
            CPAUsageEvent.objects.filter(
                account=selected,
                occurred_at__gte=event_start,
                occurred_at__lt=min(now, end),
            ).order_by("occurred_at", "id")
        )
        costs = [(e, *cpa_event_cost(e, config)) for e in events]
        current = next(
            (r for r in reversed(cycles) if r["start"] <= now < r["end"]), None
        )
        reliable_past = [
            r["reliable"][1] for r in cycles if r["end"] <= now and r["reliable"]
        ][-3:]
        prediction = (
            current["reliable"][1]
            if current and current["reliable"]
            else (median(reliable_past) if reliable_past else None)
        )
        prediction_as_of = (
            current["reliable"][0].observed_at
            if current and current["reliable"]
            else next(
                (
                    r["reliable"][0].observed_at
                    for r in reversed(cycles)
                    if r["end"] <= now and r["reliable"]
                ),
                None,
            )
        )
        # A stale observation cannot establish a new reset boundary.
        if current:
            cursor = current["end"]
            while cursor < end:
                cycles.append(
                    dict(
                        start=cursor,
                        end=cursor + timedelta(seconds=current["window"]),
                        window=current["window"],
                        reliable=None,
                        prediction=prediction,
                        prediction_as_of=prediction_as_of,
                        observation=None,
                    )
                )
                cursor = cycles[-1]["end"]
        relevant_intervals = []
        for row in cycles:
            left, right = max(start, row["start"]), min(end, row["end"])
            if left >= right:
                continue
            predicted = "prediction" in row
            cuts = sorted(
                {
                    left,
                    right,
                    *[
                        c.effective_at
                        for c in contracts
                        if left < c.effective_at < right
                    ],
                    *([now] if left < now < right else []),
                }
            )
            segments = []
            for a, b in zip(cuts, cuts[1:]):
                contract = contract_at(contracts, min(a, now))
                if contract and contract.pool_id_at_capture != pool.id:
                    continue
                if contract is None:
                    # Only current-pool accounts can contribute unknown prehistory.
                    if selected.pool_id != pool.id:
                        continue
                    reasons.add("缺少历史份额或账号所属池记录")
                    contracts_known = False
                segments.append((a, b, contract))
                relevant_intervals.append((a, b))
            if not segments:
                continue
            reliable = row["reliable"]
            capacity = (
                row.get("prediction")
                if predicted
                else (reliable[1] if reliable else None)
            )
            observed = reliable[0] if reliable else None
            row_reasons = []
            if predicted and capacity is None:
                future_known = False
            if capacity is None:
                capacity_known = False
                row_reasons.append("缺少可靠周期容量")
            cycle_costs = [
                (e, cost, unknown)
                for e, cost, unknown in costs
                if row["start"] <= e.occurred_at < min(row["end"], now)
            ]
            if not predicted:
                if row["observation"].observed_at > row["end"]:
                    capacity_known = False
                    row_reasons.append("周期边界与观测时间冲突")
                if not coverage_data(selected, row["start"], min(row["end"], now))[
                    "complete"
                ]:
                    row_reasons.append("采集覆盖不完整")
                if any(unknown for _, _, unknown in cycle_costs):
                    row_reasons.append("请求缺少模型价格")
            weight = sum(
                (fraction(row["start"], row["end"], a, b) for a, b, _ in segments), ZERO
            )
            amount = capacity * weight if capacity is not None else None
            closed = row["end"] <= now
            full_used = sum((cost for _, cost, _ in cycle_costs), ZERO)
            loss = (
                max(ZERO, capacity - full_used) * weight
                if closed and capacity is not None and not row_reasons
                else None
            )
            spendable = (
                max(ZERO, capacity - full_used) * weight
                if not closed
                and not predicted
                and capacity is not None
                and not row_reasons
                else None
            )
            result["cycles"].append(
                dict(
                    account_id=selected.id,
                    account_name=selected.name,
                    started_at=row["start"].isoformat(),
                    ended_at=row["end"].isoformat(),
                    kind="future"
                    if predicted
                    else ("historical" if closed else "current"),
                    capacity_usd=float(amount) if amount is not None else None,
                    full_capacity_usd=float(capacity) if capacity is not None else None,
                    expired_usd=float(loss) if loss is not None else None,
                    quota_as_of=observed.observed_at.isoformat()
                    if observed
                    else (
                        row["prediction_as_of"].isoformat()
                        if predicted and row["prediction_as_of"]
                        else None
                    ),
                    capacity_estimate=row.get("capacity_estimate"),
                    reasons=row_reasons,
                )
            )
            reasons.update(row_reasons)
            if amount is not None:
                if predicted:
                    future += amount
                    available += amount
                else:
                    actual += amount
                expired += loss or ZERO
                available += spendable or ZERO
                for a, b, contract in segments:
                    if contract is None:
                        continue
                    piece = capacity * fraction(row["start"], row["end"], a, b)
                    allocated = ZERO
                    for allocation in contract.allocations:
                        pk = int(allocation["participant_id"])
                        value = piece * Decimal(str(allocation["share_percent"])) / 100
                        entitlement[pk] += value
                        if closed:
                            completed_entitlement[pk] += value
                        allocated += value
                    reserve += max(ZERO, piece - allocated)
        # Detect missing actual cycle boundaries, including unobserved reset periods.
        cursor = start
        scope_segments = []
        cuts = sorted(
            {
                start,
                min(now, end),
                *[
                    c.effective_at
                    for c in contracts
                    if start < c.effective_at < min(now, end)
                ],
            }
        )
        for a, b in zip(cuts, cuts[1:]):
            c = contract_at(contracts, a)
            if (c and c.pool_id_at_capture == pool.id) or (
                c is None and selected.pool_id == pool.id
            ):
                scope_segments.append((a, b))
        for a, b in scope_segments:
            cursor = a
            for x, y in sorted(relevant_intervals):
                if y <= cursor or x >= b:
                    continue
                if x > cursor:
                    reasons.add("周期边界不完整")
                    capacity_known = False
                cursor = max(cursor, y)
            if cursor < b:
                reasons.add("周期边界不完整")
                capacity_known = False
        if selected.pool_id == pool.id and not current:
            reasons.add("等待当前周期观测，无法预测后续自然周期")
            future_known = False
            capacity_known = False
        for e, cost, unknown in costs:
            if not start <= e.occurred_at < min(end, now):
                continue
            contract = contract_at(contracts, e.occurred_at)
            if contract and contract.pool_id_at_capture != pool.id:
                continue
            if contract is None:
                if selected.pool_id != pool.id:
                    continue
                reasons.add("缺少历史份额或账号所属池记录")
                contracts_known = False
            if unknown:
                reasons.add("请求缺少模型价格")
            owner = event_owner(e, bindings)
            usage[owner] += cost
            if any(r["start"] <= e.occurred_at < r["end"] <= now for r in cycles):
                completed_usage[owner] += cost
    known = not reasons
    total = actual + future
    result.update(
        capacity_usd=float(total) if capacity_known and contracts_known else None,
        actual_capacity_usd=float(actual),
        future_capacity_usd=float(future) if future_known else None,
        expired_usd=float(expired) if known else None,
        available_usd=float(available) if known else None,
        usage_usd=float(sum(usage.values(), ZERO)),
        unattributed_usd=float(usage[None]),
        other_members_usd=float(
            sum(
                (v for k, v in usage.items() if k is not None and k not in members),
                ZERO,
            )
        ),
        unallocated_usd=float(reserve) if known else None,
        reasons=sorted(reasons),
    )
    remaining = {pk: max(ZERO, entitlement[pk] - usage[pk]) for pk in members}
    denominator = (
        sum(remaining.values(), ZERO)
        + reserve
        + sum(
            (
                max(ZERO, value - usage[pk])
                for pk, value in entitlement.items()
                if pk not in members
            ),
            ZERO,
        )
    )
    for pk in members:
        rest = entitlement[pk] - usage[pk]
        recommended = (
            min(available, denominator) * remaining[pk] / denominator
            if denominator
            else ZERO
        )
        result["members"].append(
            dict(
                participant_id=pk,
                usage_usd=float(usage[pk]),
                usage_percent=float(usage[pk] / total * 100)
                if capacity_known and contracts_known and total
                else None,
                entitlement_usd=float(entitlement[pk])
                if capacity_known and contracts_known
                else None,
                remaining_usd=float(rest) if known else None,
                recommended_usd=float(recommended) if known else None,
                recommended_percent=float(recommended / available * 100)
                if known and available
                else None,
                completed_overuse_usd=float(
                    max(ZERO, completed_usage[pk] - completed_entitlement[pk])
                )
                if known
                else None,
                projected_overuse=rest < 0 if known else None,
            )
        )
    return result


def weekly_distribution(accounts, members, config, now, bindings):
    from .reporting import account_summary

    output = []
    for account in accounts:
        obs, start, usage, total, coverage, _, latest_request = account_summary(
            account, config, now, bindings
        )
        cycles = cycle_rows(account, config, now)
        current = next(
            (r for r in reversed(cycles) if r["start"] <= now < r["end"]), None
        )
        reliable = current["reliable"] if current else None
        capacity = reliable[1] if reliable else None
        complete = bool(
            current
            and coverage_data(account, current["start"], now)["complete"]
            and not total["unpriced_request_count"]
        )
        output.append(
            dict(
                account_id=account.id,
                account_name=account.name,
                started_at=start.isoformat(),
                resets_at=obs.upstream_resets_at.isoformat() if obs else None,
                quota_as_of=reliable[0].observed_at.isoformat() if reliable else None,
                requests_as_of=latest_request.isoformat() if latest_request else None,
                capacity_usd=float(capacity) if capacity is not None else None,
                capacity_estimate=current.get("capacity_estimate") if current else None,
                coverage_complete=complete,
                remaining_usd=float(max(ZERO, capacity - total["usage_usd"]))
                if capacity is not None and complete
                else None,
                upstream_remaining_percent=float(
                    max(ZERO, 100 - obs.upstream_used_percent)
                )
                if obs and obs.upstream_resets_at > now
                else None,
                usage_usd=float(total["usage_usd"]),
                unpriced_request_count=total["unpriced_request_count"],
                unattributed_usd=float(usage[None]["usage_usd"]),
                other_members_usd=float(
                    sum(
                        (
                            v["usage_usd"]
                            for k, v in usage.items()
                            if k is not None and k not in members
                        ),
                        ZERO,
                    )
                ),
                members=[
                    dict(
                        participant_id=pk,
                        usage_usd=float(usage[pk]["usage_usd"]),
                        usage_percent=float(usage[pk]["usage_usd"] / capacity * 100)
                        if capacity
                        else None,
                    )
                    for pk in members
                ],
            )
        )
    return output
