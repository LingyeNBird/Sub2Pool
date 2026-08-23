from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0035_fast_correction_model_rules")]
TO = [("monitor", "0037_require_account_pool")]


@pytest.mark.django_db(transaction=True)
def test_quota_pool_migration_preserves_existing_global_mixed_contract():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    MonitoredAccount = old_apps.get_model("monitor", "MonitoredAccount")
    Observation = old_apps.get_model("monitor", "Observation")
    Participant = old_apps.get_model("monitor", "Participant")
    ParticipantSnapshot = old_apps.get_model("monitor", "ParticipantSnapshot")

    first = MonitoredAccount.objects.create(external_account_id=71, name="主账号")
    second = MonitoredAccount.objects.create(external_account_id=72, name="备用账号")
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=501,
        share_percent=Decimal("40"),
        is_owner=True,
    )
    observed_at = timezone.now().replace(microsecond=0)
    observation = Observation.objects.create(
        account_id=first.external_account_id,
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
        share_percent=Decimal("40"),
        is_owner=True,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        remaining_share_percent=Decimal("40"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewAccount = new_apps.get_model("monitor", "MonitoredAccount")
    NewParticipant = new_apps.get_model("monitor", "Participant")
    NewSnapshot = new_apps.get_model("monitor", "ParticipantSnapshot")
    PoolParticipant = new_apps.get_model("monitor", "PoolParticipant")
    QuotaPool = new_apps.get_model("monitor", "QuotaPool")

    pool = QuotaPool.objects.get()
    assert pool.name == "默认混池"
    assert set(
        NewAccount.objects.values_list("pool_id", flat=True)
    ) == {pool.id}
    allocation = PoolParticipant.objects.get(
        pool_id=pool.id,
        participant_id=participant.id,
    )
    assert allocation.share_percent == Decimal("40")
    assert "share_percent" not in {
        field.name for field in NewParticipant._meta.fields
    }

    migrated_snapshot = NewSnapshot.objects.get(pk=snapshot.id)
    assert migrated_snapshot.quota_pool_id == pool.id
    assert migrated_snapshot.quota_pool_name == "默认混池"
    assert migrated_snapshot.pool_contract_revision == 1
    assert NewAccount.objects.get(pk=second.id).pool_id == pool.id


@pytest.mark.django_db(transaction=True)
def test_quota_pool_migration_preserves_contract_without_monitored_accounts():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    Participant = old_apps.get_model("monitor", "Participant")
    participant = Participant.objects.create(
        name="待配置车友",
        sub2api_user_id=502,
        share_percent=Decimal("37.5"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    PoolParticipant = new_apps.get_model("monitor", "PoolParticipant")
    QuotaPool = new_apps.get_model("monitor", "QuotaPool")

    pool = QuotaPool.objects.get()
    assert pool.name == "默认混池"
    assert not pool.accounts.exists()
    assert PoolParticipant.objects.get(
        pool_id=pool.id,
        participant_id=participant.id,
    ).share_percent == Decimal("37.500")

    from monitor.serializers import MonitoredAccountSerializer

    serializer = MonitoredAccountSerializer(
        data={
            "external_account_id": 73,
            "name": "升级后首个账号",
            "enabled": True,
            "quota_query_mode": "passive",
        }
    )
    assert serializer.is_valid(), serializer.errors
    account = serializer.save()
    pool.refresh_from_db()
    assert account.pool_id == pool.id
    assert QuotaPool.objects.count() == 1
    assert pool.contract_revision == 2


@pytest.mark.django_db(transaction=True)
def test_quota_pool_reverse_migration_treats_missing_allocation_as_zero():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    MonitoredAccount = old_apps.get_model("monitor", "MonitoredAccount")
    Participant = old_apps.get_model("monitor", "Participant")

    first = MonitoredAccount.objects.create(external_account_id=71, name="主账号")
    second = MonitoredAccount.objects.create(external_account_id=72, name="备用账号")
    participant = Participant.objects.create(
        name="仅主池车友",
        sub2api_user_id=503,
        share_percent=Decimal("40"),
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    NewAccount = new_apps.get_model("monitor", "MonitoredAccount")
    QuotaPool = new_apps.get_model("monitor", "QuotaPool")
    second_pool = QuotaPool.objects.create(name="备用独立池")
    NewAccount.objects.filter(pk=second.pk).update(pool_id=second_pool.pk)

    try:
        with pytest.raises(RuntimeError, match="不同池份额"):
            MigrationExecutor(connection).migrate(FROM)
    finally:
        MigrationExecutor(connection).migrate(TO)

    assert NewAccount.objects.get(pk=first.pk).pool_id != second_pool.pk
