import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.integrations.sub2api import (
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    Observation,
    Participant,
    ParticipantBalanceOperation,
    ParticipantBalanceOperationSource,
    ParticipantSnapshot,
    PoolParticipant,
    QuotaPool,
)
from monitor.replay import rebuild_account
from monitor.reporting import aggregate_recommendation
from monitor.tests.helpers import create_monitored_account, create_participant, jwt_login


def create_account_snapshot(
    *,
    account: MonitoredAccount,
    participant: Participant,
    share_percent: Decimal,
    recommended: Decimal,
    recommended_min: Decimal,
    recommended_max: Decimal,
    charged_percent: Decimal,
    observed_at,
) -> ParticipantSnapshot:
    observation = Observation.objects.create(
        account_id=account.external_account_id,
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=observed_at + timedelta(days=4),
        attribution_started_at=observed_at - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    return ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        source_sub2api_user_id=participant.sub2api_user_id,
        share_percent=share_percent,
        quota_pool_id=account.pool_id,
        quota_pool_name=account.pool.name,
        pool_contract_revision=account.pool.contract_revision,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        charged_cycle_percent=charged_percent,
        charged_percent_lower=max(Decimal("0"), charged_percent - Decimal("1")),
        charged_percent_upper=charged_percent + Decimal("1"),
        remaining_share_percent=max(Decimal("0"), share_percent - charged_percent),
        current_balance_usd=participant.latest_balance_usd,
        recommended_balance_usd=recommended,
        recommended_balance_min_usd=recommended_min,
        recommended_balance_max_usd=recommended_max,
        balance_difference_usd=recommended - (participant.latest_balance_usd or 0),
        needs_manual_update=True,
    )


@pytest.mark.django_db
def test_allocation_api_merges_singleton_accounts_into_one_pool_contract():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)

    account_ids = []
    for external_account_id, name, mode in (
        (71, "主账号", "passive"),
        (72, "备用账号", "direct"),
    ):
        response = client.post(
            "/api/settings/monitored-accounts",
            data=json.dumps(
                {
                    "external_account_id": external_account_id,
                    "name": name,
                    "enabled": True,
                    "quota_query_mode": mode,
                }
            ),
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201, response.json()
        account_ids.append(response.json()["data"]["id"])

    participant = client.post(
        "/api/participants",
        data=json.dumps(
            {
                "name": "同一用户",
                "email": "rider@example.com",
                "sub2api_user_id": 501,
                "sub2api_username": "rider",
                "sub2api_email": "rider@example.com",
                "is_owner": True,
                "enabled": True,
                "notes": "",
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert participant.status_code == 201, participant.json()
    data = participant.json()["data"]
    assert data["sub2api_user_id"] == 501
    assert data["pool_allocations"] == []
    assert data["is_owner"] is True
    assert data["snapshot"] is None
    assert {
        item["external_account_id"] for item in data["account_breakdowns"]
    } == {71, 72}
    assert all(not item["allocated"] for item in data["account_breakdowns"])
    assert AccountParticipant.objects.filter(
        participant_id=data["id"]
    ).count() == 2

    allocation = client.put(
        "/api/quota-allocation",
        data=json.dumps(
            {
                "pools": [
                    {
                        "name": "混池 1",
                        "account_ids": account_ids,
                        "allocations": [
                            {
                                "participant_id": data["id"],
                                "share_percent": "40",
                            }
                        ],
                    }
                ]
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert allocation.status_code == 200, allocation.json()
    allocation_data = allocation.json()["data"]
    assert len(allocation_data["pools"]) == 1
    assert allocation_data["pools"][0]["name"] == "混池 1"
    assert allocation_data["pools"][0]["account_ids"] == account_ids
    assert allocation_data["pools"][0]["total_share_percent"] == 40.0
    assert MonitoredAccount.objects.values("pool_id").distinct().count() == 1

    refreshed = client.get("/api/participants", **headers).json()["data"][0]
    assert refreshed["pool_allocations"][0]["share_percent"] == 40.0

    immutable_id = client.put(
        f"/api/settings/monitored-accounts/{account_ids[0]}",
        data=json.dumps({"external_account_id": 99}),
        content_type="application/json",
        **headers,
    )
    assert immutable_id.status_code == 400

    duplicate_user = client.post(
        "/api/participants",
        data=json.dumps(
            {
                "name": "重复绑定",
                "sub2api_user_id": 501,
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert duplicate_user.status_code == 400


@pytest.mark.django_db
def test_deleting_one_mixed_pool_account_bumps_remaining_contract_revision():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    pool = QuotaPool.objects.create(name="可拆除混池")
    first = create_monitored_account(71, name="主账号", pool=pool)
    second = create_monitored_account(72, name="备用账号", pool=pool)
    client = Client()
    headers, _response = jwt_login(client)

    response = client.delete(
        f"/api/settings/monitored-accounts/{first.id}",
        **headers,
    )

    assert response.status_code == 200, response.json()
    pool.refresh_from_db()
    second.refresh_from_db()
    assert pool.contract_revision == 2
    assert second.pool_id == pool.id
    assert not MonitoredAccount.objects.filter(pk=first.pk).exists()


@pytest.mark.django_db
def test_disabling_mixed_pool_account_preserves_contract_revision():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    pool = QuotaPool.objects.create(name="可停用混池")
    first = create_monitored_account(71, name="主账号", pool=pool)
    create_monitored_account(72, name="备用账号", pool=pool)
    client = Client()
    headers, _response = jwt_login(client)

    response = client.put(
        f"/api/settings/monitored-accounts/{first.id}",
        data=json.dumps({"enabled": False}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200, response.json()
    pool.refresh_from_db()
    first.refresh_from_db()
    assert pool.contract_revision == 1
    assert first.enabled is False


@pytest.mark.django_db
def test_aggregate_recommendation_nets_accounts_before_global_zero_clamp():
    config = AppSettings.load()
    pool = QuotaPool.objects.create(name="主备用混池")
    first = create_monitored_account(71, name="主账号", pool=pool)
    second = create_monitored_account(72, name="备用账号", pool=pool)
    participant = create_participant(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
        account=first,
    )
    for account in (first, second):
        AccountParticipant.objects.get_or_create(
            account=account,
            participant=participant,
        )
    now = timezone.now().replace(microsecond=0)
    first_snapshot = create_account_snapshot(
        account=first,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("0"),
        recommended_min=Decimal("0"),
        recommended_max=Decimal("0"),
        charged_percent=Decimal("60"),
        observed_at=now,
    )
    create_account_snapshot(
        account=second,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("0"),
        recommended_min=Decimal("0"),
        recommended_max=Decimal("0"),
        charged_percent=Decimal("20"),
        observed_at=now + timedelta(seconds=1),
    )

    aggregate, snapshots = aggregate_recommendation(participant, config)
    assert aggregate is not None
    assert aggregate["allocation_model"] == "partitioned_pool_sum"
    assert aggregate["recommendation_complete"] is True
    assert aggregate["recommended_balance_usd"] == 380.0
    assert aggregate["recommended_balance_min_usd"] == 342.0
    assert aggregate["recommended_balance_max_usd"] == 418.0
    assert aggregate["is_overused"] is False
    assert aggregate["needs_manual_update"] is True
    assert {item.observation.account_id for item in snapshots} == {71, 72}
    assert aggregate["expected_entitlement_usd"] == 2000.0
    assert aggregate["consumed_entitlement_usd"] == 1600.0
    assert aggregate["remaining_entitlement_usd"] == 400.0
    assert aggregate["entitlement_usage_percent"] == 80.0
    source_by_account = {
        item["external_account_id"]: item for item in aggregate["sources"]
    }
    assert source_by_account[71]["net_position_usd"] == -200.0
    assert source_by_account[72]["net_position_usd"] == 600.0
    assert source_by_account[71]["contribution_usd"] == 0.0
    assert source_by_account[72]["contribution_usd"] == 380.0
    assert source_by_account[71]["estimated_capacity_usd"] == 2000.0
    assert source_by_account[71]["expected_entitlement_usd"] == 1000.0
    assert source_by_account[71]["consumed_entitlement_usd"] == 1200.0
    assert source_by_account[71]["remaining_entitlement_usd"] == -200.0
    assert source_by_account[71]["entitlement_usage_percent"] == 120.0
    assert source_by_account[72]["expected_entitlement_usd"] == 1000.0
    assert source_by_account[72]["consumed_entitlement_usd"] == 400.0
    assert source_by_account[72]["remaining_entitlement_usd"] == 600.0
    assert source_by_account[72]["entitlement_usage_percent"] == 40.0

    first_snapshot.charged_cycle_percent = Decimal("90")
    first_snapshot.charged_percent_lower = Decimal("89")
    first_snapshot.charged_percent_upper = Decimal("91")
    first_snapshot.save(
        update_fields=[
            "charged_cycle_percent",
            "charged_percent_lower",
            "charged_percent_upper",
        ]
    )
    aggregate, _snapshots = aggregate_recommendation(participant, config)
    assert aggregate is not None
    assert aggregate["recommended_balance_usd"] == 0.0
    assert aggregate["recommended_balance_min_usd"] == 0.0
    assert aggregate["recommended_balance_max_usd"] == 0.0
    assert aggregate["is_overused"] is True
    assert aggregate["expected_entitlement_usd"] == 2000.0
    assert aggregate["consumed_entitlement_usd"] == 2200.0
    assert aggregate["remaining_entitlement_usd"] == -200.0
    assert aggregate["entitlement_usage_percent"] == 110.0



@pytest.mark.django_db
def test_regrouping_accounts_reuses_account_user_snapshots():
    config = AppSettings.load()
    first_pool = QuotaPool.objects.create(name="A+B")
    second_pool = QuotaPool.objects.create(name="C")
    first = create_monitored_account(71, name="A", pool=first_pool)
    second = create_monitored_account(72, name="B", pool=first_pool)
    third = create_monitored_account(73, name="C", pool=second_pool)
    participant = create_participant(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("40"),
        latest_balance_usd=Decimal("10"),
        account=first,
    )
    for account in (second, third):
        AccountParticipant.objects.create(
            account=account,
            participant=participant,
        )
    PoolParticipant.objects.create(
        pool=second_pool,
        participant=participant,
        share_percent=Decimal("20"),
    )
    now = timezone.now().replace(microsecond=0)
    for index, (account, share) in enumerate(
        (
            (first, Decimal("40")),
            (second, Decimal("40")),
            (third, Decimal("20")),
        )
    ):
        create_account_snapshot(
            account=account,
            participant=participant,
            share_percent=share,
            recommended=Decimal("0"),
            recommended_min=Decimal("0"),
            recommended_max=Decimal("0"),
            charged_percent=Decimal("10"),
            observed_at=now + timedelta(seconds=index),
        )

    from monitor.serializers import QuotaAllocationWriteSerializer

    serializer = QuotaAllocationWriteSerializer(
        data={
            "pools": [
                {
                    "id": first_pool.id,
                    "name": "A",
                    "account_ids": [first.id],
                    "allocations": [],
                },
                {
                    "id": second_pool.id,
                    "name": "B+C",
                    "account_ids": [second.id, third.id],
                    "allocations": [
                        {
                            "participant_id": participant.id,
                            "share_percent": "30",
                        }
                    ],
                },
            ]
        }
    )
    assert serializer.is_valid(), serializer.errors
    serializer.apply()

    aggregate, snapshots = aggregate_recommendation(participant, config)

    assert aggregate is not None
    assert aggregate["recommendation_complete"] is True
    assert {item.observation.account_id for item in snapshots} == {72, 73}
    sources = {
        item["external_account_id"]: item for item in aggregate["sources"]
    }
    assert set(sources) == {72, 73}
    assert all(item["contract_share_percent"] == 30.0 for item in sources.values())
    assert all(item["net_position_usd"] == 400.0 for item in sources.values())


@pytest.mark.django_db
def test_share_change_can_reapply_reused_account_snapshot(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    account = create_monitored_account(71, name="主账号")
    participant = create_participant(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
        account=account,
    )
    snapshot = create_account_snapshot(
        account=account,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("100"),
        recommended_min=Decimal("90"),
        recommended_max=Decimal("110"),
        charged_percent=Decimal("10"),
        observed_at=timezone.now().replace(microsecond=0),
    )
    snapshot.recommendation_applied = True
    snapshot.save(update_fields=["recommendation_applied"])
    old_operation = ParticipantBalanceOperation.objects.create(
        participant=participant,
        sub2api_user_id=participant.sub2api_user_id,
        requested_balance_usd=Decimal("100"),
        confirmed_balance_usd=Decimal("100"),
        state="committed",
        remote_confirmed_at=timezone.now(),
        committed_at=timezone.now(),
    )
    ParticipantBalanceOperationSource.objects.create(
        operation=old_operation,
        account=account,
        account_external_id=account.external_account_id,
        share_percent=Decimal("50"),
        base_revision=0,
        snapshot=snapshot,
        contribution_usd=Decimal("100"),
    )

    from monitor.serializers import QuotaAllocationWriteSerializer

    serializer = QuotaAllocationWriteSerializer(
        data={
            "pools": [
                {
                    "id": account.pool_id,
                    "name": account.pool.name,
                    "account_ids": [account.id],
                    "allocations": [
                        {
                            "participant_id": participant.id,
                            "share_percent": "40",
                        }
                    ],
                }
            ]
        }
    )
    assert serializer.is_valid(), serializer.errors
    serializer.apply()

    aggregate, snapshots = aggregate_recommendation(participant, AppSettings.load())
    assert aggregate is not None
    assert snapshots == [snapshot]
    assert aggregate["recommendation_applied"] is False
    assert aggregate["needs_manual_update"] is True

    calls: list[tuple[int, Decimal]] = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            calls.append((user_id, balance))
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _response = jwt_login(client)
    response = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert response.status_code == 200, response.json()
    assert calls == [
        (
            participant.sub2api_user_id,
            Decimal(str(aggregate["recommended_balance_usd"])),
        )
    ]
    operations = list(
        ParticipantBalanceOperation.objects.order_by("created_at", "id")
    )
    assert len(operations) == 2
    assert list(
        operations[-1].sources.values_list(
            "account_external_id",
            "share_percent",
        )
    ) == [(account.external_account_id, Decimal("40"))]
    participant.refresh_from_db()
    applied, _snapshots = aggregate_recommendation(
        participant,
        AppSettings.load(),
    )
    assert applied is not None
    assert applied["recommendation_applied"] is True
    assert applied["needs_manual_update"] is False

@pytest.mark.django_db
def test_applying_aggregate_recommendation_writes_one_global_balance_and_two_sources(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    pool = QuotaPool.objects.create(name="主备用混池")
    accounts = [
        create_monitored_account(71, name="主账号", pool=pool),
        create_monitored_account(72, name="备用账号", pool=pool),
    ]
    create_monitored_account(73, name="无关独立账号")
    participant = create_participant(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
        account=accounts[0],
    )
    now = timezone.now().replace(microsecond=0)
    snapshots = []
    for index, account in enumerate(accounts):
        AccountParticipant.objects.get_or_create(
            account=account,
            participant=participant,
        )
        snapshots.append(
            create_account_snapshot(
                account=account,
                participant=participant,
                share_percent=Decimal("50"),
                recommended=Decimal("0"),
                recommended_min=Decimal("0"),
                recommended_max=Decimal("0"),
                charged_percent=Decimal("10"),
                observed_at=now + timedelta(seconds=index),
            )
        )
    aggregate, _source_snapshots = aggregate_recommendation(
        participant,
        AppSettings.load(),
    )
    assert aggregate is not None
    expected = Decimal(str(aggregate["recommended_balance_usd"]))

    calls: list[tuple[int, Decimal]] = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            calls.append((user_id, balance))
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _response = jwt_login(client)
    response = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert response.status_code == 200, response.json()
    assert calls == [(501, expected)]
    operation = ParticipantBalanceOperation.objects.get()
    assert operation.state == "committed"
    assert operation.requested_balance_usd == expected
    assert set(
        operation.sources.values_list("account_external_id", flat=True)
    ) == {71, 72}
    assert sum(
        operation.sources.values_list("contribution_usd", flat=True),
        Decimal("0"),
    ) == expected
    participant.refresh_from_db()
    assert participant.latest_balance_usd == expected
    for snapshot in snapshots:
        snapshot.refresh_from_db()
        assert snapshot.recommendation_applied is True
        assert snapshot.current_balance_usd == expected


@pytest.mark.django_db
def test_applying_exhausted_contract_sets_global_balance_to_zero(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    account = create_monitored_account(71, name="主账号")
    participant = create_participant(
        name="exhausted rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("80"),
        account=account,
    )
    AccountParticipant.objects.get_or_create(
        account=account,
        participant=participant,
    )
    snapshot = create_account_snapshot(
        account=account,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("0"),
        recommended_min=Decimal("0"),
        recommended_max=Decimal("0"),
        charged_percent=Decimal("60"),
        observed_at=timezone.now().replace(microsecond=0),
    )
    calls: list[tuple[int, Decimal]] = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            calls.append((user_id, balance))
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _response = jwt_login(client)

    response = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert response.status_code == 200, response.json()
    assert calls == [(501, Decimal("0"))]
    operation = ParticipantBalanceOperation.objects.get()
    assert operation.state == "committed"
    assert operation.requested_balance_usd == Decimal("0")
    participant.refresh_from_db()
    snapshot.refresh_from_db()
    assert participant.latest_balance_usd == Decimal("0")
    assert snapshot.current_balance_usd == Decimal("0")
    assert snapshot.recommendation_applied is True


@pytest.mark.django_db
def test_monitor_run_samples_only_participants_allocated_to_each_pool(
    monkeypatch,
):
    config = AppSettings.load()
    config.fast_correction_enabled = False
    config.save(update_fields=["fast_correction_enabled"])
    first = create_monitored_account(
        71,
        name="主账号",
        quota_query_mode="passive",
    )
    second = create_monitored_account(
        72,
        name="备用账号",
        quota_query_mode="direct",
    )
    first_participant = create_participant(
        name="first",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        account=first,
    )
    second_participant = create_participant(
        name="second",
        sub2api_user_id=502,
        share_percent=Decimal("50"),
        account=first,
    )
    PoolParticipant.objects.create(
        pool=second.pool,
        participant=second_participant,
        share_percent=Decimal("100"),
    )
    captured_windows: list[tuple[int, str]] = []
    user_costs = {
        71: {501: Decimal("40"), 502: Decimal("0")},
        72: {501: Decimal("0"), 502: Decimal("60")},
    }

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, account_id, mode):
            captured_windows.append((account_id, mode))
            reset_at = int((timezone.now() + timedelta(days=4)).timestamp())
            return WeeklyWindow(
                used_percent=Decimal("20"),
                window_seconds=604800,
                reset_after_seconds=4 * 86400,
                reset_at=reset_at,
                slot="weekly",
                sampled_at=timezone.now().isoformat(),
            )

        def usage_stats(self, *, account_id, user_id=None, **_kwargs):
            value = (
                sum(user_costs[account_id].values(), Decimal("0"))
                if user_id is None
                else user_costs[account_id][user_id]
            )
            return UsageStats(total_cost=value, total_actual_cost=value)

        def all_user_usage_stats(self, *, account_id, **_kwargs):
            return [
                Sub2APIUserUsage(
                    user_id=user_id,
                    email=f"{user_id}@example.com",
                    username=f"user-{user_id}",
                    stats=UsageStats(
                        total_cost=value,
                        total_actual_cost=value,
                    ),
                )
                for user_id, value in user_costs[account_id].items()
            ]

        def user_balance(self, user_id):
            return UserBalance(
                balance=Decimal(user_id - 400),
                frozen_balance=Decimal("0"),
            )

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "completed"
    assert result["error_count"] == 0
    assert captured_windows == [(71, "passive"), (72, "direct")]
    observations = {
        item.account_id: item
        for item in Observation.objects.prefetch_related(
            "participant_snapshots"
        )
    }
    assert set(observations) == {71, 72}
    assert {
        item.participant_id
        for item in observations[71].participant_snapshots.all()
    } == {first_participant.id, second_participant.id}
    assert {
        item.participant_id
        for item in observations[72].participant_snapshots.all()
    } == {second_participant.id}
    assert AccountParticipant.objects.get(
        account=first,
        participant=first_participant,
    ).latest_selected_cost == Decimal("40.000000")
    assert AccountParticipant.objects.get(
        account=first,
        participant=second_participant,
    ).latest_selected_cost == Decimal("0.000000")
    assert not AccountParticipant.objects.filter(
        account=second,
        participant=first_participant,
    ).exists()
    assert AccountParticipant.objects.get(
        account=second,
        participant=second_participant,
    ).latest_selected_cost == Decimal("60.000000")


@pytest.mark.django_db
def test_replaying_one_account_keeps_newer_global_balance_from_another_account():
    config = AppSettings.load()
    pool = QuotaPool.objects.create(name="主备用混池")
    first = create_monitored_account(71, name="主账号", pool=pool)
    second = create_monitored_account(72, name="备用账号", pool=pool)
    participant = create_participant(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
        account=first,
    )
    first_membership = AccountParticipant.objects.get(
        account=first,
        participant=participant,
    )
    second_membership = AccountParticipant.objects.create(
        account=second,
        participant=participant,
        latest_selected_cost=Decimal("123"),
    )
    now = timezone.now().replace(microsecond=0)
    first_snapshot = create_account_snapshot(
        account=first,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("100"),
        recommended_min=Decimal("90"),
        recommended_max=Decimal("110"),
        charged_percent=Decimal("10"),
        observed_at=now,
    )
    participant.latest_balance_usd = Decimal("20")
    participant.save(update_fields=["latest_balance_usd"])
    create_account_snapshot(
        account=second,
        participant=participant,
        share_percent=Decimal("50"),
        recommended=Decimal("50"),
        recommended_min=Decimal("45"),
        recommended_max=Decimal("55"),
        charged_percent=Decimal("10"),
        observed_at=now + timedelta(minutes=1),
    )

    rebuild_account(first.external_account_id, config)

    participant.refresh_from_db()
    first_membership.refresh_from_db()
    second_membership.refresh_from_db()
    first_snapshot.refresh_from_db()
    assert participant.latest_balance_usd == Decimal("20.000000")
    assert participant.last_checked_at == now + timedelta(minutes=1)
    assert first_membership.latest_selected_cost == first_snapshot.selected_cost
    assert second_membership.latest_selected_cost == Decimal("123.000000")
