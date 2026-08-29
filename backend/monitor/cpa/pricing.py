"""Local CPA model-price snapshot and request cost calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError

MILLION = Decimal("1000000")

# Copied from Sub2API's bundled model-pricing catalog. Values are USD per
# million tokens. AppSettings stores a copy, so future releases never replace
# an operator's manual edits.
_CPA_MODEL_PRICING_SNAPSHOT: dict[str, dict[str, str]] = {
    "codex-auto-review": {"input": "0.2", "cached_input": "0.02", "output": "1.2"},
    "gpt-5": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-2025-08-07": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-chat": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-chat-latest": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-codex": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-mini": {"input": "0.25", "cached_input": "0.025", "output": "2"},
    "gpt-5-mini-2025-08-07": {"input": "0.25", "cached_input": "0.025", "output": "2"},
    "gpt-5-nano": {"input": "0.05", "cached_input": "0.005", "output": "0.4"},
    "gpt-5-nano-2025-08-07": {"input": "0.05", "cached_input": "0.005", "output": "0.4"},
    "gpt-5-pro": {"input": "15", "cached_input": "15", "output": "120"},
    "gpt-5-pro-2025-10-06": {"input": "15", "cached_input": "15", "output": "120"},
    "gpt-5-search-api": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5-search-api-2025-10-14": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1-2025-11-13": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1-chat-latest": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1-codex": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1-codex-max": {"input": "1.25", "cached_input": "0.125", "output": "10"},
    "gpt-5.1-codex-mini": {"input": "0.25", "cached_input": "0.025", "output": "2"},
    "gpt-5.2": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.2-2025-12-11": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.2-chat-latest": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.2-codex": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.2-pro": {"input": "21", "cached_input": "21", "output": "168"},
    "gpt-5.2-pro-2025-12-11": {"input": "21", "cached_input": "21", "output": "168"},
    "gpt-5.3-chat-latest": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.3-codex": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.3-codex-spark": {"input": "1.75", "cached_input": "0.175", "output": "14"},
    "gpt-5.4": {"input": "2.5", "cached_input": "0.25", "output": "15"},
    "gpt-5.4-2026-03-05": {"input": "2.5", "cached_input": "0.25", "output": "15"},
    "gpt-5.4-mini": {"input": "0.75", "cached_input": "0.075", "output": "4.5"},
    "gpt-5.4-mini-2026-03-17": {"input": "0.75", "cached_input": "0.075", "output": "4.5"},
    "gpt-5.4-nano": {"input": "0.2", "cached_input": "0.02", "output": "1.25"},
    "gpt-5.4-nano-2026-03-17": {"input": "0.2", "cached_input": "0.02", "output": "1.25"},
    "gpt-5.4-pro": {"input": "30", "cached_input": "3", "output": "180"},
    "gpt-5.4-pro-2026-03-05": {"input": "30", "cached_input": "3", "output": "180"},
    "gpt-5.5": {"input": "5", "cached_input": "0.5", "output": "30"},
    "gpt-5.5-2026-04-23": {"input": "5", "cached_input": "0.5", "output": "30"},
    "gpt-5.5-pro": {"input": "30", "cached_input": "3", "output": "180"},
    "gpt-5.5-pro-2026-04-23": {"input": "30", "cached_input": "3", "output": "180"},
    "gpt-5.6-luna": {"input": "0.2", "cached_input": "0.02", "output": "1.2"},
    "gpt-5.6-sol": {"input": "5", "cached_input": "0.5", "output": "30"},
    "gpt-5.6-terra": {"input": "2", "cached_input": "0.2", "output": "12"},
}


def default_cpa_model_pricing() -> dict[str, dict[str, str]]:
    return {model: dict(prices) for model, prices in _CPA_MODEL_PRICING_SNAPSHOT.items()}


def validate_cpa_model_pricing(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or not value:
        raise ValidationError("CPA 模型价格不能为空")
    normalized: dict[str, dict[str, str]] = {}
    for raw_model, raw_prices in value.items():
        model = str(raw_model).strip()
        if not model or not isinstance(raw_prices, dict):
            raise ValidationError("CPA 模型价格结构无效")
        prices: dict[str, str] = {}
        for field in ("input", "cached_input", "output"):
            try:
                price = Decimal(str(raw_prices.get(field)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValidationError(f"{model} 的 {field} 价格无效") from exc
            if not price.is_finite() or price < 0:
                raise ValidationError(f"{model} 的 {field} 价格不能为负数")
            prices[field] = str(price)
        normalized[model] = prices
    return normalized


def resolve_model_price(
    pricing: dict[str, dict[str, Any]], model: str
) -> tuple[str, dict[str, Decimal]] | None:
    candidates = [model]
    lowered = model.lower()
    if lowered.endswith("-latest"):
        candidates.append(model[: -len("-latest")])
    for candidate in candidates:
        raw = pricing.get(candidate)
        if not isinstance(raw, dict):
            continue
        try:
            return candidate, {
                field: Decimal(str(raw[field]))
                for field in ("input", "cached_input", "output")
            }
        except (InvalidOperation, KeyError, TypeError, ValueError):
            return None
    return None


@dataclass(frozen=True)
class CPACostResult:
    pricing_model: str
    base_cost_usd: Decimal
    estimated_cost_usd: Decimal
    fast_multiplier: Decimal
    double_billing_multiplier: Decimal


def calculate_cpa_cost(
    *,
    pricing: dict[str, dict[str, Any]],
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    service_tier: str,
    fast_multiplier: Decimal,
    double_billing_enabled: bool,
    double_billing_threshold_tokens: int,
    double_billing_multiplier: Decimal,
) -> CPACostResult | None:
    resolved = resolve_model_price(pricing, model)
    if resolved is None:
        return None
    pricing_model, prices = resolved
    input_count = max(0, int(input_tokens))
    cached_count = min(input_count, max(0, int(cached_input_tokens)))
    uncached_count = input_count - cached_count
    output_count = max(0, int(output_tokens))
    base = (
        Decimal(uncached_count) * prices["input"]
        + Decimal(cached_count) * prices["cached_input"]
        + Decimal(output_count) * prices["output"]
    ) / MILLION
    normalized_tier = service_tier.strip().lower()
    applied_fast = (
        Decimal(fast_multiplier)
        if normalized_tier in {"fast", "priority"}
        else Decimal("1")
    )
    applied_double = (
        Decimal(double_billing_multiplier)
        if double_billing_enabled
        and input_count > int(double_billing_threshold_tokens)
        else Decimal("1")
    )
    return CPACostResult(
        pricing_model=pricing_model,
        base_cost_usd=base,
        estimated_cost_usd=base * applied_fast * applied_double,
        fast_multiplier=applied_fast,
        double_billing_multiplier=applied_double,
    )
