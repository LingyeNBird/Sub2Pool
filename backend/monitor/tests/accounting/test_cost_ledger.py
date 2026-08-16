from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.sampling.local_usage import save_local_bundle
from monitor.sampling.types import (
    LocalBundle,
    LocalParticipantData,
    WindowReference,
)
from monitor.integrations.sub2api import (
    Sub2APIUsageLog,
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)


@pytest.mark.django_db
def test_runtime_bridges_a_changed_cost_query_window_with_request_logs(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.fast_correction_enabled = False
    config.cost_basis = "actual"
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now().replace(microsecond=0)
    previous_at = now - timedelta(minutes=10)
    reset_at = now + timedelta(days=3)
    previous = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=previous_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("47"),
        raw_selected_total_cost=Decimal("1217"),
        selected_total_cost=Decimal("1217"),
        total_standard_cost=Decimal("1217"),
        total_actual_cost=Decimal("1217"),
        effective_usd_per_percent=Decimal("20"),
    )
    ParticipantSnapshot.objects.create(
        observation=previous,
        participant=participant,
        raw_selected_cost=Decimal("1217"),
        selected_cost=Decimal("1217"),
        current_balance_usd=Decimal("500"),
    )
    Sub2APIUserUsageSample.objects.create(
        account_id=7,
        sub2api_user_id=1,
        observed_at=previous_at,
        window_started_at=None,
        window_resets_at=reset_at,
        total_standard_cost=Decimal("1217"),
        total_actual_cost=Decimal("1217"),
    )
    bridge_log = Sub2APIUsageLog(
        id=1,
        user_id=1,
        account_id=7,
        created_at=now - timedelta(minutes=5),
        service_tier="",
        total_cost=Decimal("18"),
        actual_cost=Decimal("18"),
    )

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("49"),
                604800,
                259200,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("379"), Decimal("379"))

        def all_user_usage_stats(self, **_kwargs):
            return [
                Sub2APIUserUsage(
                    user_id=1,
                    email="owner@example.com",
                    username="owner",
                    stats=UsageStats(Decimal("379"), Decimal("379")),
                )
            ]

        def usage_logs(self, **kwargs):
            assert kwargs["started_at"] == previous_at
            return [bridge_log]

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)

    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    current = Observation.objects.order_by("observed_at", "id").last()
    assert current is not None
    assert current.raw_selected_total_cost == Decimal("379")
    assert current.total_actual_cost == Decimal("379")
    assert current.cost_window_started_at is not None
    assert current.cost_window_ended_at == current.observed_at
    assert current.interval_cost_started_at == previous_at
    assert current.interval_actual_cost == Decimal("18")
    assert current.interval_cost_source == "request_logs"
    assert current.normalized_actual_cost == Decimal("1235")
    assert current.selected_total_cost == Decimal("1235")

    user_sample = Sub2APIUserUsageSample.objects.order_by(
        "observed_at", "id"
    ).last()
    assert user_sample is not None
    assert user_sample.total_actual_cost == Decimal("379")
    assert user_sample.interval_actual_cost == Decimal("18")
    assert user_sample.interval_source == "request_logs"
    assert user_sample.normalized_actual_cost == Decimal("1235")

    previous.refresh_from_db()
    assert previous.total_actual_cost == Decimal("1217")
    assert previous.normalized_actual_cost == Decimal("1217")



@pytest.mark.django_db
def test_local_trend_keeps_normalized_cost_across_query_window_change():
    config = AppSettings.load()
    config.openai_account_id = 7
    config.cost_basis = "actual"
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now().replace(microsecond=0)
    previous_at = now - timedelta(minutes=10)
    reset_at = now + timedelta(days=3)
    previous_window_start = now - timedelta(days=5)
    current_window_start = now.replace(hour=0, minute=0, second=0)
    latest = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=previous_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=previous_at,
        upstream_used_percent=Decimal("18"),
        raw_selected_total_cost=Decimal("361"),
        selected_total_cost=Decimal("0"),
        total_standard_cost=Decimal("361"),
        total_actual_cost=Decimal("361"),
        normalized_standard_cost=Decimal("1229"),
        normalized_actual_cost=Decimal("1229"),
        effective_usd_per_percent=Decimal("20"),
    )
    latest.is_manual_start = True
    latest.manual_start_end = latest
    latest.save(update_fields=["is_manual_start", "manual_start_end"])
    ParticipantSnapshot.objects.create(
        observation=latest,
        participant=participant,
        raw_selected_cost=Decimal("361"),
        selected_cost=Decimal("0"),
        current_balance_usd=Decimal("500"),
    )
    Sub2APIUserUsageSample.objects.create(
        account_id=7,
        sub2api_user_id=1,
        observed_at=previous_at,
        window_started_at=previous_window_start,
        window_ended_at=previous_at,
        window_resets_at=reset_at,
        total_standard_cost=Decimal("361"),
        total_actual_cost=Decimal("361"),
        normalized_standard_cost=Decimal("1229"),
        normalized_actual_cost=Decimal("1229"),
    )
    local = LocalBundle(
        total=UsageStats(Decimal("20"), Decimal("20")),
        participants=[
            LocalParticipantData(
                participant=participant,
                stats=UsageStats(Decimal("20"), Decimal("20")),
                balance=UserBalance(Decimal("500"), Decimal("0")),
            )
        ],
        users=[
            Sub2APIUserUsage(
                user_id=1,
                email="owner@example.com",
                username="owner",
                stats=UsageStats(Decimal("20"), Decimal("20")),
            )
        ],
        checked_at=now,
        cost_window_started_at=current_window_start,
        cost_window_ended_at=now,
    )
    interval_log = Sub2APIUsageLog(
        id=2,
        user_id=1,
        account_id=7,
        created_at=now - timedelta(minutes=5),
        service_tier="",
        total_cost=Decimal("18"),
        actual_cost=Decimal("18"),
    )

    save_local_bundle(
        config,
        WindowReference(7, reset_at, 604800),
        local,
        latest,
        interval_logs=[interval_log],
    )

    user_sample = Sub2APIUserUsageSample.objects.get(observed_at=now)
    assert user_sample.total_actual_cost == Decimal("20")
    assert user_sample.interval_actual_cost == Decimal("18")
    assert user_sample.normalized_actual_cost == Decimal("1247")
    trend = ParticipantUsageSample.objects.get(observed_at=now)
    assert trend.raw_selected_cost == Decimal("20")
    assert trend.selected_cost == Decimal("18")
    participant.refresh_from_db()
    assert participant.latest_selected_cost == Decimal("18")