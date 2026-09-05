"""Read-time interval summaries with an explicit legacy-evidence fallback."""

from dataclasses import dataclass, field
from decimal import Decimal

from .domain import BillingCorrectionRules, CorrectionAmounts
from .facts import validate_capture

ZERO = Decimal("0")


@dataclass
class UserCorrection:
    user_id: int
    request_count: int | None = 0
    fast_request_count: int = 0
    raw_cost: Decimal = ZERO
    fast_raw_cost: Decimal = ZERO
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


def interval_corrections(observation, config, *, rules=None, include_models=False, started_at=None, ended_at=None) -> IntervalCorrection:
    result = IntervalCorrection()
    if observation.account_id < 0:
        return result  # CPA has its own local pricing; never apply Sub2API policies.
    capture = getattr(observation, "billing_capture", None)
    if capture is None:
        actual = config.cost_basis == "actual"
        value = observation.fast_correction_actual_cost if actual else observation.fast_correction_standard_cost
        result.calculated = observation.fast_correction_actual_cost is not None and observation.fast_correction_standard_cost is not None
        result.legacy_fast_only = result.calculated
        result.amounts = CorrectionAmounts(fast=value or ZERO)
        result.request_count = observation.fast_correction_request_count
        for row in observation.fast_corrections.all():
            result.users[row.sub2api_user_id] = UserCorrection(
                user_id=row.sub2api_user_id, request_count=row.request_count,
                fast_request_count=row.fast_request_count,
                fast_raw_cost=row.fast_actual_cost if actual else row.fast_standard_cost,
                amounts=CorrectionAmounts(fast=row.actual_correction_cost if actual else row.standard_correction_cost),
            )
        return result
    rules = rules or BillingCorrectionRules(config)
    result.calculated = result.facts_complete = True
    result.raw_cost = ZERO
    result.request_count = capture.request_count
    model_rows = {}
    facts = list(capture.facts.all())
    validate_capture(capture, observation, facts)
    for fact in facts:
        if started_at is not None and fact.created_at < started_at:
            continue
        if ended_at is not None and fact.created_at >= ended_at:
            continue
        calculated = rules.calculate(fact, config.cost_basis)
        result.amounts += calculated.amounts
        result.raw_cost += calculated.raw_cost
        result.unknown_long_context_request_count += int(calculated.long_context_unknown)
        result.missing_model_request_count += int(not fact.model)
        user = result.users.setdefault(fact.user_id, UserCorrection(user_id=fact.user_id))
        user.request_count += 1
        user.raw_cost += calculated.raw_cost
        user.amounts += calculated.amounts
        user.unknown_long_context_request_count += int(calculated.long_context_unknown)
        if fact.service_tier.strip().casefold() in {"priority", "fast"}:
            user.fast_request_count += 1
            user.fast_raw_cost += calculated.raw_cost
        if include_models:
            key = (fact.model, fact.service_tier, str(calculated.fast_factor), str(calculated.long_context_factor), str(calculated.model_factor), calculated.long_context_evidence)
            row = model_rows.setdefault(key, {"request_count": 0, "raw_cost": ZERO, "amounts": CorrectionAmounts()})
            row["request_count"] += 1
            row["raw_cost"] += calculated.raw_cost
            row["amounts"] += calculated.amounts
    for key, row in sorted(model_rows.items()):
        result.model_details.append({
            "model": key[0], "service_tier": key[1], "fast_factor": key[2],
            "long_context_factor": key[3], "model_factor": key[4],
            "long_context_evidence": key[5], "request_count": row["request_count"],
            "raw_cost_usd": float(row["raw_cost"]),
            "corrected_cost_usd": float(row["raw_cost"] + row["amounts"].total),
            **row["amounts"].payload(),
        })
    return result
