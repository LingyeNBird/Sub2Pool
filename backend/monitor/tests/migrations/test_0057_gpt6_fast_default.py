from copy import deepcopy
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from monitor.upstream_pricing.planning import normalize_policy, rule_factor


FROM = [("monitor", "0056_pricing_announcement_action")]
TO = [("monitor", "0057_gpt6_fast_default")]


@pytest.fixture(autouse=True)
def restore_latest_schema(transactional_db):
    targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
    yield
    MigrationExecutor(connection).migrate(targets)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rules,expected", [
    ([{"model_pattern": "*", "multiplier": "2.5"}], ("2", "2", "2.5")),
    ([{"model_pattern": "gpt-6-astra", "multiplier": "3"},
      {"model_pattern": "*", "multiplier": "4"}], ("3", "2", "4")),
    ([{"model_pattern": "gpt-6*", "multiplier": "3"},
      {"model_pattern": "*", "multiplier": "4"}], ("3", "3", "4")),
    ([], (None, None, None)),
    ([{"model_pattern": f"custom-{index}", "multiplier": "3"} for index in range(99)]
     + [{"model_pattern": "*", "multiplier": "4"}], ("4", "4", "4")),
])
def test_upgrade_adds_default_without_overwriting_custom_rules_or_history(rules, expected):
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    apps = executor.loader.project_state(FROM).apps
    State = apps.get_model("monitor", "UpstreamPricingState")
    frozen = {"fast_correction_rules": [{
        "model_pattern": "*", "source_multiplier": "2", "target_multiplier": "2.5",
    }]}
    State.objects.update_or_create(pk=1, defaults={
        "policy": {"fast_rules": deepcopy(rules), "model_rules": [],
                   "long_context_pricing_enabled": None},
        "legacy_policy": frozen, "status": "applied", "revision": 7,
        "attempted_at": timezone.now(), "applied_at": timezone.now(),
        "announcement_applied_at": timezone.now(), "selected_group_ids": [7],
        "targets": [{"group_id": 7, "status": "applied", "baseline": {},
                     "after": {"model_pricing": [{"models": ["*"], "fast_multiplier": 2.5}]}}],
    })
    before = State.objects.values().get(pk=1)
    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    State = executor.loader.project_state(TO).apps.get_model("monitor", "UpstreamPricingState")
    after = State.objects.values().get(pk=1)
    policy = normalize_policy(after["policy"])
    for model, multiplier in zip(("gpt-6-astra", "GPT-6-future", "gpt-5.6"), expected):
        assert rule_factor(policy["fast_rules"], model) == (
            Decimal(multiplier) if multiplier is not None else None
        )
    changed = policy["fast_rules"] != rules
    assert after["status"] == ("pending" if changed else "applied")
    for key in before.keys() - {"policy", "status", "last_error"}:
        assert after[key] == before[key]
    assert policy["model_rules"] == []
    assert policy["long_context_pricing_enabled"] is None
    # Downgrade cannot undo user edits; re-upgrade must not duplicate the rule.
    executor.migrate(FROM)
    MigrationExecutor(connection).migrate(TO)
    assert State.objects.values().get(pk=1) == after
