"""Ordered, bounded model rules. Defaults are operator policy, not API prices."""

import re
from hashlib import sha256
import json
from typing import Any

from django.core.exceptions import ValidationError

from ..fast_correction.rules import (
    _decimal_text, _multiplier, MAX_FAST_CORRECTION_RULES,
    MAX_MODEL_PATTERN_LENGTH,
)

CORRECTION_SETTINGS = frozenset({
    "fast_correction_enabled", "fast_correction_rules",
    "long_context_correction_enabled", "long_context_correction_rules",
    "model_correction_enabled", "model_correction_rules",
})


def default_long_context_correction_rules() -> list[dict]:
    return [
        {"model_pattern": pattern, "source_multiplier": "2",
         "target_multiplier": "1", "threshold_tokens": 272000}
        for pattern in ("gpt-5.6*", "gpt-6*")
    ]


def default_model_correction_rules() -> list[dict]:
    return [{"model_pattern": "gpt-6*", "multiplier": "1.8"}]


def _normalize(value: Any, *, long_context: bool) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_FAST_CORRECTION_RULES:
        raise ValueError(f"修正规则必须是列表，最多 {MAX_FAST_CORRECTION_RULES} 条")
    result = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条修正规则格式无效")
        pattern = item.get("model_pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"第 {index} 条规则必须填写模型匹配")
        pattern = pattern.strip().casefold()
        if len(pattern) > MAX_MODEL_PATTERN_LENGTH:
            raise ValueError(f"模型匹配不能超过 {MAX_MODEL_PATTERN_LENGTH} 个字符")
        row = {"model_pattern": pattern}
        fields = ("source_multiplier", "target_multiplier") if long_context else ("multiplier",)
        for field in fields:
            row[field] = _decimal_text(_multiplier(item.get(field), f"第 {index} 条规则的倍率"))
        if long_context:
            # Used only when upstream does not expose its applied-billing flag.
            threshold = item.get("threshold_tokens", 272000)
            if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= 100000000:
                raise ValueError("长上下文阈值必须是 1 到 100000000 之间的整数")
            row["threshold_tokens"] = threshold
        result.append(row)
    return result


def normalize_long_context_correction_rules(value: Any) -> list[dict]:
    return _normalize(value, long_context=True)


def normalize_model_correction_rules(value: Any) -> list[dict]:
    return _normalize(value, long_context=False)


def validate_long_context_correction_rules(value: Any) -> None:
    try:
        normalize_long_context_correction_rules(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_model_correction_rules(value: Any) -> None:
    try:
        normalize_model_correction_rules(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def compile_rules(rows: list[dict]) -> tuple:
    return tuple((re.compile(re.escape(row["model_pattern"]).replace(r"\*", ".*"), re.IGNORECASE), row) for row in rows)


def first_match(rules: tuple, model: str) -> dict | None:
    return next((row for matcher, row in rules if matcher.fullmatch(str(model or "").strip())), None)


def corrections_digest(config) -> str:
    payload = {name: getattr(config, name) for name in sorted(CORRECTION_SETTINGS)}
    # Change this version when the order or rounding semantics change.
    payload["calculation_version"] = "fast-long-model-v1"
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def corrections_enabled(config) -> bool:
    return any(getattr(config, name) for name in CORRECTION_SETTINGS if name.endswith("_enabled"))
