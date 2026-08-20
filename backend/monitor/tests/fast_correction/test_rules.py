from decimal import Decimal

import pytest

from monitor.fast_correction.rules import (
    FastCorrectionRuleSet,
    normalize_fast_correction_rules,
)


def test_ordered_model_rules_use_first_wildcard_match():
    rules = FastCorrectionRuleSet(
        [
            {
                "model_pattern": "gpt-5.6*",
                "source_multiplier": "2.5",
                "target_multiplier": "2.5",
            },
            {
                "model_pattern": "*",
                "source_multiplier": "2",
                "target_multiplier": "2.5",
            },
        ]
    )

    assert rules.correction_factor_for_model("gpt-5.6-codex") == Decimal("0")
    assert rules.correction_factor_for_model("GPT-5.4") == Decimal("0.25")
    assert rules.correction_factor_for_model("") == Decimal("0.25")


def test_rule_validation_normalizes_values_and_rejects_negative_correction():
    assert normalize_fast_correction_rules(
        [
            {
                "model_pattern": " GPT-5.6* ",
                "source_multiplier": "2.50",
                "target_multiplier": "2.500",
            }
        ]
    ) == [
        {
            "model_pattern": "gpt-5.6*",
            "source_multiplier": "2.5",
            "target_multiplier": "2.5",
        }
    ]

    with pytest.raises(ValueError, match="不能小于源倍率"):
        normalize_fast_correction_rules(
            [
                {
                    "model_pattern": "*",
                    "source_multiplier": "2.5",
                    "target_multiplier": "2",
                }
            ]
        )
