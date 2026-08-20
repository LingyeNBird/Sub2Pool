from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0032_system_user_page_access")]
TO = [("monitor", "0033_disable_legacy_fast_correction")]


@pytest.mark.django_db(transaction=True)
def test_upgrade_disables_legacy_fast_correction_and_announces_cutover():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    OldSettings = old_apps.get_model("monitor", "AppSettings")
    OldSettings.objects.create(pk=1, fast_correction_enabled=True)
    OldObservation = old_apps.get_model("monitor", "Observation")
    OldCorrection = old_apps.get_model(
        "monitor",
        "ObservationFastCorrection",
    )
    observed_at = timezone.now().replace(microsecond=0)
    observation = OldObservation.objects.create(
        account_id=7,
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=observed_at + timedelta(days=4),
        attribution_started_at=observed_at - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("100"),
        selected_total_cost=Decimal("125"),
        total_standard_cost=Decimal("125"),
        total_actual_cost=Decimal("125"),
        fast_correction_standard_cost=Decimal("25"),
        fast_correction_actual_cost=Decimal("25"),
        effective_usd_per_percent=Decimal("6.25"),
    )
    correction = OldCorrection.objects.create(
        observation=observation,
        sub2api_user_id=51,
        request_count=1,
        fast_request_count=1,
        fast_standard_cost=Decimal("100"),
        fast_actual_cost=Decimal("100"),
        standard_correction_cost=Decimal("25"),
        actual_correction_cost=Decimal("25"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewSettings = new_apps.get_model("monitor", "AppSettings")

    migrated = NewSettings.objects.get(pk=1)
    assert migrated.fast_correction_enabled is False
    assert migrated.fast_pricing_upgrade_notice_pending is True
    NewObservation = new_apps.get_model("monitor", "Observation")
    NewCorrection = new_apps.get_model(
        "monitor",
        "ObservationFastCorrection",
    )
    preserved_observation = NewObservation.objects.get(pk=observation.pk)
    preserved_correction = NewCorrection.objects.get(pk=correction.pk)
    assert preserved_observation.fast_correction_actual_cost == Decimal("25")
    assert preserved_observation.selected_total_cost == Decimal("125")
    assert preserved_correction.actual_correction_cost == Decimal("25")

    fresh = NewSettings.objects.create(pk=2)
    assert fresh.fast_correction_enabled is False
    assert fresh.fast_pricing_upgrade_notice_pending is False

    # 后续测试使用当前模型；显式恢复到最新迁移，避免数据库停在 0033。
    MigrationExecutor(connection).migrate(
        [("monitor", "0034_announcement_reads")]
    )
