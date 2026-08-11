from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0023_appsettings_readonly_api_key_created_at_and_more")]
TO = [("monitor", "0024_history_maintenance_control_plane")]


@pytest.mark.django_db(transaction=True)
def test_0024_adds_canonical_points_without_rewriting_legacy_amounts():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    AppSettings = old_apps.get_model("monitor", "AppSettings")
    Participant = old_apps.get_model("monitor", "Participant")
    Observation = old_apps.get_model("monitor", "Observation")
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")
    ParticipantUsageSample = old_apps.get_model(
        "monitor", "ParticipantUsageSample"
    )
    Sub2APIUserUsageSample = old_apps.get_model(
        "monitor", "Sub2APIUserUsageSample"
    )

    AppSettings.objects.create(pk=1, openai_account_id=7)
    observed_at = timezone.now().replace(microsecond=0)
    reset_at = observed_at + timedelta(days=3)
    participant = Participant.objects.create(
        name="legacy rider",
        sub2api_user_id=11,
        share_percent=Decimal("100"),
        is_owner=True,
    )
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("15"),
        raw_selected_total_cost=Decimal("321.123456"),
        selected_total_cost=Decimal("300.123456"),
        total_standard_cost=Decimal("400.123456"),
        total_actual_cost=Decimal("321.123456"),
        cost_window_started_at=observed_at - timedelta(days=2),
        cost_window_ended_at=observed_at,
        interval_cost_started_at=observed_at - timedelta(hours=1),
        interval_standard_cost=Decimal("10.000001"),
        interval_actual_cost=Decimal("8.000001"),
        effective_usd_per_percent=Decimal("20"),
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        selected_cost=Decimal("300.123456"),
        raw_selected_cost=Decimal("321.123456"),
        current_balance_usd=Decimal("678.876544"),
    )
    user = Sub2APIUserUsageSample.objects.create(
        account_id=7,
        sub2api_user_id=11,
        username="legacy-user",
        email="legacy@example.com",
        observed_at=observed_at,
        window_started_at=observed_at - timedelta(days=2),
        window_ended_at=observed_at,
        window_resets_at=reset_at,
        total_standard_cost=Decimal("400.123456"),
        total_actual_cost=Decimal("321.123456"),
        interval_started_at=observed_at - timedelta(hours=1),
        interval_standard_cost=Decimal("10.000001"),
        interval_actual_cost=Decimal("8.000001"),
        interval_source="saved_interval",
    )
    usage = ParticipantUsageSample.objects.create(
        participant=participant,
        account_id=7,
        observed_at=observed_at,
        balance_usd=Decimal("678.876544"),
        selected_cost=Decimal("300.123456"),
        raw_selected_cost=Decimal("321.123456"),
    )
    original = {
        "observation": (
            observation.raw_selected_total_cost,
            observation.selected_total_cost,
            observation.total_standard_cost,
            observation.total_actual_cost,
        ),
        "user": (user.total_standard_cost, user.total_actual_cost),
        "usage": (
            usage.balance_usd,
            usage.selected_cost,
            usage.raw_selected_cost,
        ),
    }

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewSettings = new_apps.get_model("monitor", "AppSettings")
    NewObservation = new_apps.get_model("monitor", "Observation")
    NewParticipantUsage = new_apps.get_model(
        "monitor", "ParticipantUsageSample"
    )
    NewUser = new_apps.get_model("monitor", "Sub2APIUserUsageSample")
    UsageSamplePoint = new_apps.get_model("monitor", "UsageSamplePoint")
    ParticipantBalanceSample = new_apps.get_model(
        "monitor", "ParticipantBalanceSample"
    )
    HistoryMaintenanceState = new_apps.get_model(
        "monitor", "HistoryMaintenanceState"
    )
    HistoricalRebuildCoverage = new_apps.get_model(
        "monitor",
        "HistoricalRebuildCoverage",
    )

    migrated_observation = NewObservation.objects.get(pk=observation.pk)
    migrated_user = NewUser.objects.get(pk=user.pk)
    migrated_usage = NewParticipantUsage.objects.get(pk=usage.pk)
    point = UsageSamplePoint.objects.get(account_id=7, observed_at=observed_at)

    assert (
        migrated_observation.raw_selected_total_cost,
        migrated_observation.selected_total_cost,
        migrated_observation.total_standard_cost,
        migrated_observation.total_actual_cost,
    ) == original["observation"]
    assert (
        migrated_user.total_standard_cost,
        migrated_user.total_actual_cost,
    ) == original["user"]
    assert (
        migrated_usage.balance_usd,
        migrated_usage.selected_cost,
        migrated_usage.raw_selected_cost,
    ) == original["usage"]
    assert migrated_observation.sample_point_id == point.id
    assert migrated_user.sample_point_id == point.id
    assert migrated_usage.sample_point_id == point.id
    assert point.write_status == "legacy_unknown"
    assert point.reconciliation_status == "unknown"
    assert point.account_actual_cost == Decimal("321.123456")
    assert point.residual_actual_cost == Decimal("0")
    assert set(
        ParticipantBalanceSample.objects.filter(point_id=point.id).values_list(
            "provenance", flat=True
        )
    ) == {
        "legacy_participant_usage",
        "legacy_observation_snapshot",
    }
    assert HistoryMaintenanceState.objects.get(account_id=7).fact_revision == 0
    assert NewSettings.objects.get(pk=1).sub2api_usage_log_query_horizon_days == 90
    assert "out_of_scope" in {
        value
        for value, _label in HistoricalRebuildCoverage._meta.get_field(
            "status"
        ).choices
    }


@pytest.mark.django_db(transaction=True)
def test_0025_adds_durable_balance_journal_without_rewriting_existing_facts():
    source = [("monitor", "0024_history_maintenance_control_plane")]
    target = [("monitor", "0025_participantbalanceoperation")]
    executor = MigrationExecutor(connection)
    executor.migrate(source)
    old_apps = executor.loader.project_state(source).apps
    Participant = old_apps.get_model("monitor", "Participant")
    Observation = old_apps.get_model("monitor", "Observation")
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")
    participant = Participant.objects.create(
        name="existing rider",
        sub2api_user_id=51,
        share_percent=Decimal("100"),
    )
    observed_at = timezone.now().replace(microsecond=0)
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=observed_at + timedelta(days=3),
        upstream_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("100"),
        selected_total_cost=Decimal("100"),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
        effective_usd_per_percent=Decimal("10"),
    )
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        recommended_balance_usd=Decimal("123.45"),
        needs_manual_update=True,
    )

    executor = MigrationExecutor(connection)
    executor.migrate(target)
    new_apps = executor.loader.project_state(target).apps
    NewParticipant = new_apps.get_model("monitor", "Participant")
    NewSnapshot = new_apps.get_model("monitor", "ParticipantSnapshot")
    BalanceOperation = new_apps.get_model(
        "monitor",
        "ParticipantBalanceOperation",
    )
    operation = BalanceOperation.objects.create(
        account_id=7,
        base_revision=4,
        participant_id=participant.id,
        snapshot_id=snapshot.id,
        sub2api_user_id=51,
        requested_balance_usd=Decimal("123.45"),
    )

    assert NewParticipant.objects.get(pk=participant.id).share_percent == Decimal(
        "100"
    )
    assert NewSnapshot.objects.get(pk=snapshot.id).selected_cost == Decimal(
        "100"
    )
    assert operation.state == "prepared"
    assert operation.attempt_count == 0
