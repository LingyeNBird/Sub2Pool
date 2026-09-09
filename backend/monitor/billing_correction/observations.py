"""Read-time interval summaries with an explicit legacy-evidence fallback."""

from dataclasses import dataclass, field
from decimal import Decimal

from .domain import BillingCorrectionRules, CorrectionAmounts
from .facts import validate_capture
from .rules import corrections_digest, observation_correction_config

ZERO = Decimal("0")


@dataclass
class UserCorrection:
    user_id: int
    request_count: int | None = 0
    fast_request_count: int = 0
    raw_cost: Decimal = ZERO
    fast_raw_cost: Decimal = ZERO
    actual_cost: Decimal = ZERO
    amounts: CorrectionAmounts = field(default_factory=CorrectionAmounts)
    unknown_long_context_request_count: int = 0


@dataclass
class IntervalCorrection:
    amounts: CorrectionAmounts = field(default_factory=CorrectionAmounts)
    users: dict[int, UserCorrection] = field(default_factory=dict)
    facts_complete: bool = False
    legacy_fast_only: bool = False
    calculated: bool = False
    unknown_long_context_request_count: int = 0
    missing_model_request_count: int = 0
    raw_cost: Decimal | None = None
    actual_cost: Decimal | None = None
    request_count: int | None = None
    model_details: list[dict] = field(default_factory=list)

    def payload(self) -> dict:
        return {
            **self.amounts.payload(),
            "correction_calculated": self.calculated,
            "correction_facts_complete": self.facts_complete,
            "legacy_fast_only": self.legacy_fast_only,
            "unknown_long_context_request_count": self.unknown_long_context_request_count,
            "missing_model_request_count": self.missing_model_request_count,
        }


def interval_corrections(
    observation,
    config,
    *,
    include_models=False,
    started_at=None,
    ended_at=None,
    rules_cache=None,
) -> IntervalCorrection:
    result = IntervalCorrection()
    if observation.account_id < 0:
        return result  # CPA has its own local pricing; never apply Sub2API policies.

    if observation.correction_source not in {"local", "upstream", "none"}:
        raise ValueError("未知修正来源")
    local = observation.correction_source == "local"
    correction_config = observation_correction_config(observation) if local else None
    capture = getattr(observation, "billing_capture", None)
    if capture is None:
        result.request_count = observation.fast_correction_request_count
        if not local:
            # Upstream/none intervals have a known zero local correction.  Keep
            # aggregate raw costs usable even when their request capture failed.
            result.calculated = True
            result.raw_cost = observation.interval_cost(config.cost_basis)
            result.actual_cost = observation.interval_cost("actual")
            return result
        actual = config.cost_basis == "actual"
        value = (
            observation.fast_correction_actual_cost
            if actual
            else observation.fast_correction_standard_cost
        )
        # A frozen FAST subtotal does not prove the two new corrections were
        # calculated. Keep that subtotal readable, but offer targeted backfill.
        result.legacy_fast_only = (
            observation.fast_correction_actual_cost is not None
            and observation.fast_correction_standard_cost is not None
        )
        result.amounts = CorrectionAmounts(fast=value or ZERO)
        for row in observation.fast_corrections.all():
            result.users[row.sub2api_user_id] = UserCorrection(
                user_id=row.sub2api_user_id,
                request_count=row.request_count,
                fast_request_count=row.fast_request_count,
                fast_raw_cost=(
                    row.fast_actual_cost if actual else row.fast_standard_cost
                ),
                amounts=CorrectionAmounts(
                    fast=(
                        row.actual_correction_cost
                        if actual
                        else row.standard_correction_cost
                    )
                ),
            )
        return result

    rules = None
    if local:
        cache_key = corrections_digest(correction_config)
        if rules_cache is None:
            rules = BillingCorrectionRules(correction_config)
        else:
            rules = rules_cache.get(cache_key)
            if rules is None:
                rules = BillingCorrectionRules(correction_config)
                rules_cache[cache_key] = rules
    result.calculated = result.facts_complete = True
    result.raw_cost = ZERO
    result.actual_cost = ZERO
    result.request_count = capture.request_count
    model_rows = {}
    facts = list(capture.facts.all())
    validate_capture(capture, observation, facts)
    for fact in facts:
        if started_at is not None and fact.created_at < started_at:
            continue
        if ended_at is not None and fact.created_at >= ended_at:
            continue
        calculated = rules.calculate(fact, config.cost_basis) if rules is not None else None
        raw_cost = calculated.raw_cost if calculated is not None else Decimal(
            fact.actual_cost if config.cost_basis == "actual" else fact.total_cost
        )
        unknown = int(calculated.long_context_unknown) if calculated is not None else 0
        if calculated is not None:
            result.amounts += calculated.amounts
        result.raw_cost += raw_cost
        actual_cost = Decimal(fact.actual_cost)
        result.actual_cost += actual_cost
        result.unknown_long_context_request_count += unknown
        result.missing_model_request_count += int(not fact.model)
        user = result.users.setdefault(
            fact.user_id,
            UserCorrection(user_id=fact.user_id),
        )
        user.request_count += 1
        user.raw_cost += raw_cost
        user.actual_cost += actual_cost
        if calculated is not None:
            user.amounts += calculated.amounts
        user.unknown_long_context_request_count += unknown
        if fact.service_tier.strip().casefold() in {"priority", "fast"}:
            user.fast_request_count += 1
            user.fast_raw_cost += raw_cost
        if include_models and calculated is not None:
            key = (
                fact.model,
                fact.service_tier,
                str(calculated.fast_factor),
                str(calculated.long_context_factor),
                str(calculated.model_factor),
                calculated.long_context_evidence,
            )
            row = model_rows.setdefault(
                key,
                {
                    "request_count": 0,
                    "raw_cost": ZERO,
                    "amounts": CorrectionAmounts(),
                },
            )
            row["request_count"] += 1
            row["raw_cost"] += calculated.raw_cost
            row["amounts"] += calculated.amounts
    for key, row in sorted(model_rows.items()):
        result.model_details.append(
            {
                "model": key[0],
                "service_tier": key[1],
                "fast_factor": key[2],
                "long_context_factor": key[3],
                "model_factor": key[4],
                "long_context_evidence": key[5],
                "request_count": row["request_count"],
                "raw_cost_usd": float(row["raw_cost"]),
                "corrected_cost_usd": float(
                    row["raw_cost"] + row["amounts"].total
                ),
                **row["amounts"].payload(),
            }
        )
    return result
