"""Ordered, bounded model rules. Defaults are operator policy, not API prices."""

import re
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace
from typing import Any, Mapping

from django.core.exceptions import ValidationError

from ..fast_correction.rules import (
    _decimal_text,
    _multiplier,
    MAX_FAST_CORRECTION_RULES,
    MAX_MODEL_PATTERN_LENGTH,
    normalize_fast_correction_rules,
)

CORRECTION_SETTINGS = frozenset({
    "fast_correction_enabled", "fast_correction_rules",
    "long_context_correction_enabled", "long_context_correction_rules",
    "model_correction_enabled", "model_correction_rules",
})

CALCULATION_VERSION = "fast-long-model-v1"


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


def disabled_correction_policy() -> dict[str, Any]:
    return {
        "fast_correction_enabled": False,
        "fast_correction_rules": [],
        "long_context_correction_enabled": False,
        "long_context_correction_rules": [],
        "model_correction_enabled": False,
        "model_correction_rules": [],
    }


def _policy_value(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        if name not in source:
            raise ValueError(f"冻结修正策略缺少字段 {name}")
        return source[name]
    try:
        return getattr(source, name)
    except AttributeError as exc:
        raise ValueError(f"冻结修正策略缺少字段 {name}") from exc


def correction_policy_values(
    source: Any,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Return the normalized six-field legacy local-pricing policy."""

    if isinstance(source, Mapping) and not source:
        if allow_empty:
            return disabled_correction_policy()
        raise ValueError("历史本地修正观测缺少冻结策略")
    enabled = {}
    for name in (
        "fast_correction_enabled",
        "long_context_correction_enabled",
        "model_correction_enabled",
    ):
        value = _policy_value(source, name)
        if type(value) is not bool:
            raise ValueError(f"冻结修正策略字段 {name} 必须是布尔值")
        enabled[name] = value
    return {
        "fast_correction_enabled": enabled["fast_correction_enabled"],
        "fast_correction_rules": normalize_fast_correction_rules(
            _policy_value(source, "fast_correction_rules")
        ),
        "long_context_correction_enabled": enabled[
            "long_context_correction_enabled"
        ],
        "long_context_correction_rules": normalize_long_context_correction_rules(
            _policy_value(source, "long_context_correction_rules")
        ),
        "model_correction_enabled": enabled["model_correction_enabled"],
        "model_correction_rules": normalize_model_correction_rules(
            _policy_value(source, "model_correction_rules")
        ),
    }


def observation_correction_config(observation) -> SimpleNamespace:
    """Resolve one observation's immutable local-pricing configuration."""

    source = str(observation.correction_source)
    if source == "local":
        policy = correction_policy_values(observation.frozen_correction_policy)
    elif source in {"upstream", "none"}:
        policy = disabled_correction_policy()
    else:
        raise ValueError(f"未知修正来源：{source}")
    return SimpleNamespace(**policy)


def corrections_digest(
    source: Any,
    *,
    cutoff_at: datetime | None = None,
) -> str:
    payload = {
        "calculation_version": CALCULATION_VERSION,
        "policy": correction_policy_values(source, allow_empty=True),
    }
    if cutoff_at is not None:
        payload["local_cutoff_at"] = cutoff_at.isoformat()
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def historical_rules_digest(observation) -> str:
    payload = {
        "calculation_version": CALCULATION_VERSION,
        "correction_source": str(observation.correction_source),
        "pricing_epoch": str(observation.pricing_epoch),
        "policy": correction_policy_values(
            observation_correction_config(observation)
        ),
    }
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def corrections_enabled(source: Any) -> bool:
    policy = correction_policy_values(source, allow_empty=True)
    return any(
        policy[name]
        for name in CORRECTION_SETTINGS
        if name.endswith("_enabled")
    )
