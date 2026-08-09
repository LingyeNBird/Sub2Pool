import json
import sqlite3
from io import BytesIO, StringIO

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.management.commands.runmonitor import schedule_next_run
from monitor.models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    NotificationEvent,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.notifications import send_notification
from monitor.replay import (
    RATE_METHOD,
    exclude_observation,
    rebuild_account,
    rebuild_observation_suffix,
)
from monitor.secrets import encrypt_secret
from monitor.integrations.sub2api import (
    Sub2APIClient,
    Sub2APIError,
    Sub2APIUserUsage,
    Sub2APIUsageLog,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor import database_transfer
from monitor.tests.helpers import create_recommendation_snapshot, jwt_login

@pytest.mark.django_db
def test_integer_percent_plateau_uses_cumulative_cost_for_capacity(monkeypatch):
    """16% 平台期内的消费不能在跳到 17% 时被漏掉并产生 $687 的错误总额。"""
    config = AppSettings.load()
    config.openai_account_id = 7
    config.cost_basis = "actual"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
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
        run_monitor(force_upstream=True, source="manual")
    observations = list(Observation.objects.order_by("observed_at"))

    assert observations[0].sample_usd_per_percent == Decimal("26.213123")
    assert observations[1].sample_usd_per_percent == Decimal("26.972384")
    assert observations[2].delta_cost == Decimal("6.873233")
    assert observations[2].sample_usd_per_percent == Decimal("25.790081")
    assert Decimal("14") <= observations[2].effective_usd_per_percent <= Decimal(
        "21"
    )
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
        owner.share_percent - snapshot.charged_cycle_percent
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
    config.openai_account_id = 7
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
def test_passive_reset_timestamp_drift_keeps_the_same_cycle(monkeypatch):
    """被动快照重置时间漂移几十秒时不能误建一个新的官方周期。"""
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
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
                Decimal("10") + self.step,
                604800,
                345600,
                int((reset_at + timedelta(seconds=70 * self.step)).timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            cost = Decimal("100") + Decimal("10") * self.step
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1000"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(force_upstream=True, source="manual")
    run_monitor(force_upstream=True, source="manual")

    observations = list(Observation.objects.order_by("observed_at", "id"))
    assert len(observations) == 2
    assert (
        observations[0].attribution_started_at
        == observations[1].attribution_started_at
    )

@pytest.mark.django_db
def test_official_zero_observation_rebases_natural_day_usage_costs():
    """自然日累计成本必须在官方窗口首个 0% 观测处扣除跨周期结转。"""

    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    config.openai_account_id = 7
    config.weekly_quota_model = "constant_average"
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    window_seconds = 604800
    zero_observed_at = timezone.now().replace(microsecond=0)
    new_reset_at = zero_observed_at + timedelta(
        seconds=window_seconds,
        minutes=-40,
    )
    old_reset_at = new_reset_at - timedelta(seconds=window_seconds)

    def raw_observation(
        observed_at,
        reset_at,
        used_percent,
        total_cost,
        participant_cost,
    ):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at,
            window_seconds=window_seconds,
            upstream_resets_at=reset_at,
            upstream_used_percent=used_percent,
            raw_selected_total_cost=total_cost,
            selected_total_cost=total_cost,
            total_standard_cost=total_cost,
            total_actual_cost=total_cost,
            effective_usd_per_percent=Decimal("20"),
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=participant_cost,
            selected_cost=participant_cost,
            current_balance_usd=Decimal("1000"),
            remaining_share_percent=Decimal("100"),
        )
        return observation

    raw_observation(
        zero_observed_at - timedelta(hours=1),
        old_reset_at,
        Decimal("71"),
        Decimal("1931.418384"),
        Decimal("1200"),
    )
    zero = raw_observation(
        zero_observed_at,
        new_reset_at,
        Decimal("0"),
        Decimal("175.310566"),
        Decimal("100"),
    )
    raw_observation(
        zero_observed_at + timedelta(hours=1),
        new_reset_at,
        Decimal("1"),
        Decimal("196.788804"),
        Decimal("115"),
    )
    latest = raw_observation(
        zero_observed_at + timedelta(hours=2),
        new_reset_at,
        Decimal("3"),
        Decimal("260.872599"),
        Decimal("150"),
    )

    rebuild_account(7, config)
    zero.refresh_from_db()
    latest.refresh_from_db()
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=latest,
        participant=participant,
    )
    assert zero.attribution_started_at == zero.observed_at
    assert zero.selected_total_cost == Decimal("0")
    assert zero.raw_window["replay_segment_reason"] == (
        "official_zero_observation"
    )
    assert latest.attribution_started_at == zero.observed_at
    assert latest.selected_total_cost == Decimal("85.562033")
    assert latest_snapshot.selected_cost == Decimal("50")

    statistics = client.get("/api/statistics", **headers).json()["data"]
    assert statistics["capacity_summary"]["cycle"]["estimate_usd"] == 2852.07

    appended = raw_observation(
        zero_observed_at + timedelta(hours=3),
        new_reset_at,
        Decimal("4"),
        Decimal("300"),
        Decimal("170"),
    )
    result = rebuild_observation_suffix(appended, config)
    appended.refresh_from_db()
    assert result.rebuilt_observations == 4
    assert appended.attribution_started_at == zero.observed_at
    assert appended.selected_total_cost == Decimal("124.689434")
    assert appended.raw_window["replay_segment_reason"] == (
        "official_zero_observation"
    )

@pytest.mark.django_db
def test_midcycle_initialization_assigns_existing_ten_percent_to_owner(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=2,
        share_percent=50,
    )
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
            costs = {None: Decimal("100"), 1: Decimal("100"), 2: Decimal("0")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("700") if user_id == 1 else Decimal("800")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(force_upstream=True, source="manual")

    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    owner_snapshot = snapshots[owner.id]
    rider_snapshot = snapshots[rider.id]
    assert Decimal("4.75") <= owner_snapshot.charged_cycle_percent <= Decimal(
        "7.15"
    )
    assert (
        owner_snapshot.charged_percent_lower
        <= owner_snapshot.charged_cycle_percent
        <= owner_snapshot.charged_percent_upper
    )
    assert owner_snapshot.remaining_share_percent == (
        owner.share_percent - owner_snapshot.charged_cycle_percent
    )
    assert rider_snapshot.charged_cycle_percent == Decimal("0")
    assert rider_snapshot.remaining_share_percent == Decimal("50")

@pytest.mark.django_db
def test_unmapped_user_usage_is_saved_and_attributed_after_binding(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.initial_usd_per_percent = Decimal("20")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=60,
        is_owner=True,
    )
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
    run_monitor(force_upstream=True, source="manual")

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
                "share_percent": 40,
                "enabled": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 201
    rider = Participant.objects.get(sub2api_user_id=2)
    latest = Observation.objects.get()
    snapshots = {
        row.participant_id: row
        for row in latest.participant_snapshots.all()
    }
    owner_snapshot = snapshots[owner.id]
    rider_snapshot = snapshots[rider.id]
    assert owner_snapshot.charged_percent_lower <= owner_snapshot.charged_cycle_percent
    assert owner_snapshot.charged_cycle_percent <= owner_snapshot.charged_percent_upper
    assert rider_snapshot.charged_percent_lower <= rider_snapshot.charged_cycle_percent
    assert rider_snapshot.charged_cycle_percent <= rider_snapshot.charged_percent_upper
    assert float(owner_snapshot.charged_cycle_percent) == pytest.approx(
        float(rider_snapshot.charged_cycle_percent) * 3,
        rel=0.08,
    )
    assert snapshots[rider.id].selected_cost == Decimal("100")
    assert ParticipantUsageSample.objects.filter(
        participant=rider,
        raw_selected_cost=Decimal("100"),
    ).exists()

@pytest.mark.django_db
def test_adding_participant_midcycle_replays_the_complete_segment(monkeypatch):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
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
    run_monitor(force_upstream=True, source="manual")
    first_observation = Observation.objects.get()
    first_observation.sample_note = "必须由完整区间重放覆盖"
    first_observation.save(update_fields=["sample_note"])

    owner.share_percent = Decimal("60")
    owner.save(update_fields=["share_percent"])
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=2,
        share_percent=40,
    )
    run_monitor(force_upstream=True, source="manual")

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
def test_same_official_reset_rollbacks_wait_for_explicit_manual_start(
    monkeypatch,
):
    """同一 reset_at 下连续低点也不能覆盖官方七天边界。"""

    config = AppSettings.load()
    config.openai_account_id = 7
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=4)
    previous = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=now - timedelta(hours=1),
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=reset_at - timedelta(days=7),
        upstream_used_percent=Decimal("10"),
        interval_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("200"),
        selected_total_cost=Decimal("200"),
        total_standard_cost=Decimal("200"),
        total_actual_cost=Decimal("200"),
        sample_usd_per_percent=Decimal("20"),
        effective_usd_per_percent=Decimal("20"),
        valid_sample=True,
        raw_window={"rate_method": RATE_METHOD},
    )
    ParticipantSnapshot.objects.create(
        observation=previous,
        participant=owner,
        raw_selected_cost=Decimal("200"),
        selected_cost=Decimal("200"),
        charged_delta_percent=Decimal("10"),
        charged_cycle_percent=Decimal("10"),
        remaining_share_percent=Decimal("40"),
        current_balance_usd=Decimal("500"),
        recommended_balance_usd=Decimal("500"),
    )

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
                Decimal("0"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
                (now + timedelta(minutes=self.step)).isoformat(),
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("0"), Decimal("0"))

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    first_low = run_monitor(force_upstream=True, source="manual")
    second_low = run_monitor(force_upstream=True, source="manual")

    assert first_low["status"] == "reset_pending"
    assert second_low["status"] == "reset_pending"
    included = list(
        Observation.objects.filter(excluded_at__isnull=True).order_by(
            "observed_at",
            "id",
        )
    )
    assert included == [previous]
    excluded = list(
        Observation.objects.filter(exclusion_source="automatic").order_by(
            "observed_at",
            "id",
        )
    )
    assert len(excluded) == 2
    assert all(item.attribution_started_at is None for item in excluded)
    assert all("官方重置时间未变化" in item.exclusion_reason for item in excluded)

@pytest.mark.django_db
def test_single_false_rollback_is_excluded_without_rewriting_prior_points(
    monkeypatch,
):
    """47→18→49 中的 18 留作审计；49 直接衔接上一个有效点。"""
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    reset_at = timezone.now() + timedelta(days=3)
    percents = [Decimal("47"), Decimal("18"), Decimal("49")]
    costs = [Decimal("940"), Decimal("960"), Decimal("980")]
    sampled_at = [
        (timezone.now() + timedelta(minutes=index)).isoformat()
        for index in range(3)
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
                percents[self.step],
                604800,
                259200,
                int(reset_at.timestamp()),
                "passive_snapshot",
                sampled_at[self.step],
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(costs[self.step], costs[self.step])

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1000"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    first = run_monitor(force_upstream=True, source="manual")
    rollback = run_monitor(force_upstream=True, source="manual")
    recovered = run_monitor(force_upstream=True, source="manual")

    assert first["status"] == "calibrated"
    assert rollback["status"] == "reset_pending"
    assert recovered["status"] == "calibrated"
    assert Observation.objects.count() == 3

    included = list(
        Observation.objects.filter(excluded_at__isnull=True).order_by(
            "observed_at",
            "id",
        )
    )
    assert [item.upstream_used_percent for item in included] == [
        Decimal("47"),
        Decimal("49"),
    ]
    candidate = Observation.objects.get(pk=rollback["observation_id"])
    assert candidate.excluded_at is not None
    assert candidate.exclusion_source == "automatic"
    assert candidate.raw_window["replay_decision"] == "automatic_exclusion"
    assert "瞬时异常" in candidate.exclusion_reason
    assert included[-1].delta_percent == Decimal("2")
    assert included[-1].delta_cost == Decimal("40")
    assert (
        included[-1].attribution_started_at
        == included[0].attribution_started_at
    )
    snapshot = ParticipantSnapshot.objects.get(
        observation=included[-1],
        participant=owner,
    )
    assert (
        snapshot.charged_percent_lower
        <= snapshot.charged_cycle_percent
        <= snapshot.charged_percent_upper
    )

@pytest.mark.django_db
def test_append_and_exclusion_replay_the_affected_official_interval():
    """粒子状态依赖完整区间，但更早官方区间不得被改写。"""

    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now()
    old_reset = now - timedelta(days=8)
    current_reset = now + timedelta(days=3)

    def raw_observation(at, reset_at, percent_value, cost_value):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=percent_value,
            raw_selected_total_cost=cost_value,
            selected_total_cost=cost_value,
            total_standard_cost=cost_value,
            total_actual_cost=cost_value,
            effective_usd_per_percent=Decimal("20"),
            raw_window={"rate_method": RATE_METHOD},
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=cost_value,
            selected_cost=cost_value,
            current_balance_usd=Decimal("1000"),
            remaining_share_percent=Decimal("100"),
        )
        return observation

    old_first = raw_observation(
        now - timedelta(days=10),
        old_reset,
        Decimal("10"),
        Decimal("100"),
    )
    raw_observation(
        now - timedelta(days=9),
        old_reset,
        Decimal("20"),
        Decimal("200"),
    )
    raw_observation(
        now - timedelta(days=2),
        current_reset,
        Decimal("10"),
        Decimal("100"),
    )
    current_middle = raw_observation(
        now - timedelta(days=1),
        current_reset,
        Decimal("20"),
        Decimal("200"),
    )
    initial = rebuild_account(7, config)
    assert initial.rebuilt_observations == 4

    old_first.sample_note = "旧周期哨兵"
    old_first.save(update_fields=["sample_note"])
    current_latest = raw_observation(
        now,
        current_reset,
        Decimal("30"),
        Decimal("300"),
    )
    appended = rebuild_observation_suffix(current_latest, config)
    assert appended.rebuilt_observations == 3
    old_first.refresh_from_db()
    current_latest.refresh_from_db()
    assert old_first.sample_note == "旧周期哨兵"
    assert current_latest.delta_percent == Decimal("10")
    assert current_latest.delta_cost == Decimal("100")

    replayed = exclude_observation(current_middle, "中间点不可信")
    assert replayed["rebuilt_observations"] == 2
    old_first.refresh_from_db()
    current_latest.refresh_from_db()
    assert old_first.sample_note == "旧周期哨兵"
    assert current_latest.delta_percent == Decimal("20")
    assert current_latest.delta_cost == Decimal("200")

@pytest.mark.django_db
def test_startup_replay_command_skips_current_algorithm_records():
    """容器重启不应重放已经由当前算法生成的稳定历史。"""

    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
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
        sample_note="稳定结果哨兵",
        raw_window={"rate_method": RATE_METHOD},
    )
    output = StringIO()
    call_command("replayobservations", stdout=output)

    observation.refresh_from_db()
    assert observation.sample_note == "稳定结果哨兵"
    assert "派生结果已是最新版" in output.getvalue()

@pytest.mark.django_db
def test_exclusion_restore_and_manual_start_cancellation_replay_affected_suffix():
    """回退点可恢复为管理员起点，也可取消后重新由异常检测排除。"""
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=3)

    def raw_observation(minutes_ago, percent_value, cost_value, source):
        observed_at = now - timedelta(minutes=minutes_ago)
        observation = Observation.objects.create(
            account_id=7,
            source=source,
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=reset_at - timedelta(days=7),
            upstream_used_percent=percent_value,
            raw_selected_total_cost=cost_value,
            selected_total_cost=cost_value,
            total_standard_cost=cost_value,
            total_actual_cost=cost_value,
            sample_usd_per_percent=(
                cost_value / percent_value if percent_value else None
            ),
            effective_usd_per_percent=Decimal("20"),
            valid_sample=percent_value > 0,
            raw_window={
                "rate_method": RATE_METHOD,
                "sampled_at": observed_at.isoformat(),
            },
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=cost_value,
            selected_cost=cost_value,
            charged_delta_percent=percent_value,
            charged_cycle_percent=percent_value,
            remaining_share_percent=Decimal("100") - percent_value,
            current_balance_usd=Decimal("1000"),
            recommended_balance_usd=Decimal("1000"),
        )
        return observation

    raw_observation(120, Decimal("47"), Decimal("940"), "manual")
    false_reset = raw_observation(60, Decimal("18"), Decimal("960"), "reset")
    raw_observation(40, Decimal("49"), Decimal("980"), "manual")
    raw_observation(20, Decimal("50"), Decimal("1000"), "manual")

    client = Client()
    headers, _ = jwt_login(client)
    response = client.post(
        f"/api/observations/{false_reset.id}/exclude",
        data=json.dumps({"reason": "异常的 18% 快照"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["rebuilt_observations"] == 3
    false_reset.refresh_from_db()
    assert false_reset.excluded_at is not None
    assert false_reset.exclusion_source == "manual"

    included = list(
        Observation.objects.filter(excluded_at__isnull=True).order_by(
            "observed_at",
            "id",
        )
    )
    assert [item.upstream_used_percent for item in included] == [
        Decimal("47"),
        Decimal("49"),
        Decimal("50"),
    ]
    assert [item.selected_total_cost for item in included] == [
        Decimal("940"),
        Decimal("980"),
        Decimal("1000"),
    ]
    assert included[-1].delta_percent == Decimal("1")
    assert included[-1].delta_cost == Decimal("20")
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=included[-1],
        participant=participant,
    )
    assert (
        latest_snapshot.charged_percent_lower
        <= latest_snapshot.charged_cycle_percent
        <= latest_snapshot.charged_percent_upper
    )

    listed = client.get("/api/observations", **headers).json()["data"]
    assert listed["summary"]["excluded_count"] == 1
    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["cycle"]["upstream_used_percent"] == 50.0
    assert dashboard["cycle"]["selected_total_cost"] == 1000.0

    restored = client.post(
        f"/api/observations/{false_reset.id}/restore",
        **headers,
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["included"] is True
    assert restored.json()["data"]["inferred_intervals"] == 1
    false_reset.refresh_from_db()
    assert false_reset.excluded_at is None
    assert false_reset.is_manual_start is True
    assert false_reset.interval_used_percent == Decimal("0")
    assert false_reset.selected_total_cost == Decimal("0")
    assert Observation.objects.filter(excluded_at__isnull=True).count() == 4

    latest = Observation.objects.order_by("-observed_at", "-id").first()
    assert latest.attribution_started_at == false_reset.observed_at
    assert latest.interval_used_percent == Decimal("32")
    assert latest.selected_total_cost == Decimal("40")
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=latest,
        participant=participant,
    )
    assert (
        latest_snapshot.charged_percent_lower
        <= latest_snapshot.charged_cycle_percent
        <= latest_snapshot.charged_percent_upper
    )

    cleared = client.delete(
        f"/api/observations/{false_reset.id}/manual-start",
        **headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["rebuilt_observations"] == 3
    false_reset.refresh_from_db()
    assert false_reset.is_manual_start is False
    assert false_reset.exclusion_source == "automatic"
    assert false_reset.excluded_at is not None

@pytest.mark.django_db
def test_rebuild_api_recomputes_current_interval_without_changing_raw_samples():
    """管理员可只重建派生字段；错误点排除后，后续增量重新衔接上一有效点。"""

    get_user_model().objects.create_superuser(
        username="rebuild-owner",
        password="very-strong-password",
        email="rebuild-owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=3)

    def raw_observation(minutes_ago, percent_value, cost_value):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=now - timedelta(minutes=minutes_ago),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=percent_value,
            raw_selected_total_cost=cost_value,
            selected_total_cost=cost_value,
            total_standard_cost=cost_value,
            total_actual_cost=cost_value,
            effective_usd_per_percent=Decimal("20"),
            raw_window={"rate_method": RATE_METHOD},
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=cost_value,
            selected_cost=cost_value,
            current_balance_usd=Decimal("1000"),
            remaining_share_percent=Decimal("100"),
        )
        return observation

    first = raw_observation(60, Decimal("47"), Decimal("940"))
    false_reset = raw_observation(40, Decimal("18"), Decimal("960"))
    recovered = raw_observation(20, Decimal("49"), Decimal("980"))
    rebuild_account(7, config)
    exclude_observation(false_reset, "异常的 18% 快照")

    recovered.refresh_from_db()
    assert recovered.delta_percent == Decimal("2")
    assert recovered.delta_cost == Decimal("40")
    raw_totals_before = list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    )

    recovered.selected_total_cost = Decimal("180")
    recovered.delta_percent = Decimal("-29")
    recovered.delta_cost = Decimal("-800")
    recovered.sample_usd_per_percent = Decimal("0.2")
    recovered.save(
        update_fields=[
            "selected_total_cost",
            "delta_percent",
            "delta_cost",
            "sample_usd_per_percent",
        ]
    )
    recovered_snapshot = ParticipantSnapshot.objects.get(
        observation=recovered,
        participant=participant,
    )
    recovered_snapshot.selected_cost = Decimal("180")
    recovered_snapshot.delta_cost = Decimal("-800")
    recovered_snapshot.save(update_fields=["selected_cost", "delta_cost"])

    client = Client()
    headers, _ = jwt_login(client, username="rebuild-owner")
    response = client.post("/api/observations/rebuild", **headers)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["rebuilt_observations"] == 2
    assert payload["replay_started_at"] is not None
    assert list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    ) == raw_totals_before
    first.refresh_from_db()
    false_reset.refresh_from_db()
    recovered.refresh_from_db()
    recovered_snapshot.refresh_from_db()
    assert first.selected_total_cost == Decimal("940")
    assert false_reset.exclusion_source == "manual"
    assert recovered.selected_total_cost == Decimal("980")
    assert recovered.delta_percent == Decimal("2")
    assert recovered.delta_cost == Decimal("40")
    assert recovered_snapshot.selected_cost == Decimal("980")
    assert recovered_snapshot.delta_cost == Decimal("40")

@pytest.mark.django_db
def test_api_requires_admin_jwt_and_accepts_admin_login():
    user = get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    unauthorized = client.get("/api/dashboard")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["ok"] is False

    headers, logged_in = jwt_login(client)
    assert "access" in logged_in.json()["data"]
    assert "refresh" not in logged_in.json()["data"]
    assert logged_in.cookies["pinche_refresh"]["httponly"]
    assert "sessionid" not in logged_in.cookies
    assert logged_in.json()["data"]["timezone"] == "Asia/Shanghai"
    assert client.get("/api/auth/me", **headers).json()["data"]["timezone"] == (
        "Asia/Shanghai"
    )
    config = AppSettings.load()
    config.last_upstream_check_at = timezone.now() - timedelta(hours=13)
    config.stale_warning_hours = 12
    config.save(
        update_fields=["last_upstream_check_at", "stale_warning_hours", "updated_at"]
    )

    dashboard = client.get("/api/dashboard", **headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["quota_query_mode"] == "passive"
    assert dashboard.json()["data"]["snapshot_stale"] is True
    assert user.is_staff



@pytest.mark.django_db
def test_failed_initial_replay_leaves_observation_pending_without_version(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.fast_correction_enabled = False
    config.save()
    Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
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
        run_monitor(force_upstream=True, source="manual")

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
def test_replay_uses_both_persisted_cost_bases():
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
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=actual,
            selected_cost=actual,
            current_balance_usd=Decimal("1000"),
        )
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
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
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
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=cost,
            selected_cost=cost,
            current_balance_usd=Decimal("1000"),
        )
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