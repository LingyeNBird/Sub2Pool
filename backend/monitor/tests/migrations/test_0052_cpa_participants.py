import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_cpa_migration_preserves_users_grants_and_unclaimed_events():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    before = [("monitor", "0051_pooled_research_raw_only")]
    after = [("monitor", "0052_cpa_participants")]
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Participant = apps.get_model("monitor", "Participant")
        Pool = apps.get_model("monitor", "QuotaPool")
        Account = apps.get_model("monitor", "MonitoredAccount")
        Event = apps.get_model("monitor", "CPAUsageEvent")
        User = apps.get_model("auth", "User")
        user = User.objects.create(username="legacy-rider")
        person = Participant.objects.create(name="原成员", sub2api_user_id=99)
        person.authorized_users.add(user)
        pool = Pool.objects.create(name="原池")
        account = Account.objects.create(
            provider="cpa", cpa_auth_index="legacy-cpa", pool=pool, name="原 CPA"
        )
        event = Event.objects.create(
            account=account,
            occurred_at=timezone.now(),
            model="gpt-test",
            event_fingerprint="legacy-event",
            api_key_hash="a" * 64,
            api_key_hint="9999",
            total_tokens=100,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        assert (
            apps.get_model("monitor", "Participant")
            .objects.get(pk=person.pk)
            .sub2api_user_id
            == 99
        )
        assert (
            apps.get_model("monitor", "Participant")
            .objects.get(pk=person.pk)
            .authorized_users.filter(pk=user.pk)
            .exists()
        )
        assert (
            apps.get_model("monitor", "CPAUsageEvent")
            .objects.get(pk=event.pk)
            .api_key_hash
            == "a" * 64
        )
        assert not apps.get_model("monitor", "CPAKeyBinding").objects.exists()
        assert not apps.get_model("monitor", "CPAQuotaContract").objects.exists()
        apps.get_model("monitor", "Participant").objects.create(
            name="CPA 新成员", sub2api_user_id=None
        )
    finally:
        MigrationExecutor(connection).migrate(latest)
