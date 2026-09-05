"""Pure calculation from request facts. Never infer a correction from a prior total."""

from dataclasses import dataclass
from decimal import Decimal

from ..fast_correction.domain import money
from ..fast_correction.rules import FastCorrectionRuleSet
from .rules import (
    compile_rules, first_match, normalize_long_context_correction_rules,
    normalize_model_correction_rules,
)

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class CorrectionAmounts:
    fast: Decimal = ZERO
    long_context: Decimal = ZERO
    model: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.fast + self.long_context + self.model

    def __add__(self, other: "CorrectionAmounts") -> "CorrectionAmounts":
        return CorrectionAmounts(self.fast + other.fast, self.long_context + other.long_context, self.model + other.model)

    def __sub__(self, other: "CorrectionAmounts") -> "CorrectionAmounts":
        return CorrectionAmounts(self.fast - other.fast, self.long_context - other.long_context, self.model - other.model)

    def payload(self) -> dict[str, float]:
        return {
            "fast_correction_usd": float(self.fast),
            "long_context_correction_usd": float(self.long_context),
            "model_correction_usd": float(self.model),
            "correction_total_usd": float(self.total),
        }


@dataclass(frozen=True)
class RequestCorrection:
    amounts: CorrectionAmounts
    raw_cost: Decimal
    corrected_cost: Decimal
    long_context_unknown: bool
    long_context_applied: bool
    fast_factor: Decimal
    long_context_factor: Decimal
    model_factor: Decimal
    long_context_evidence: str


class BillingCorrectionRules:
    """Compile once per read/replay; priority/fast -> long context -> model.

    Long-context false is authoritative. Missing flag falls back to the saved
    *total input* token count, never model name, output tokens or current cost.
    Each stage adjustment rounds to six decimals; differences telescope exactly.
    """

    def __init__(self, config):
        self.fast_enabled = config.fast_correction_enabled
        self.fast = FastCorrectionRuleSet(config.fast_correction_rules)
        self.long_rules = compile_rules(normalize_long_context_correction_rules(config.long_context_correction_rules)) if config.long_context_correction_enabled else ()
        self.model_rules = compile_rules(normalize_model_correction_rules(config.model_correction_rules)) if config.model_correction_enabled else ()

    def calculate(self, log, basis: str) -> RequestCorrection:
        raw = Decimal(str(log.selected(basis)))
        if not raw.is_finite() or raw < ZERO:
            raise ValueError("请求原始成本必须是非负有限数字")
        fast_factor = ONE
        if self.fast_enabled and str(log.service_tier).strip().casefold() in {"priority", "fast"}:
            fast_factor += self.fast.correction_factor_for_model(log.model)
        long_factor, model_factor = ONE, ONE
        unknown, applied, evidence = False, False, "not_matched"
        rule = first_match(self.long_rules, log.model)
        if rule is not None:
            flag = getattr(log, "long_context_billing_applied", None)
            if flag is not None:
                applied, evidence = bool(flag), "upstream_flag"
            else:
                tokens = [getattr(log, name, None) for name in ("input_tokens", "cache_creation_tokens", "cache_read_tokens")]
                known_total = sum(value for value in tokens if value is not None)
                if known_total > rule["threshold_tokens"] or all(value is not None for value in tokens):
                    applied = known_total > rule["threshold_tokens"]
                    evidence = "input_tokens_threshold"
                else:
                    unknown, evidence = True, "missing_input_facts"
            if rule["target_multiplier"] == rule["source_multiplier"]:
                unknown = False
            if applied:
                long_factor = Decimal(rule["target_multiplier"]) / Decimal(rule["source_multiplier"])
        rule = first_match(self.model_rules, log.model)
        if rule is not None:
            model_factor = Decimal(rule["multiplier"])
        after_fast = raw + money(raw * (fast_factor - ONE))
        after_long = after_fast + money(after_fast * (long_factor - ONE))
        final = after_long + money(after_long * (model_factor - ONE))
        return RequestCorrection(
            amounts=CorrectionAmounts(after_fast - raw, after_long - after_fast, final - after_long),
            raw_cost=raw, corrected_cost=final, long_context_unknown=unknown,
            long_context_applied=applied, fast_factor=fast_factor,
            long_context_factor=long_factor, model_factor=model_factor,
            long_context_evidence=evidence,
        )
