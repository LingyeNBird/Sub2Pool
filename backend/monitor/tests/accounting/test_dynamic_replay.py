import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.replay import (
    RATE_METHOD,
    rebuild_account,
)
from monitor.integrations.sub2api import (
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    jwt_login,
)


@pytest.mark.django_db
def test_integer_percent_plateau_uses_cumulative_cost_for_capacity(monkeypatch):
    """16% 平台期内的消费不能在跳到 17% 时被漏掉并产生 $687 的错误总额。"""
    config = AppSettings.load()
    create_monitored_account(7)
    config.cost_basis = "actual"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=50,
    is_owner=True,)
    reset_at = timezone.now() + timedelta(days=4)
    used_values = [Decimal("16"), Decimal("16"), Decimal("17")]
    cost_values = [
        Decimal("419.409971"),
        Decimal("431.558149"),
        Decimal("438.431382"),
    ]

    class FakeClient:
        run_count = 0

        def __init__(self, _config):
            self.step = type(self).run_count
            type(self).run_count += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                used_values[self.step],
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            cost = cost_values[self.step]
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1561.568618"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    for _ in range(3):
        run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    observations = list(Observation.objects.order_by("observed_at"))

    assert observations[0].sample_usd_per_percent == Decimal("26.213123")
    assert observations[1].sample_usd_per_percent == Decimal("26.972384")
    assert observations[2].delta_cost == Decimal("6.873233")
    assert observations[2].sample_usd_per_percent == Decimal("25.790081")
    assert Decimal("14") <= observations[2].effective_usd_per_percent <= Decimal(
        "40"
    )
    assert abs(
        observations[2].effective_usd_per_percent
        - observations[2].sample_usd_per_percent
    ) < Decimal("5")
    assert observations[2].selected_total_cost == cost_values[-1]
    assert observations[2].raw_window["rate_method"] == RATE_METHOD

    snapshot = ParticipantSnapshot.objects.get(
        observation=observations[2],
        participant=owner,
    )
    assert (
        snapshot.charged_percent_lower
        <= snapshot.charged_cycle_percent
        <= snapshot.charged_percent_upper
    )
    assert snapshot.remaining_share_percent == (
        snapshot.share_percent - snapshot.charged_cycle_percent
    )


@pytest.mark.django_db
def test_statistics_use_endpoint_ratio_independent_of_particle_filter():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()

    now = timezone.now()
    reset_at = now + timedelta(days=4)
    for index, (used_percent, cost) in enumerate(
        (
            (Decimal("10"), Decimal("100")),
            (Decimal("20"), Decimal("600")),
        )
    ):
        Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=now + timedelta(minutes=index),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=used_percent,
            raw_selected_total_cost=cost,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            effective_usd_per_percent=config.initial_usd_per_percent,
        )

    rebuild_account(7, config)

    observations = list(Observation.objects.order_by("observed_at", "id"))
    assert observations[0].sample_usd_per_percent == Decimal("10.000000")
    assert observations[1].sample_usd_per_percent == Decimal("30.000000")
    assert all(item.model_diagnostics for item in observations)

    statistics = client.get("/api/statistics", **headers).json()["data"]
    cycle = statistics["capacity_summary"]["cycle"]
    assert cycle["calculation_model"] == "endpoint_ratio"
    assert cycle["estimate_usd"] == 3000.0
    closing_basis = statistics["capacity_series"][-1]["basis"]
    assert closing_basis["calculation_model"] == "endpoint_ratio"
    assert closing_basis["estimate_usd"] == 3000.0


@pytest.mark.django_db
def test_midcycle_initialization_assigns_existing_ten_percent_to_owner(
    monkeypatch,
):
    config = AppSettings.load()
    create_monitored_account(7)
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=50,
    is_owner=True,)
    rider = create_participant(name="车友",
    sub2api_user_id=2,
    share_percent=50,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("10"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("200"), 1: Decimal("200"), 2: Decimal("0")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("700") if user_id == 1 else Decimal("800")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    owner_snapshot = snapshots[owner.id]
    rider_snapshot = snapshots[rider.id]
    assert Decimal("9") <= owner_snapshot.charged_cycle_percent <= Decimal(
        "11"
    )
    assert (
        owner_snapshot.charged_percent_lower
        <= owner_snapshot.charged_cycle_percent
        <= owner_snapshot.charged_percent_upper
    )
    assert owner_snapshot.remaining_share_percent == (
        owner_snapshot.share_percent - owner_snapshot.charged_cycle_percent
    )
    assert rider_snapshot.charged_cycle_percent == Decimal("0")
    assert rider_snapshot.remaining_share_percent == Decimal("50")


@pytest.mark.django_db
def test_unmapped_user_usage_is_saved_without_retroactive_participant_history(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.initial_usd_per_percent = Decimal("20")
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=60,
    is_owner=True,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        balance_reads: list[int] = []

        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("20"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            assert user_id is None
            return UsageStats(Decimal("400"), Decimal("400"))

        def all_user_usage_stats(self, **_kwargs):
            return [
                Sub2APIUserUsage(
                    1,
                    "owner@example.com",
                    "owner",
                    UsageStats(Decimal("300"), Decimal("300")),
                ),
                Sub2APIUserUsage(
                    2,
                    "rider@example.com",
                    "rider",
                    UsageStats(Decimal("100"), Decimal("100")),
                ),
            ]

        def user_balance(self, user_id):
            type(self).balance_reads.append(user_id)
            return UserBalance(Decimal("600"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    assert FakeClient.balance_reads == [1]
    assert set(
        Sub2APIUserUsageSample.objects.values_list(
            "sub2api_user_id",
            flat=True,
        )
    ) == {1, 2}
    assert not ParticipantSnapshot.objects.filter(
        participant__sub2api_user_id=2,
    ).exists()

    client = Client()
    headers, _ = jwt_login(client)
    response = client.post(
        "/api/participants",
        data=json.dumps(
            {
                "name": "车友",
                "email": "rider@example.com",
                "sub2api_user_id": 2,
                "sub2api_username": "rider",
                "sub2api_email": "rider@example.com",
                "enabled": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 201
    rider = Participant.objects.get(sub2api_user_id=2)
    latest = Observation.objects.get()
    assert set(
        latest.participant_snapshots.values_list("participant_id", flat=True)
    ) == {owner.id}
    assert not ParticipantSnapshot.objects.filter(participant=rider).exists()
    assert not ParticipantUsageSample.objects.filter(participant=rider).exists()
    assert Sub2APIUserUsageSample.objects.filter(
        sub2api_user_id=2,
        total_actual_cost=Decimal("100"),
    ).exists()


@pytest.mark.django_db
def test_adding_participant_midcycle_replays_the_complete_segment(monkeypatch):
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        run_count = 0

        def __init__(self, _config):
            self.step = type(self).run_count
            type(self).run_count += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("20"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = (
                {None: Decimal("400"), 1: Decimal("400")}
                if self.step == 0
                else {
                    None: Decimal("500"),
                    1: Decimal("400"),
                    2: Decimal("100"),
                }
            )
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    first_observation = Observation.objects.get()
    first_observation.sample_note = "必须由完整区间重放覆盖"
    first_observation.save(update_fields=["sample_note"])

    owner_allocation = owner.pool_allocations.get()
    owner_allocation.share_percent = Decimal("60")
    owner_allocation.save(update_fields=["share_percent"])
    owner_allocation.pool.contract_revision += 1
    owner_allocation.pool.save(update_fields=["contract_revision", "updated_at"])
    rider = create_participant(name="车友",
    sub2api_user_id=2,
    share_percent=40,)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    first_observation.refresh_from_db()
    assert first_observation.sample_note != "必须由完整区间重放覆盖"
    assert Observation.objects.count() == 2
    latest = Observation.objects.order_by("-observed_at", "-id").first()
    assert latest is not None
    snapshots = {
        item.participant_id: item
        for item in latest.participant_snapshots.all()
    }
    assert snapshots[owner.id].selected_cost == Decimal("400.000000")
    assert snapshots[rider.id].selected_cost == Decimal("100.000000")
    assert snapshots[owner.id].charged_cycle_percent > snapshots[
        rider.id
    ].charged_cycle_percent
    assert latest.model_diagnostics["algorithm"] == RATE_METHOD


@pytest.mark.django_db
def test_dynamic_model_removes_safety_factor_for_only_remaining_participant(
    monkeypatch,
):
    config = AppSettings.load()
    create_monitored_account(7)
    config.safety_factor = Decimal("0.5")
    config.save()
    exhausted = create_participant(name="已跑完",
    sub2api_user_id=1,
    share_percent=10,)
    remaining = create_participant(name="唯一剩余",
    sub2api_user_id=2,
    share_percent=90,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("20"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {
                None: Decimal("400"),
                1: Decimal("400"),
                2: Decimal("0"),
            }
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("5") if user_id == exhausted.sub2api_user_id else Decimal("0")
            return UserBalance(balance, balance)

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)

    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    observation = Observation.objects.get()
    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    assert snapshots[exhausted.id].remaining_share_percent == Decimal("0")
    exhausted_snapshot = snapshots[exhausted.id]
    assert exhausted_snapshot.recommended_balance_usd == Decimal("0")
    assert exhausted_snapshot.recommended_balance_min_usd == Decimal("0")
    assert exhausted_snapshot.recommended_balance_max_usd == Decimal("0")
    assert exhausted_snapshot.needs_manual_update is True
    assert exhausted_snapshot.reason == (
        "百分比权益已用尽，建议清零 Sub2API 用户余额"
    )
    remaining_snapshot = snapshots[remaining.id]
    expected = (
        remaining_snapshot.remaining_share_percent
        * observation.effective_usd_per_percent
    ).quantize(Decimal("0.01"))
    assert remaining_snapshot.recommended_balance_usd == expected
    assert remaining_snapshot.recommended_balance_usd > expected * Decimal(
        "0.9"
    )
