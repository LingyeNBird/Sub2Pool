from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_0049_defaults_do_not_invent_or_overwrite_historical_request_facts():
    before = [("monitor", "0048_cpa_collection_intervals")]
    after = [("monitor", "0049_traceable_billing_corrections")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Settings = apps.get_model("monitor", "AppSettings")
        Observation = apps.get_model("monitor", "Observation")
        Settings.objects.create(pk=1, fast_correction_enabled=False, fast_correction_rules=[
            {"model_pattern": "custom*", "source_multiplier": "2.5", "target_multiplier": "3"},
        ])
        at = timezone.now()
        row = Observation.objects.create(
            account_id=7, observed_at=at, window_seconds=604800,
            upstream_resets_at=at+timedelta(days=3), upstream_used_percent=15,
            raw_selected_total_cost=Decimal("120"), selected_total_cost=Decimal("125"),
            total_actual_cost=Decimal("120"), total_standard_cost=Decimal("240"),
            fast_correction_actual_cost=Decimal("5"), fast_correction_standard_cost=Decimal("10"),
            effective_usd_per_percent=Decimal("8"),
        )
        original = Observation.objects.values().get(pk=row.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        settings = apps.get_model("monitor", "AppSettings").objects.get(pk=1)
        assert not settings.fast_correction_enabled
        assert settings.fast_correction_rules[0]["target_multiplier"] == "3"
        assert settings.long_context_correction_enabled and settings.model_correction_enabled
        assert [rule["model_pattern"] for rule in settings.long_context_correction_rules] == ["gpt-5.6*", "gpt-6*"]
        assert all(rule["source_multiplier"] == "2" and rule["target_multiplier"] == "1" for rule in settings.long_context_correction_rules)
        assert settings.model_correction_rules == [{"model_pattern": "gpt-6*", "multiplier": "1.8"}]
        assert apps.get_model("monitor", "Observation").objects.values().get(pk=row.pk) == original
        for name in ("ObservationBillingCapture", "BillingUsageFact", "APIUsageRequestFact"):
            assert not apps.get_model("monitor", name).objects.exists()
    finally:
        current = MigrationExecutor(connection)
        current.migrate(current.loader.graph.leaf_nodes())
