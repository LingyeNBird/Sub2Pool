from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.integrations.sub2api import UsageStats, UserBalance, WeeklyWindow
from monitor.models import (
    AppSettings,
    Observation,
    ParticipantSnapshot,
    ParticipantUsageSample,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
    create_recommendation_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_dashboard_only_lists_participants_that_need_manual_adjustment():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    actionable = create_participant(name="需要调整",
    sub2api_user_id=51,
    share_percent=50,)
    settled = create_participant(name="当前无需调整",
    sub2api_user_id=52,
    share_percent=40,
    latest_balance_usd=Decimal("760"),)
    create_participant(name="等待测算",
    sub2api_user_id=53,
    share_percent=10,)
    create_recommendation_snapshot(actionable)
    settled_snapshot = create_recommendation_snapshot(
        settled,
        recommended=Decimal("80"),
    )
    settled_snapshot.needs_manual_update = False
    settled_snapshot.save(update_fields=["needs_manual_update"])
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers)

    assert dashboard.status_code == 200
    assert [
        item["id"] for item in dashboard.json()["data"]["participants"]
    ] == [actionable.id]


@pytest.mark.django_db
def test_usage_deviation_is_not_a_dashboard_suggestion_and_can_clear():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.weekly_quota_model = "time_varying"
    config.save()
    confirmed = create_participant(name="确认偏差",
    sub2api_user_id=61,
    share_percent=40,)
    uncertain = create_participant(name="尚不确定",
    sub2api_user_id=62,
    share_percent=40,)
    now = timezone.now()
    starts_at = now - timedelta(days=4)
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=starts_at,
        upstream_used_percent=Decimal("50"),
        interval_used_percent=Decimal("50"),
        raw_selected_total_cost=Decimal("1000"),
        selected_total_cost=Decimal("1000"),
        total_standard_cost=Decimal("1000"),
        total_actual_cost=Decimal("1000"),
        effective_usd_per_percent=Decimal("20"),
    )
    create_participant_snapshot(observation=observation,
    participant=confirmed,
    raw_selected_cost=Decimal("860"),
    selected_cost=Decimal("860"),
    charged_cycle_percent=Decimal("43"),
    charged_percent_lower=Decimal("41"),
    charged_percent_upper=Decimal("45"),
    remaining_share_percent=Decimal("0"),
    needs_manual_update=False,)
    create_participant_snapshot(observation=observation,
    participant=uncertain,
    raw_selected_cost=Decimal("820"),
    selected_cost=Decimal("820"),
    charged_cycle_percent=Decimal("41"),
    charged_percent_lower=Decimal("39"),
    charged_percent_upper=Decimal("43"),
    remaining_share_percent=Decimal("0"),
    needs_manual_update=False,)
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["participants"] == []
    visible = client.get("/api/participants", **headers).json()["data"]
    confirmed_snapshot = next(
        item["account_breakdowns"][0]["snapshot"]
        for item in visible
        if item["id"] == confirmed.id
    )
    uncertain_snapshot = next(
        item["account_breakdowns"][0]["snapshot"]
        for item in visible
        if item["id"] == uncertain.id
    )
    assert confirmed_snapshot["is_overused"] is True
    assert confirmed_snapshot["overused_percent"] == 3.0
    assert confirmed_snapshot["overused_percent_min"] == 1.0
    assert confirmed_snapshot["overused_percent_max"] == 5.0
    assert uncertain_snapshot["is_overused"] is False

    revised = Observation.objects.create(
        account_id=7,
        observed_at=now + timedelta(minutes=5),
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=starts_at,
        upstream_used_percent=Decimal("51"),
        interval_used_percent=Decimal("51"),
        raw_selected_total_cost=Decimal("1020"),
        selected_total_cost=Decimal("1020"),
        total_standard_cost=Decimal("1020"),
        total_actual_cost=Decimal("1020"),
        effective_usd_per_percent=Decimal("21"),
    )
    create_participant_snapshot(observation=revised,
    participant=confirmed,
    raw_selected_cost=Decimal("870"),
    selected_cost=Decimal("870"),
    charged_cycle_percent=Decimal("39"),
    charged_percent_lower=Decimal("38"),
    charged_percent_upper=Decimal("40"),
    remaining_share_percent=Decimal("1"),
    needs_manual_update=False,)

    revised_visible = client.get("/api/participants", **headers).json()["data"]
    revised_snapshot = next(
        item["account_breakdowns"][0]["snapshot"]
        for item in revised_visible
        if item["id"] == confirmed.id
    )
    assert revised_snapshot["is_overused"] is False



@pytest.mark.django_db
def test_dashboard_uses_particle_filter_residual_attribution():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.weekly_quota_model = "time_varying"
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,)
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=now - timedelta(days=4),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
        estimated_used_percent=Decimal("20"),
        model_diagnostics={"residual_attributed_percent": 7.25},
    )
    create_participant_snapshot(observation=observation,
    participant=participant,
    raw_selected_cost=Decimal("200"),
    selected_cost=Decimal("200"),
    charged_cycle_percent=Decimal("12"),
    remaining_share_percent=Decimal("38"),)
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers)

    assert dashboard.status_code == 200
    assert (
        dashboard.json()["data"]["cycle"]["unattributed_used_percent"]
        == 7.25
    )
@pytest.mark.django_db
def test_constant_average_model_changes_only_presented_attribution():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.weekly_quota_model = "constant_average"
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=4),
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("15"),
    )
    stored = create_participant_snapshot(observation=observation,
    participant=participant,
    raw_selected_cost=Decimal("100"),
    selected_cost=Decimal("100"),
    charged_cycle_percent=Decimal("12"),
    remaining_share_percent=Decimal("38"),
    current_balance_usd=Decimal("80"),
    recommended_balance_usd=Decimal("722"),
    needs_manual_update=True,)
    client = Client()
    headers, _ = jwt_login(client)

    constant = client.get("/api/participants", **headers).json()["data"][0]

    assert constant["snapshot"]["allocation_model"] == "partitioned_pool_sum"
    constant_snapshot = constant["account_breakdowns"][0]["snapshot"]
    assert constant_snapshot["allocation_model"] == "constant_average"
    assert constant_snapshot["charged_cycle_percent"] == 5.0
    assert constant_snapshot["remaining_share_percent"] == 45.0
    assert constant_snapshot["recommended_balance_usd"] == 834.55
    assert constant_snapshot["recommended_balance_min_usd"] == 814.09
    assert constant_snapshot["recommended_balance_max_usd"] == 855.0
    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["weekly_quota_model"] == "constant_average"
    assert dashboard["cycle"]["effective_usd_per_percent"] == 20.0
    assert dashboard["cycle"]["interval_used_percent"] == 20.0
    assert dashboard["cycle"]["rate_calculated"] is True
    assert dashboard["cycle"]["estimated_used_percent"] == 20.0
    assert dashboard["cycle"]["unattributed_used_percent"] == 15.0
    dashboard_snapshot = dashboard["participants"][0]["account_breakdowns"][0][
        "snapshot"
    ]
    assert dashboard_snapshot["recommended_balance_usd"] == 834.55
    assert dashboard_snapshot["recommended_balance_min_usd"] == 814.09
    assert dashboard_snapshot["recommended_balance_max_usd"] == 855.0
    stored.refresh_from_db()
    assert stored.charged_cycle_percent == Decimal("12")
    assert stored.recommended_balance_usd == Decimal("722")

    config.weekly_quota_model = "time_varying"
    config.save(update_fields=["weekly_quota_model"])
    time_varying = client.get(
        "/api/participants",
        **headers,
    ).json()["data"][0]
    assert time_varying["snapshot"]["allocation_model"] == "partitioned_pool_sum"
    time_varying_snapshot = time_varying["account_breakdowns"][0]["snapshot"]
    assert time_varying_snapshot["allocation_model"] == "time_varying"
    assert time_varying_snapshot["charged_cycle_percent"] == 12.0
    time_varying_dashboard = client.get(
        "/api/dashboard",
        **headers,
    ).json()["data"]
    assert (
        time_varying_dashboard["cycle"]["effective_usd_per_percent"] == 15.0
    )
    assert time_varying_dashboard["cycle"]["rate_calculated"] is False
    time_varying_account = time_varying_dashboard["participants"][0][
        "account_breakdowns"
    ][0]["snapshot"]
    assert time_varying_account["recommended_balance_usd"] == 722.0
    assert time_varying_account["recommended_balance_min_usd"] == 722.0
    assert time_varying_account["recommended_balance_max_usd"] == 722.0

@pytest.mark.django_db
def test_initial_observation_respects_capacity_bounds_and_builds_recommendations(monkeypatch):
    config = AppSettings.load()
    create_monitored_account(7)
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = create_participant(name="车主", sub2api_user_id=1, share_percent=50, is_owner=True)
    rider = create_participant(name="车友", sub2api_user_id=2, share_percent=50)
    now = datetime(2026, 8, 10, tzinfo=datetime_timezone.utc)
    reset_after_seconds = int(timedelta(days=4).total_seconds())
    reset_at = now + timedelta(seconds=reset_after_seconds)
    monkeypatch.setattr(timezone, "now", lambda: now)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, account_id, mode):
            assert account_id == 7
            assert mode == "passive"
            return WeeklyWindow(
                Decimal("40"),
                604800,
                reset_after_seconds,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("400"), 1: Decimal("300"), 2: Decimal("100")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("300") if user_id == 1 else Decimal("100")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    owner_snapshot = snapshots[owner.id]
    rider_snapshot = snapshots[rider.id]
    assert owner_snapshot.charged_cycle_percent > rider_snapshot.charged_cycle_percent
    assert float(
        owner_snapshot.charged_cycle_percent
        + rider_snapshot.charged_cycle_percent
    ) == pytest.approx(40, abs=1)
    assert (
        owner_snapshot.charged_percent_lower
        <= owner_snapshot.charged_cycle_percent
        <= owner_snapshot.charged_percent_upper
    )
    assert (
        rider_snapshot.charged_percent_lower
        <= rider_snapshot.charged_cycle_percent
        <= rider_snapshot.charged_percent_upper
    )
    assert owner_snapshot.recommended_balance_usd is not None
    assert rider_snapshot.recommended_balance_usd is not None
    assert ParticipantUsageSample.objects.count() == 2


@pytest.mark.django_db
def test_constant_average_removes_safety_factor_for_only_remaining_participant():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.weekly_quota_model = "constant_average"
    config.safety_factor = Decimal("0.5")
    config.save()
    exhausted = create_participant(name="已跑完",
    sub2api_user_id=71,
    share_percent=50,)
    remaining = create_participant(name="唯一剩余",
    sub2api_user_id=72,
    share_percent=50,)
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=now - timedelta(days=4),
        upstream_used_percent=Decimal("80"),
        interval_used_percent=Decimal("80"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("5"),
    )
    create_participant_snapshot(observation=observation,
    participant=exhausted,
    raw_selected_cost=Decimal("300"),
    selected_cost=Decimal("300"),
    current_balance_usd=Decimal("5"),)
    create_participant_snapshot(observation=observation,
    participant=remaining,
    raw_selected_cost=Decimal("100"),
    selected_cost=Decimal("100"),
    current_balance_usd=Decimal("0"),)
    client = Client()
    headers, _ = jwt_login(client)

    rows = client.get("/api/participants", **headers).json()["data"]
    exhausted_snapshot = next(
        item["account_breakdowns"][0]["snapshot"]
        for item in rows
        if item["id"] == exhausted.id
    )
    assert exhausted_snapshot["remaining_share_percent"] == 0.0
    assert exhausted_snapshot["recommended_balance_usd"] == 0.0
    assert exhausted_snapshot["recommended_balance_min_usd"] == 0.0
    assert exhausted_snapshot["recommended_balance_max_usd"] == 0.0
    assert exhausted_snapshot["needs_manual_update"] is True
    assert exhausted_snapshot["reason"] == (
        "百分比权益已用尽，建议清零 Sub2API 用户余额"
    )
    snapshot = next(
        item["account_breakdowns"][0]["snapshot"]
        for item in rows
        if item["id"] == remaining.id
    )

    assert snapshot["remaining_share_percent"] == 30.0
    assert snapshot["recommended_balance_min_usd"] == 147.22
    assert snapshot["recommended_balance_max_usd"] == 150.0
    assert snapshot["recommended_balance_usd"] == 148.61
    assert snapshot["reason"] == (
        "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
    )
