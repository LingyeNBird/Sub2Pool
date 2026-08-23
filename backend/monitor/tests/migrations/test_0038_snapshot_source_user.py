from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0037_require_account_pool")]
TO = [("monitor", "0038_snapshot_source_user")]


@pytest.mark.django_db(transaction=True)
def test_snapshot_source_user_migration_backfills_bound_identity():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    Observation = old_apps.get_model("monitor", "Observation")
    Sub2APIUserUsageSample = old_apps.get_model(
        "monitor",
        "Sub2APIUserUsageSample",
    )
    UsageSamplePoint = old_apps.get_model("monitor", "UsageSamplePoint")
    Participant = old_apps.get_model("monitor", "Participant")
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")

    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=501,
    )
    observed_at = timezone.now().replace(microsecond=0)
    point = UsageSamplePoint.objects.create(
        account_id=71,
        observed_at=observed_at,
        window_started_at=observed_at - timedelta(days=3),
        window_ended_at=observed_at,
        window_resets_at=observed_at + timedelta(days=4),
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=Decimal("400"),
        account_actual_cost=Decimal("400"),
        interval_started_at=observed_at - timedelta(days=3),
        interval_standard_cost=Decimal("400"),
        interval_actual_cost=Decimal("400"),
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=1,
        expected_user_digest="verified",
        write_status="complete",
        reconciliation_status="reconciled",
    )
    observation = Observation.objects.create(
        sample_point=point,
        account_id=71,
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=observed_at + timedelta(days=4),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    Sub2APIUserUsageSample.objects.create(
        account_id=71,
        sample_point=point,
        sub2api_user_id=participant.sub2api_user_id,
        observed_at=observed_at,
        window_resets_at=observed_at + timedelta(days=4),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
    )
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        share_percent=Decimal("40"),
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
    )
    unverified_participant = Participant.objects.create(
        name="无原始身份证据的车友",
        sub2api_user_id=502,
    )
    unverified_snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=unverified_participant,
        share_percent=Decimal("20"),
        raw_selected_cost=Decimal("50"),
        selected_cost=Decimal("50"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewSnapshot = new_apps.get_model("monitor", "ParticipantSnapshot")

    assert (
        NewSnapshot.objects.get(pk=snapshot.pk).source_sub2api_user_id
        == participant.sub2api_user_id
    )
    assert (
        NewSnapshot.objects.get(
            pk=unverified_snapshot.pk
        ).source_sub2api_user_id
        is None
    )
