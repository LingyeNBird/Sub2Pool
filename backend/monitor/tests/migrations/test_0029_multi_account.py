from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0028_manual_start_intervals")]
TO = [("monitor", "0031_pooled_participant_contracts")]


@pytest.mark.django_db(transaction=True)
def test_multi_account_migrations_preserve_singleton_policy_and_pending_operation():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    AppSettings = old_apps.get_model("monitor", "AppSettings")
    Observation = old_apps.get_model("monitor", "Observation")
    Participant = old_apps.get_model("monitor", "Participant")
    ParticipantBalanceOperation = old_apps.get_model(
        "monitor", "ParticipantBalanceOperation"
    )
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")

    checked_at = timezone.now().replace(microsecond=0)
    reset_at = checked_at + timedelta(days=4)
    AppSettings.objects.create(
        pk=1,
        openai_account_id=77,
        quota_query_mode="direct",
        last_local_check_at=checked_at,
        last_upstream_check_at=checked_at,
        last_success_at=checked_at,
        next_local_check_at=checked_at + timedelta(minutes=10),
        last_error="legacy status",
    )
    participant = Participant.objects.create(
        name="legacy rider",
        sub2api_user_id=701,
        share_percent=Decimal("62.5"),
        is_owner=True,
        latest_balance_usd=Decimal("88.25"),
        latest_selected_cost=Decimal("44.125"),
        last_checked_at=checked_at,
    )
    observation = Observation.objects.create(
        account_id=77,
        observed_at=checked_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=checked_at - timedelta(days=3),
        upstream_used_percent=Decimal("25"),
        raw_selected_total_cost=Decimal("120"),
        selected_total_cost=Decimal("120"),
        total_standard_cost=Decimal("120"),
        total_actual_cost=Decimal("120"),
        effective_usd_per_percent=Decimal("20"),
    )
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        selected_cost=Decimal("44.125"),
        raw_selected_cost=Decimal("44.125"),
        current_balance_usd=Decimal("88.25"),
        recommended_balance_usd=Decimal("100"),
    )
    operation = ParticipantBalanceOperation.objects.create(
        account_id=77,
        base_revision=9,
        participant=participant,
        snapshot=snapshot,
        sub2api_user_id=701,
        requested_balance_usd=Decimal("100"),
        confirmed_balance_usd=Decimal("100"),
        state="remote_confirmed",
        attempt_count=1,
        remote_confirmed_at=checked_at,
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewParticipant = new_apps.get_model("monitor", "Participant")
    NewSnapshot = new_apps.get_model("monitor", "ParticipantSnapshot")
    MonitoredAccount = new_apps.get_model("monitor", "MonitoredAccount")
    AccountParticipant = new_apps.get_model("monitor", "AccountParticipant")
    NewOperation = new_apps.get_model("monitor", "ParticipantBalanceOperation")
    OperationSource = new_apps.get_model(
        "monitor", "ParticipantBalanceOperationSource"
    )

    account = MonitoredAccount.objects.get(external_account_id=77)
    assert account.name == "OpenAI 账号 77"
    assert account.quota_query_mode == "direct"
    assert account.last_local_check_at == checked_at
    assert account.next_local_check_at == checked_at + timedelta(minutes=10)
    assert account.last_error == "legacy status"

    membership = AccountParticipant.objects.get(
        account_id=account.id,
        participant_id=participant.id,
    )
    assert {field.name for field in membership._meta.fields}.isdisjoint(
        {"share_percent", "is_owner", "enabled"}
    )
    assert membership.latest_selected_cost == Decimal("44.125000")
    assert membership.last_checked_at == checked_at

    migrated_participant = NewParticipant.objects.get(pk=participant.id)
    assert migrated_participant.latest_balance_usd == Decimal("88.250000")
    assert migrated_participant.share_percent == Decimal("62.500")
    assert migrated_participant.is_owner is True
    migrated_snapshot = NewSnapshot.objects.get(pk=snapshot.id)
    assert migrated_snapshot.share_percent == Decimal("62.500")
    assert migrated_snapshot.is_owner is True

    migrated_operation = NewOperation.objects.get(pk=operation.id)
    source = OperationSource.objects.get(operation_id=migrated_operation.id)
    assert source.account_id == account.id
    assert source.account_external_id == 77
    assert source.base_revision == 9
    assert source.snapshot_id == snapshot.id
    assert source.contribution_usd == Decimal("100.000000")
