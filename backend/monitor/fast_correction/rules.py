"""Ordered model rules for translating Sub2API FAST costs."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Pattern

from django.core.exceptions import ValidationError


MAX_FAST_CORRECTION_RULES = 100
MAX_MODEL_PATTERN_LENGTH = 160
MIN_MULTIPLIER = Decimal("0.01")
MAX_MULTIPLIER = Decimal("100")


def default_fast_correction_rules() -> list[dict[str, str]]:
    return [
        {
            "model_pattern": "*",
            "source_multiplier": "2",
            "target_multiplier": "2.5",
        }
    ]


def _multiplier(value: Any, field_label: str) -> Decimal:
    try:
        multiplier = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_label}必须是有效数字") from exc
    if not multiplier.is_finite():
        raise ValueError(f"{field_label}必须是有限数字")
    if multiplier < MIN_MULTIPLIER or multiplier > MAX_MULTIPLIER:
        raise ValueError(
            f"{field_label}必须在 {MIN_MULTIPLIER} 到 {MAX_MULTIPLIER} 之间"
        )
    return multiplier


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def normalize_fast_correction_rules(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("FAST 模型修正规则必须是列表")
    if len(value) > MAX_FAST_CORRECTION_RULES:
        raise ValueError(f"FAST 模型修正规则最多 {MAX_FAST_CORRECTION_RULES} 条")

    normalized: list[dict[str, str]] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 条 FAST 模型修正规则格式无效")
        pattern = str(raw.get("model_pattern") or "").strip().casefold()
        if not pattern:
            raise ValueError(f"第 {index} 条规则必须填写模型匹配")
        if len(pattern) > MAX_MODEL_PATTERN_LENGTH:
            raise ValueError(
                f"第 {index} 条规则的模型匹配不能超过 {MAX_MODEL_PATTERN_LENGTH} 个字符"
            )
        source = _multiplier(
            raw.get("source_multiplier"),
            f"第 {index} 条规则的 Sub2API FAST 倍率",
        )
        target = _multiplier(
            raw.get("target_multiplier"),
            f"第 {index} 条规则的修正目标倍率",
        )
        if target < source:
            raise ValueError(f"第 {index} 条规则的修正目标倍率不能小于源倍率")
        normalized.append(
            {
                "model_pattern": pattern,
                "source_multiplier": _decimal_text(source),
                "target_multiplier": _decimal_text(target),
            }
        )
    return normalized


def validate_fast_correction_rules(value: Any) -> None:
    try:
        normalize_fast_correction_rules(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@dataclass(frozen=True)
class FastCorrectionRule:
    model_pattern: str
    source_multiplier: Decimal
    target_multiplier: Decimal
    matcher: Pattern[str]

    @property
    def correction_factor(self) -> Decimal:
        return self.target_multiplier / self.source_multiplier - Decimal("1")


class FastCorrectionRuleSet:
    def __init__(self, raw_rules: Any):
        normalized = normalize_fast_correction_rules(raw_rules)
        self.rules = tuple(
            FastCorrectionRule(
                model_pattern=item["model_pattern"],
                source_multiplier=Decimal(item["source_multiplier"]),
                target_multiplier=Decimal(item["target_multiplier"]),
                matcher=re.compile(
                    "^"
                    + re.escape(item["model_pattern"]).replace(r"\*", ".*")
                    + "$",
                    re.IGNORECASE,
                ),
            )
            for item in normalized
        )

    def correction_factor_for_model(self, model: str) -> Decimal:
        normalized_model = str(model or "").strip()
        for rule in self.rules:
            if rule.matcher.fullmatch(normalized_model):
                return rule.correction_factor
        return Decimal("0")


def fast_correction_rules_digest(raw_rules: Any) -> str:
    normalized = normalize_fast_correction_rules(raw_rules)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
