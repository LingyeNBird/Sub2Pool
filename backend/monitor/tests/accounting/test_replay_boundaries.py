from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.models import (
    AppSettings,
    CPAAccountCollectionInterval,
    Observation,
    ParticipantSnapshot,
)
from monitor.replay import (
    RATE_METHOD,
    exclude_observation,
    rebuild_account,
    rebuild_current_interval,
    rebuild_observation_suffix,
    restore_observation,
)
from monitor.integrations.sub2api import (
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_cpa_account,
    create_participant,
    create_participant_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_cpa_partial_cycle_starts_at_first_connection_observation():
    config = AppSettings.load()
    account = create_cpa_account()
    first_at = timezone.now().replace(microsecond=0)
    reset_at = first_at + timedelta(days=3)

    first = Observation.objects.create(
        account_id=account.fact_key,
        source="manual",
        observed_at=first_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("45"),
        raw_selected_total_cost=Decimal("10"),
        selected_total_cost=Decimal("10"),
        total_standard_cost=Decimal("10"),
        total_actual_cost=Decimal("10"),
        effective_usd_per_percent=Decimal("16"),
        raw_window={"provider": "cpa"},
    )
    second = Observation.objects.create(
        account_id=account.fact_key,
        source="manual",
        observed_at=first_at + timedelta(hours=1),
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("46"),
        raw_selected_total_cost=Decimal("14"),
        selected_total_cost=Decimal("14"),
        total_standard_cost=Decimal("14"),
        total_actual_cost=Decimal("14"),
        raw_window={"provider": "cpa"},
        effective_usd_per_percent=Decimal("16"),
    )
    CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="partial-cycle",
        connected_at=first_at,
    )

    rebuild_account(account.fact_key, config)
    first.refresh_from_db()
    second.refresh_from_db()

    assert first.attribution_started_at == first.observed_at
    assert first.interval_used_percent == Decimal("0")
    assert first.selected_total_cost == Decimal("0")
    assert first.raw_window["replay_segment_reason"] == "provider_collection_baseline"
    assert second.attribution_started_at == first.observed_at
    assert second.interval_used_percent == Decimal("1")
    assert second.selected_total_cost == Decimal("4.000000")


@pytest.mark.django_db
def test_cpa_reconnect_baseline_splits_out_uncollected_gap():
    config = AppSettings.load()
    account = create_cpa_account()
    first_at = timezone.now().replace(microsecond=0)
    reset_at = first_at + timedelta(days=3)
    observations = []
    for offset_hours, percent, total_cost, boundary in [
        (0, "50", "10", "opened"),
        (1, "51", "14", "closed"),
        (1.5, "60", "14", ""),
        (2, "70", "14", "opened"),
        (3, "71", "18", ""),
    ]:
        observations.append(
            Observation.objects.create(
                account_id=account.fact_key,
                source=(
                    f"cpa_subscription_{boundary}"
                    if boundary
                    else "scheduled"
                ),
                observed_at=first_at + timedelta(hours=offset_hours),
                window_seconds=604800,
                upstream_resets_at=reset_at,
                upstream_used_percent=Decimal(percent),
                raw_selected_total_cost=Decimal(total_cost),
                selected_total_cost=Decimal(total_cost),
                total_standard_cost=Decimal(total_cost),
                total_actual_cost=Decimal(total_cost),
                effective_usd_per_percent=Decimal("16"),
                raw_window={"provider": "cpa"},
            )
        )
    CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="before-gap",
        connected_at=first_at,
        disconnected_at=first_at + timedelta(hours=1),
        end_reliable=True,
        opening_observation=observations[0],
        closing_observation=observations[1],
    )
    CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="after-gap",
        connected_at=first_at + timedelta(hours=2),
        opening_observation=observations[3],
    )

    rebuild_account(account.fact_key, config)
    for observation in observations:
        observation.refresh_from_db()

    first, disconnected, inside_gap, reconnected, after_reconnect = observations
    assert first.attribution_started_at == first.observed_at
    assert first.interval_used_percent == Decimal("0")
    assert first.selected_total_cost == Decimal("0")
    assert disconnected.attribution_started_at == first.observed_at
    assert disconnected.interval_used_percent == Decimal("1")
    assert disconnected.selected_total_cost == Decimal("4.000000")
    assert inside_gap.excluded_at is not None
    assert inside_gap.attribution_started_at is None
    assert inside_gap.exclusion_source == "automatic"
    assert inside_gap.exclusion_reason == "CPA usage 采集区间未覆盖该百分比观测"
    assert reconnected.attribution_started_at == reconnected.observed_at
    assert reconnected.interval_used_percent == Decimal("0")
    assert reconnected.selected_total_cost == Decimal("0")
    assert after_reconnect.attribution_started_at == reconnected.observed_at
    assert after_reconnect.interval_used_percent == Decimal("1")
    assert after_reconnect.selected_total_cost == Decimal("4.000000")
    assert (
        reconnected.raw_window["replay_segment_reason"]
        == "provider_collection_baseline"
    )


@pytest.mark.django_db
def test_cpa_manual_percentage_exclusion_keeps_connection_close_marker():
    config = AppSettings.load()
    account = create_cpa_account()
    opened_at = timezone.now().replace(microsecond=0)
    reset_at = opened_at + timedelta(days=3)
    observations = []
    for offset_minutes, percent, boundary in [
        (0, "50", "opened"),
        (1, "51", "closed"),
        (2, "52", ""),
    ]:
        observations.append(
            Observation.objects.create(
                account_id=account.fact_key,
                source=(
                    f"cpa_subscription_{boundary}"
                    if boundary
                    else "scheduled"
                ),
                observed_at=opened_at + timedelta(minutes=offset_minutes),
                window_seconds=604800,
                upstream_resets_at=reset_at,
                upstream_used_percent=Decimal(percent),
                raw_selected_total_cost=Decimal(offset_minutes),
                selected_total_cost=Decimal(offset_minutes),
                total_standard_cost=Decimal(offset_minutes),
                total_actual_cost=Decimal(offset_minutes),
                effective_usd_per_percent=Decimal("16"),
                raw_window={"provider": "cpa"},
            )
        )
    CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="manual-closing-exclusion",
        connected_at=opened_at,
        disconnected_at=opened_at + timedelta(minutes=1),
        end_reliable=True,
        opening_observation=observations[0],
        closing_observation=observations[1],
    )

    rebuild_account(account.fact_key, config)
    opened, closed, gap = observations
    gap.refresh_from_db()
    assert gap.exclusion_source == "automatic"

    exclude_observation(closed, "关闭百分比异常")

    closed.refresh_from_db()
    gap.refresh_from_db()
    assert closed.exclusion_source == "manual"
    assert gap.exclusion_source == "automatic"
    assert gap.exclusion_reason == "CPA usage 采集区间未覆盖该百分比观测"
    assert gap.attribution_started_at is None
    opened.refresh_from_db()
    assert opened.exclusion_source == ""


@pytest.mark.django_db
def test_cpa_partial_rebuild_and_restore_use_persisted_collection_interval():
    config = AppSettings.load()
    account = create_cpa_account()
    first_at = timezone.now().replace(microsecond=0)
    old_reset = first_at + timedelta(days=2)
    new_reset = first_at + timedelta(days=9)
    observations = []
    for offset_hours, percent, total_cost, reset_at in [
        (0, "50", "10", old_reset),
        (1, "51", "14", old_reset),
        (2, "0", "20", new_reset),
        (3, "1", "24", new_reset),
    ]:
        observations.append(
            Observation.objects.create(
                account_id=account.fact_key,
                source="scheduled",
                observed_at=first_at + timedelta(hours=offset_hours),
                window_seconds=604800,
                upstream_resets_at=reset_at,
                upstream_used_percent=Decimal(percent),
                raw_selected_total_cost=Decimal(total_cost),
                selected_total_cost=Decimal(total_cost),
                total_standard_cost=Decimal(total_cost),
                total_actual_cost=Decimal(total_cost),
                raw_window={"provider": "cpa"},
                effective_usd_per_percent=Decimal("16"),
            )
        )
    CPAAccountCollectionInterval.objects.create(
        account=account,
        session_key="official-reset",
        connected_at=first_at,
        opening_observation=observations[0],
    )

    rebuild_account(account.fact_key, config)
    rebuild_current_interval(account.fact_key, config)
    for observation in observations:
        observation.refresh_from_db()
        assert observation.exclusion_source == ""

    official_reset, current = observations[2:]
    assert official_reset.attribution_started_at == official_reset.observed_at
    assert current.attribution_started_at == official_reset.observed_at
    assert current.selected_total_cost == Decimal("4.000000")

    exclude_observation(current, "临时排除")
    restore_observation(current)
    official_reset.refresh_from_db()
    current.refresh_from_db()
    assert official_reset.exclusion_source == ""
    assert current.exclusion_source == ""
    assert current.attribution_started_at == official_reset.observed_at
    assert current.selected_total_cost == Decimal("4.000000")

@pytest.mark.django_db
def test_passive_reset_timestamp_drift_keeps_the_same_cycle(monkeypatch):
    """被动快照重置时间漂移数分钟时不能误建一个新的官方周期。"""
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    create_participant(name="车主",
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
                Decimal("10") + self.step,
                604800,
                345600,
                int(
                    (
                        reset_at
                        + timedelta(minutes=7, seconds=30) * self.step
                    ).timestamp()
                ),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            cost = Decimal("100") + Decimal("10") * self.step
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1000"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    observations = list(Observation.objects.order_by("observed_at", "id"))
    assert len(observations) == 2
    assert (
        observations[0].attribution_started_at
        == observations[1].attribution_started_at
    )


@pytest.mark.django_db
def test_backward_reset_timestamp_does_not_start_new_cycle():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    observed_at = timezone.now().replace(microsecond=0)
    current_reset_at = observed_at + timedelta(days=7)
    stale_reset_at = current_reset_at - timedelta(days=7)

    def raw_observation(index, used_percent, reset_at, cost):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at + timedelta(minutes=10 * index),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=Decimal(used_percent),
            raw_selected_total_cost=Decimal(cost),
            selected_total_cost=Decimal(cost),
            total_standard_cost=Decimal(cost),
            total_actual_cost=Decimal(cost),
            effective_usd_per_percent=Decimal("16"),
        )
        rebuild_observation_suffix(observation, config)
        observation.refresh_from_db()
        return observation

    first = raw_observation(0, "40", current_reset_at, "400")
    stale = raw_observation(1, "41", stale_reset_at, "420")

    assert stale.exclusion_source == ""
    assert stale.attribution_started_at == first.attribution_started_at
    assert stale.delta_percent == Decimal("1")
    assert stale.delta_cost == Decimal("20")


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
    create_monitored_account(7)
    config.weekly_quota_model = "constant_average"
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
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
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=participant_cost,
        selected_cost=participant_cost,
        current_balance_usd=Decimal("1000"),
        remaining_share_percent=Decimal("100"),)
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
    continued_zero = raw_observation(
        zero_observed_at + timedelta(minutes=30),
        new_reset_at,
        Decimal("0"),
        Decimal("185.310566"),
        Decimal("110"),
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
    continued_zero.refresh_from_db()
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=latest,
        participant=participant,
    )
    assert zero.attribution_started_at == zero.observed_at
    assert zero.selected_total_cost == Decimal("0")
    assert zero.raw_window["replay_segment_reason"] == (
        "official_zero_observation"
    )
    assert continued_zero.attribution_started_at == zero.observed_at
    assert continued_zero.selected_total_cost == Decimal("10.000000")
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
    assert result.rebuilt_observations == 5
    assert appended.attribution_started_at == zero.observed_at
    assert appended.selected_total_cost == Decimal("124.689434")
    assert appended.raw_window["replay_segment_reason"] == (
        "official_zero_observation"
    )


@pytest.mark.django_db
def test_same_official_reset_rollbacks_wait_for_explicit_manual_start(
    monkeypatch,
):
    """同一 reset_at 下连续低点也不能覆盖官方七天边界。"""

    config = AppSettings.load()
    create_monitored_account(7)
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=50,
    is_owner=True,)
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
    create_participant_snapshot(observation=previous,
    participant=owner,
    raw_selected_cost=Decimal("200"),
    selected_cost=Decimal("200"),
    charged_delta_percent=Decimal("10"),
    charged_cycle_percent=Decimal("10"),
    remaining_share_percent=Decimal("40"),
    current_balance_usd=Decimal("500"),
    recommended_balance_usd=Decimal("500"),)

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
    first_low = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    second_low = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

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
def test_reset_and_percent_reversion_follows_later_window_evidence():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    window_seconds = 604800
    observed_at = timezone.now().replace(microsecond=0)
    original_reset_at = observed_at + timedelta(minutes=5)
    changed_reset_at = original_reset_at + timedelta(days=7)

    def raw_observation(index, used_percent, reset_at, cost):
        return Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=observed_at + timedelta(minutes=10 * index),
            window_seconds=window_seconds,
            upstream_resets_at=reset_at,
            upstream_used_percent=Decimal(used_percent),
            raw_selected_total_cost=Decimal(cost),
            selected_total_cost=Decimal(cost),
            total_standard_cost=Decimal(cost),
            total_actual_cost=Decimal(cost),
            effective_usd_per_percent=Decimal("16"),
        )

    first = raw_observation(0, "40", original_reset_at, "400")
    rebuild_observation_suffix(first, config)
    false_reset = raw_observation(1, "0", changed_reset_at, "420")
    rebuild_observation_suffix(false_reset, config)
    recovered = raw_observation(2, "40", original_reset_at, "440")
    rebuild_observation_suffix(recovered, config)
    for observation in (first, false_reset, recovered):
        observation.refresh_from_db()

    expected_started_at = original_reset_at - timedelta(
        seconds=window_seconds,
    )
    assert false_reset.exclusion_source == "automatic"
    assert false_reset.attribution_started_at is None
    assert "重置时间和百分比恢复" in false_reset.exclusion_reason
    assert first.attribution_started_at == expected_started_at
    assert recovered.attribution_started_at == expected_started_at
    assert recovered.delta_percent == Decimal("0")
    assert recovered.delta_cost == Decimal("40")

    confirmed = raw_observation(3, "1", changed_reset_at, "460")
    rebuild_observation_suffix(confirmed, config)
    for observation in (false_reset, recovered, confirmed):
        observation.refresh_from_db()

    assert false_reset.exclusion_source == ""
    assert false_reset.attribution_started_at == false_reset.observed_at
    assert false_reset.selected_total_cost == Decimal("0")
    assert recovered.exclusion_source == "automatic"
    assert recovered.attribution_started_at is None
    assert "再次确认候选窗口" in recovered.exclusion_reason
    assert confirmed.attribution_started_at == false_reset.observed_at
    assert confirmed.selected_total_cost == Decimal("40")


@pytest.mark.django_db
def test_single_false_rollback_is_excluded_without_rewriting_prior_points(
    monkeypatch,
):
    """47→18→49 中的 18 留作审计；49 直接衔接上一个有效点。"""
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    owner = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
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
    first = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    rollback = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")
    recovered = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

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
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
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
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=cost_value,
        selected_cost=cost_value,
        current_balance_usd=Decimal("1000"),
        remaining_share_percent=Decimal("100"),)
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
