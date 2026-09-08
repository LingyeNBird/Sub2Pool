import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_billing_upgrade_preserves_identity_ownership_and_requests():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("monitor", "0055_cpaquotaresetrequest")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        pool = apps.get_model("monitor", "QuotaPool").objects.create(
            name="Preserved pool"
        )
        member = apps.get_model("monitor", "Participant").objects.create(
            name="Owner", is_owner=True, sub2api_user_id=912, latest_balance_usd=123
        )
        account = apps.get_model("monitor", "MonitoredAccount").objects.create(
            provider="cpa", cpa_auth_index="billing-upgrade", pool=pool, name="CPA"
        )
        apps.get_model("monitor", "PoolParticipant").objects.create(
            pool=pool, participant=member, share_percent=50
        )
        event = apps.get_model("monitor", "CPAUsageEvent").objects.create(
            account=account,
            occurred_at=timezone.now(),
            event_fingerprint="preserved-event",
            api_key_hash="preserved-hash",
            model="unknown",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        assert (
            apps.get_model("monitor", "QuotaPool")
            .objects.get(pk=pool.pk)
            .cpa_billing_anchor
            is None
        )
        saved = apps.get_model("monitor", "Participant").objects.get(pk=member.pk)
        assert saved.sub2api_user_id == 912 and saved.latest_balance_usd == 123
        assert (
            apps.get_model("monitor", "CPAUsageEvent")
            .objects.get(pk=event.pk)
            .api_key_hash
            == "preserved-hash"
        )
        assert not apps.get_model("monitor", "CPAClaimEvent").objects.exists()
        assert (
            apps.get_model("monitor", "PoolParticipant")
            .objects.get(pool_id=pool.pk)
            .participant_id
            == member.pk
        )
    finally:
        MigrationExecutor(connection).migrate(latest)
