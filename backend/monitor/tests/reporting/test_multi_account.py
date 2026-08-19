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
    ParticipantSnapshot,
)
from monitor.replay import rebuild_account
from monitor.reporting import aggregate_recommendation
from monitor.tests.helpers import jwt_login


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
        share_percent=share_percent,
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
def test_account_and_participant_apis_expose_one_global_pooled_contract():
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
                "share_percent": "40",
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
    assert data["share_percent"] == 40.0
    assert data["is_owner"] is True
    assert data["snapshot"]["recommendation_complete"] is False
    assert {
        item["external_account_id"] for item in data["account_breakdowns"]
    } == {71, 72}
    assert all(
        "share_percent" not in item and "is_owner" not in item
        for item in data["account_breakdowns"]
    )
    assert AccountParticipant.objects.filter(
        participant_id=data["id"]
    ).count() == 2

    immutable_id = client.put(
        f"/api/settings/monitored-accounts/{account_ids[0]}",
        data=json.dumps({"external_account_id": 99}),
        content_type="application/json",
        **headers,
    )
    assert immutable_id.status_code == 400
    assert (
        MonitoredAccount.objects.get(pk=account_ids[0]).external_account_id
        == 71
    )

    duplicate_user = client.post(
        "/api/participants",
        data=json.dumps(
            {
                "name": "重复绑定",
                "sub2api_user_id": 501,
                "share_percent": "10",
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert duplicate_user.status_code == 400


@pytest.mark.django_db
def test_aggregate_recommendation_nets_accounts_before_global_zero_clamp():
    config = AppSettings.load()
    participant = Participant.objects.create(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
    )
    first = MonitoredAccount.objects.create(
        external_account_id=71,
        name="主账号",
    )
    second = MonitoredAccount.objects.create(
        external_account_id=72,
        name="备用账号",
    )
    for account in (first, second):
        AccountParticipant.objects.create(
            account=account,
            participant=participant,
        )
    now = timezone.now().replace(microsecond=0)
    first_snapshot = create_account_snapshot(
        account=first,
        participant=participant,
        share_percent=participant.share_percent,
        recommended=Decimal("0"),
        recommended_min=Decimal("0"),
        recommended_max=Decimal("0"),
        charged_percent=Decimal("60"),
        observed_at=now,
    )
    create_account_snapshot(
        account=second,
        participant=participant,
        share_percent=participant.share_percent,
        recommended=Decimal("0"),
        recommended_min=Decimal("0"),
        recommended_max=Decimal("0"),
        charged_percent=Decimal("20"),
        observed_at=now + timedelta(seconds=1),
    )

    aggregate, snapshots = aggregate_recommendation(participant, config)
    assert aggregate is not None
    assert aggregate["allocation_model"] == "pooled_account_sum"
    assert aggregate["recommendation_complete"] is True
    assert aggregate["recommended_balance_usd"] == 380.0
    assert aggregate["recommended_balance_min_usd"] == 342.0
    assert aggregate["recommended_balance_max_usd"] == 418.0
    assert aggregate["is_overused"] is False
    assert aggregate["needs_manual_update"] is True
    assert {item.observation.account_id for item in snapshots} == {71, 72}
    source_by_account = {
        item["external_account_id"]: item for item in aggregate["sources"]
    }
    assert source_by_account[71]["net_position_usd"] == -200.0
    assert source_by_account[72]["net_position_usd"] == 600.0
    assert source_by_account[71]["contribution_usd"] == 0.0
    assert source_by_account[72]["contribution_usd"] == 380.0

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


@pytest.mark.django_db
def test_applying_aggregate_recommendation_writes_one_global_balance_and_two_sources(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = Participant.objects.create(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
    )
    accounts = [
        MonitoredAccount.objects.create(external_account_id=71, name="主账号"),
        MonitoredAccount.objects.create(external_account_id=72, name="备用账号"),
    ]
    now = timezone.now().replace(microsecond=0)
    snapshots = []
    for index, account in enumerate(accounts):
        AccountParticipant.objects.create(
            account=account,
            participant=participant,
        )
        snapshots.append(
            create_account_snapshot(
                account=account,
                participant=participant,
                share_percent=participant.share_percent,
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
    participant = Participant.objects.create(
        name="exhausted rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("80"),
    )
    account = MonitoredAccount.objects.create(
        external_account_id=71,
        name="主账号",
    )
    AccountParticipant.objects.create(
        account=account,
        participant=participant,
    )
    snapshot = create_account_snapshot(
        account=account,
        participant=participant,
        share_percent=participant.share_percent,
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
def test_monitor_run_samples_every_global_participant_on_each_account(
    monkeypatch,
):
    config = AppSettings.load()
    config.fast_correction_enabled = False
    config.save(update_fields=["fast_correction_enabled"])
    first = MonitoredAccount.objects.create(
        external_account_id=71,
        name="主账号",
        quota_query_mode="passive",
    )
    second = MonitoredAccount.objects.create(
        external_account_id=72,
        name="备用账号",
        quota_query_mode="direct",
    )
    first_participant = Participant.objects.create(
        name="first",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
    )
    second_participant = Participant.objects.create(
        name="second",
        sub2api_user_id=502,
        share_percent=Decimal("50"),
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
    expected_participants = {first_participant.id, second_participant.id}
    assert {
        item.participant_id
        for item in observations[71].participant_snapshots.all()
    } == expected_participants
    assert {
        item.participant_id
        for item in observations[72].participant_snapshots.all()
    } == expected_participants
    assert AccountParticipant.objects.get(
        account=first,
        participant=first_participant,
    ).latest_selected_cost == Decimal("40.000000")
    assert AccountParticipant.objects.get(
        account=first,
        participant=second_participant,
    ).latest_selected_cost == Decimal("0.000000")
    assert AccountParticipant.objects.get(
        account=second,
        participant=first_participant,
    ).latest_selected_cost == Decimal("0.000000")
    assert AccountParticipant.objects.get(
        account=second,
        participant=second_participant,
    ).latest_selected_cost == Decimal("60.000000")


@pytest.mark.django_db
def test_replaying_one_account_keeps_newer_global_balance_from_another_account():
    config = AppSettings.load()
    participant = Participant.objects.create(
        name="rider",
        sub2api_user_id=501,
        share_percent=Decimal("50"),
        latest_balance_usd=Decimal("10"),
    )
    first = MonitoredAccount.objects.create(
        external_account_id=71,
        name="主账号",
    )
    second = MonitoredAccount.objects.create(
        external_account_id=72,
        name="备用账号",
    )
    first_membership = AccountParticipant.objects.create(
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
