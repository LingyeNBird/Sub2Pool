import importlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from monitor.historical_rebuild import apply_rebuild_plan


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


@pytest.mark.django_db(transaction=True)
def test_0026_removes_unused_after_hashes_without_dropping_run_data():
    source = [("monitor", "0025_participantbalanceoperation")]
    target = [("monitor", "0026_remove_historicalrebuildrun_after_hashes")]
    executor = MigrationExecutor(connection)
    executor.migrate(source)
    old_apps = executor.loader.project_state(source).apps
    HistoricalRebuildRun = old_apps.get_model("monitor", "HistoricalRebuildRun")
    run = HistoricalRebuildRun.objects.create(
        account_id=7,
        mode="audit_replay",
        state="applied",
        base_revision=3,
        result_revision=4,
        source_digest="source",
        algorithm_version="algorithm",
        build_id="build",
        config_digest="config",
        participant_policy_digest="policy",
        expires_at=timezone.now() + timedelta(hours=1),
        before_source_hash="before-source",
        after_source_hash="after-source",
        before_observable_hash="before-observable",
        after_observable_hash="after-observable",
    )

    executor = MigrationExecutor(connection)
    executor.migrate(target)
    new_apps = executor.loader.project_state(target).apps
    NewHistoricalRebuildRun = new_apps.get_model(
        "monitor",
        "HistoricalRebuildRun",
    )
    migrated = NewHistoricalRebuildRun.objects.get(pk=run.pk)
    field_names = {field.name for field in NewHistoricalRebuildRun._meta.get_fields()}

    assert migrated.state == "applied"
    assert migrated.result_revision == 4
    assert migrated.before_source_hash == "before-source"
    assert migrated.before_observable_hash == "before-observable"
    assert "after_source_hash" not in field_names
    assert "after_observable_hash" not in field_names


@pytest.mark.django_db(transaction=True)
def test_0027_removes_remote_repair_and_preserves_local_replay_results():
    source = [("monitor", "0026_remove_historicalrebuildrun_after_hashes")]
    target = [("monitor", "0027_remove_verified_remote_repair")]
    executor = MigrationExecutor(connection)
    executor.migrate(source)
    old_apps = executor.loader.project_state(source).apps
    AppSettings = old_apps.get_model("monitor", "AppSettings")
    HistoricalRebuildRun = old_apps.get_model("monitor", "HistoricalRebuildRun")
    expires_at = timezone.now() + timedelta(hours=1)
    AppSettings.objects.create(pk=1)
    local = HistoricalRebuildRun.objects.create(
        account_id=7,
        mode="audit_replay",
        state="applied",
        base_revision=3,
        result_revision=4,
        source_digest="source",
        plan_digest="legacy-local-digest",
        patch_summary={
            "total": 0,
            "replay": {
                "rebuilt_observations": 2,
                "automatic_exclusions": 0,
            },
        },
        algorithm_version="algorithm",
        build_id="build",
        config_digest="config",
        participant_policy_digest="policy",
        expires_at=expires_at,
    )
    ready_local = HistoricalRebuildRun.objects.create(
        account_id=7,
        mode="audit_replay",
        state="ready",
        base_revision=3,
        source_digest="ready-source",
        plan_digest="obsolete-digest",
        algorithm_version="algorithm",
        build_id="build",
        config_digest="config",
        participant_policy_digest="policy",
        expires_at=expires_at,
    )
    blocked_remote = HistoricalRebuildRun.objects.create(
        account_id=7,
        mode="verified_remote_repair",
        state="blocked",
        base_revision=3,
        source_digest="remote-source",
        algorithm_version="algorithm",
        build_id="build",
        config_digest="config",
        participant_policy_digest="policy",
        expires_at=expires_at,
    )
    applied_remote = HistoricalRebuildRun.objects.create(
        account_id=7,
        mode="verified_remote_repair",
        state="applied",
        base_revision=3,
        result_revision=4,
        source_digest="applied-remote-source",
        algorithm_version="algorithm",
        build_id="build",
        config_digest="config",
        participant_policy_digest="policy",
        expires_at=expires_at,
    )
    migration = importlib.import_module(
        "monitor.migrations.0027_remove_verified_remote_repair"
    )
    with pytest.raises(RuntimeError, match="已应用的远端修复"):
        migration.prepare_local_replay_runs(old_apps, None)
    applied_remote.delete()

    executor = MigrationExecutor(connection)
    executor.migrate(target)
    new_apps = executor.loader.project_state(target).apps
    NewSettings = new_apps.get_model("monitor", "AppSettings")
    NewHistoricalRebuildRun = new_apps.get_model(
        "monitor",
        "HistoricalRebuildRun",
    )
    migrated = NewHistoricalRebuildRun.objects.get(pk=local.pk)
    run_fields = {field.name for field in NewHistoricalRebuildRun._meta.get_fields()}
    settings_fields = {
        field.name for field in NewSettings._meta.get_fields()
    }

    assert migrated.replay_summary == {
        "rebuilt_observations": 2,
        "automatic_exclusions": 0,
    }
    assert migrated.state == "applied"
    assert (
        apply_rebuild_plan(local.pk, "legacy-local-digest").result_revision == 4
    )
    assert (
        NewHistoricalRebuildRun.objects.get(pk=ready_local.pk).state == "stale"
    )
    assert not NewHistoricalRebuildRun.objects.filter(pk=blocked_remote.pk).exists()
    assert {
        "mode",
        "cutoff",
        "requested_started_at",
        "requested_ended_at",
        "patch_summary",
        "before_source_hash",
        "before_observable_hash",
        "rollback_revision",
        "rolled_back_at",
    }.isdisjoint(run_fields)
    assert "sub2api_usage_log_query_horizon_days" not in settings_fields
    assert "rolled_back" not in {
        value
        for value, _label in NewHistoricalRebuildRun._meta.get_field(
            "state"
        ).choices
    }
    with pytest.raises(LookupError):
        new_apps.get_model("monitor", "HistoricalRebuildCoverage")
    with pytest.raises(LookupError):
        new_apps.get_model("monitor", "HistoricalRebuildPatch")
