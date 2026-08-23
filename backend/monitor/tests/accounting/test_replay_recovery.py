from io import StringIO
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import (
    AppSettings,
    Observation,
    ParticipantSnapshot,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from monitor.replay import (
    RATE_METHOD,
    rebuild_account,
)
from monitor.integrations.sub2api import (
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
)


@pytest.mark.django_db
def test_failed_initial_replay_leaves_observation_pending_without_version(
    monkeypatch,
):
    config = AppSettings.load()
    create_monitored_account(7)
    config.fast_correction_enabled = False
    config.save()
    create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    reset_at = timezone.now() + timedelta(days=3)

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
                259200,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            cost = Decimal("400") if user_id is None else Decimal("400")
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1000"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)

    def fail_replay(*_args, **_kwargs):
        raise ValueError("模拟派生计算失败")

    monkeypatch.setattr(
        "monitor.engine.rebuild_observation_suffix",
        fail_replay,
    )

    with pytest.raises(ValueError, match="模拟派生计算失败"):
        run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    observation = Observation.objects.get()
    assert observation.sample_note == "等待派生计算"
    assert "rate_method" not in observation.raw_window

    call_command("replayobservations", stdout=StringIO())
    observation.refresh_from_db()
    assert observation.raw_window["rate_method"] == RATE_METHOD
    assert observation.model_diagnostics["algorithm"] == RATE_METHOD


@pytest.mark.django_db
def test_startup_replay_recovers_pending_row_with_current_version_marker():
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("16"),
        sample_note="等待派生计算",
        raw_window={"rate_method": RATE_METHOD},
    )

    call_command("replayobservations", stdout=StringIO())

    observation.refresh_from_db()
    assert observation.sample_note != "等待派生计算"
    assert observation.model_diagnostics["algorithm"] == RATE_METHOD


@pytest.mark.django_db
def test_replay_handles_more_than_sqlite_expression_depth_sample_points():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    now = timezone.now().replace(microsecond=0)
    reset_at = now + timedelta(days=7)
    points = UsageSamplePoint.objects.bulk_create(
        [
            UsageSamplePoint(
                account_id=7,
                observed_at=now + timedelta(seconds=index),
            )
            for index in range(1001)
        ],
        batch_size=500,
    )
    observations = Observation.objects.bulk_create(
        [
            Observation(
                account_id=7,
                sample_point=point,
                source="manual",
                observed_at=point.observed_at,
                upstream_resets_at=reset_at,
                upstream_used_percent=Decimal("0"),
                raw_selected_total_cost=Decimal("0"),
                selected_total_cost=Decimal("0"),
                total_standard_cost=Decimal("0"),
                total_actual_cost=Decimal("0"),
                effective_usd_per_percent=Decimal("16"),
            )
            for point in points
        ],
        batch_size=100,
    )

    result = rebuild_account(7, config)

    assert result.rebuilt_observations == 1001
    assert result.latest_observation_id == observations[-1].id
    assert Observation.objects.filter(
        attribution_started_at=observations[0].observed_at,
    ).count() == 1001


@pytest.mark.django_db
def test_replay_uses_both_persisted_cost_bases():
    config = AppSettings.load()
    create_monitored_account(7)
    config.cost_basis = "actual"
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    now = timezone.now()
    reset_at = now + timedelta(days=3)
    observations = []
    for index, (actual, standard, percent) in enumerate(
        (
            (Decimal("100"), Decimal("150"), Decimal("5")),
            (Decimal("200"), Decimal("300"), Decimal("10")),
        )
    ):
        observed_at = now + timedelta(hours=index)
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=percent,
            raw_selected_total_cost=actual,
            selected_total_cost=actual,
            total_standard_cost=standard,
            total_actual_cost=actual,
            effective_usd_per_percent=Decimal("16"),
        )
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=actual,
        selected_cost=actual,
        current_balance_usd=Decimal("1000"),)
        Sub2APIUserUsageSample.objects.create(
            account_id=7,
            sub2api_user_id=1,
            observed_at=observed_at,
            window_started_at=reset_at - timedelta(days=7),
            window_resets_at=reset_at,
            total_standard_cost=standard,
            total_actual_cost=actual,
        )
        observations.append(observation)

    rebuild_account(7, config)
    observations[-1].refresh_from_db()
    actual_snapshot = ParticipantSnapshot.objects.get(
        observation=observations[-1],
        participant=participant,
    )
    assert observations[-1].selected_total_cost == Decimal("200")
    assert actual_snapshot.selected_cost == Decimal("200")

    config.cost_basis = "standard"
    config.save(update_fields=["cost_basis"])
    rebuild_account(7, config)
    observations[-1].refresh_from_db()
    standard_snapshot = ParticipantSnapshot.objects.get(
        observation=observations[-1],
        participant=participant,
    )
    assert observations[-1].selected_total_cost == Decimal("300")
    assert standard_snapshot.selected_cost == Decimal("300")


@pytest.mark.django_db
def test_replay_repairs_cumulative_cost_rollback_without_double_counting():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    now = timezone.now()
    reset_at = now + timedelta(days=3)
    observations = []
    for index, cost in enumerate(
        (Decimal("100"), Decimal("90"), Decimal("100"))
    ):
        observed_at = now + timedelta(hours=index)
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=Decimal("5"),
            raw_selected_total_cost=cost,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            effective_usd_per_percent=Decimal("16"),
        )
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=cost,
        selected_cost=cost,
        current_balance_usd=Decimal("1000"),)
        Sub2APIUserUsageSample.objects.create(
            account_id=7,
            sub2api_user_id=1,
            observed_at=observed_at,
            window_started_at=reset_at - timedelta(days=7),
            window_resets_at=reset_at,
            total_standard_cost=cost,
            total_actual_cost=cost,
        )
        observations.append(observation)

    rebuild_account(7, config)

    selected_totals = []
    selected_users = []
    for observation in observations:
        observation.refresh_from_db()
        selected_totals.append(observation.selected_total_cost)
        selected_users.append(
            ParticipantSnapshot.objects.get(
                observation=observation,
                participant=participant,
            ).selected_cost
        )
    assert selected_totals == [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
    ]
    assert selected_users == [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
    ]
    assert (
        observations[1].model_diagnostics[
            "total_cost_monotonic_repair_usd"
        ]
        == 10.0
    )
    assert (
        observations[1].model_diagnostics["cost_monotonic_repair_usd"]
        == 10.0
    )
    assert (
        observations[2].model_diagnostics[
            "total_cost_monotonic_repair_usd"
        ]
        == 0.0
    )
