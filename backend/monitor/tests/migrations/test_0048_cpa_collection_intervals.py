from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


FROM = [("monitor", "0047_expand_observation_sources")]
TO = [("monitor", "0048_cpa_collection_intervals")]


@pytest.mark.django_db(transaction=True)
def test_cpa_collection_interval_migration_uses_only_explicit_markers():
    executor = MigrationExecutor(connection)
    executor.migrate(FROM)
    old_apps = executor.loader.project_state(FROM).apps
    Account = old_apps.get_model("monitor", "MonitoredAccount")
    Observation = old_apps.get_model("monitor", "Observation")
    QuotaPool = old_apps.get_model("monitor", "QuotaPool")

    pool = QuotaPool.objects.create(name="迁移测试池")
    account = Account.objects.create(
        provider="cpa",
        pool=pool,
        cpa_auth_index="legacy-auth-index",
        name="旧 CPA 账号",
    )
    first_at = timezone.now().replace(microsecond=0)
    reset_at = first_at + timedelta(days=7)

    def observation(offset_minutes, percent, raw_window, source="scheduled"):
        observed_at = first_at + timedelta(minutes=offset_minutes)
        return Observation.objects.create(
            account_id=-account.id,
            source=source,
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=Decimal(percent),
            raw_selected_total_cost=Decimal(offset_minutes),
            selected_total_cost=Decimal(offset_minutes),
            total_standard_cost=Decimal(offset_minutes),
            total_actual_cost=Decimal(offset_minutes),
            effective_usd_per_percent=Decimal("16"),
            raw_window=raw_window,
        )

    observation(0, "40", {"provider": "cpa"})
    opening = observation(
        10,
        "41",
        {
            "provider": "cpa",
            "connection_baseline": True,
            "connection_boundary": "opened",
            "connection_boundary_id": f"session-a:opened:{account.id}",
        },
        source="cpa_subscription_opened",
    )
    observation(20, "42", {"provider": "cpa"})
    closing = observation(
        30,
        "43",
        {
            "provider": "cpa",
            "connection_boundary": "closed",
            "connection_boundary_reliable": False,
            "connection_boundary_id": f"session-a:closed:{account.id}",
        },
        source="cpa_subscription_closed",
    )
    observation(40, "44", {"provider": "cpa"})
    unmarked_account = Account.objects.create(
        provider="cpa",
        pool=pool,
        cpa_auth_index="unmarked-auth-index",
        name="无连接标记 CPA 账号",
    )
    Observation.objects.create(
        account_id=-unmarked_account.id,
        source="scheduled",
        observed_at=first_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("40"),
        raw_selected_total_cost=Decimal("0"),
        selected_total_cost=Decimal("0"),
        total_standard_cost=Decimal("0"),
        total_actual_cost=Decimal("0"),
        effective_usd_per_percent=Decimal("16"),
        raw_window={"provider": "cpa"},
    )

    executor = MigrationExecutor(connection)
    executor.migrate(TO)
    new_apps = executor.loader.project_state(TO).apps
    CollectionInterval = new_apps.get_model(
        "monitor",
        "CPAAccountCollectionInterval",
    )

    intervals = list(
        CollectionInterval.objects.filter(account_id=account.id).order_by(
            "connected_at",
            "id",
        )
    )
    assert len(intervals) == 1
    assert intervals[0].session_key == "session-a"
    assert intervals[0].connected_at == opening.observed_at
    assert intervals[0].disconnected_at == closing.observed_at
    assert intervals[0].opening_observation_id == opening.id
    assert intervals[0].closing_observation_id == closing.id
    assert intervals[0].end_reliable is False
    assert not CollectionInterval.objects.filter(
        account_id=unmarked_account.id
    ).exists()
