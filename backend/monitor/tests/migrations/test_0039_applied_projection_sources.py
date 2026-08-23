from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0038_snapshot_source_user")]
TO = [("monitor", "0039_applied_projection_sources")]


@pytest.mark.django_db(transaction=True)
def test_applied_projection_migration_backfills_share_and_allows_reuse():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    Account = old_apps.get_model("monitor", "MonitoredAccount")
    Observation = old_apps.get_model("monitor", "Observation")
    Operation = old_apps.get_model("monitor", "ParticipantBalanceOperation")
    OperationSource = old_apps.get_model(
        "monitor",
        "ParticipantBalanceOperationSource",
    )
    Participant = old_apps.get_model("monitor", "Participant")
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")
    QuotaPool = old_apps.get_model("monitor", "QuotaPool")

    pool = QuotaPool.objects.create(name="测试池")
    account = Account.objects.create(
        pool=pool,
        external_account_id=71,
        name="主账号",
    )
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=501,
    )
    observed_at = timezone.now().replace(microsecond=0)
    observation = Observation.objects.create(
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
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        source_sub2api_user_id=participant.sub2api_user_id,
        share_percent=Decimal("40"),
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
    )
    operation = Operation.objects.create(
        participant=participant,
        sub2api_user_id=participant.sub2api_user_id,
        requested_balance_usd=Decimal("100"),
    )
    source = OperationSource.objects.create(
        operation=operation,
        account=account,
        account_external_id=account.external_account_id,
        base_revision=0,
        snapshot=snapshot,
        contribution_usd=Decimal("100"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewOperation = new_apps.get_model("monitor", "ParticipantBalanceOperation")
    NewSource = new_apps.get_model(
        "monitor",
        "ParticipantBalanceOperationSource",
    )

    migrated = NewSource.objects.get(pk=source.pk)
    assert migrated.share_percent == Decimal("40")
    second_operation = NewOperation.objects.create(
        participant_id=participant.pk,
        sub2api_user_id=participant.sub2api_user_id,
        requested_balance_usd=Decimal("80"),
    )
    second_source = NewSource.objects.create(
        operation=second_operation,
        account_id=account.pk,
        account_external_id=account.external_account_id,
        base_revision=0,
        snapshot_id=snapshot.pk,
        share_percent=Decimal("30"),
        contribution_usd=Decimal("80"),
    )
    assert second_source.snapshot_id == migrated.snapshot_id
