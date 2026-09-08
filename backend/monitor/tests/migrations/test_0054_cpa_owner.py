import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_upgrade_uses_owner_role_prospectively_and_preserves_events():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("monitor", "0053_cpa_claim_events")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Pool = apps.get_model("monitor", "QuotaPool")
        Person = apps.get_model("monitor", "Participant")
        Account = apps.get_model("monitor", "MonitoredAccount")
        Allocation = apps.get_model("monitor", "PoolParticipant")
        Event = apps.get_model("monitor", "CPAUsageEvent")
        pool = Pool.objects.create(name="existing")
        owner = Person.objects.create(name="any owner name", is_owner=True)
        Allocation.objects.create(pool=pool, participant=owner, share_percent=50)
        account = Account.objects.create(
            provider="cpa", cpa_auth_index="existing", pool=pool, name="CPA"
        )
        old = Event.objects.create(
            account=account,
            occurred_at=timezone.now(),
            event_fingerprint="old",
            api_key_hash="",
            model="unknown",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        rule = apps.get_model("monitor", "CPAAccountOwnerBinding").objects.get(
            account_id=account.pk
        )
        assert rule.participant_id == owner.pk
        assert rule.started_at > old.occurred_at
        assert not apps.get_model("monitor", "CPAClaimEvent").objects.exists()
        assert (
            apps.get_model("monitor", "CPAUsageEvent")
            .objects.get(pk=old.pk)
            .api_key_hash
            == ""
        )
    finally:
        MigrationExecutor(connection).migrate(latest)
