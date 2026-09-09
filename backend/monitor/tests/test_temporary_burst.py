"""Behavioral regression for borrowed-rights conservation and temporary wallets."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from monitor.balance_operations import (
    apply_participant_recommendation,
    auto_apply_recommendations,
)
from monitor.models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    PoolParticipant,
)
from monitor.models.temporary_burst import TemporaryBurstCycle
from monitor.reporting import aggregate_recommendation
from monitor.secrets import encrypt_secret
from monitor.temporary_burst import (
    active_session,
    reconcile_account,
    settle_percentages,
    start_session,
)
from monitor.tests.helpers import create_monitored_account


@pytest.mark.parametrize(
    "usage, expected",
    [
        ([33, 33, 34], [17, -8, -9]),
        ([20, 30, 10], ["3.33333", -5, "1.66667"]),
        ([50, 15, 10], [0, 0, 0]),
        ([30, 20, 20], [0, 0, 0]),
    ],
)
def test_borrowed_rights_not_all_unused_rights_are_carried(usage, expected):
    result = settle_percentages(
        dict(enumerate(map(D, [50, 25, 25]))), dict(enumerate(map(D, usage)))
    )
    assert list(result.values()) == list(map(D, expected))
    assert sum(result.values()) == 0


def record(account, people, at, reset, used, *, segment=None):
    total = sum(map(D, used)) * 10
    observation = Observation.objects.create(
        account_id=account.fact_key,
        observed_at=at,
        upstream_resets_at=reset,
        attribution_started_at=segment or reset - timedelta(days=7),
        upstream_used_percent=sum(map(D, used)),
        interval_used_percent=sum(map(D, used)),
        estimated_used_percent=sum(map(D, used)),
        raw_selected_total_cost=total,
        selected_total_cost=total,
        total_standard_cost=total,
        total_actual_cost=total,
        effective_usd_per_percent=10,
        correction_source="upstream",
    )
    for person, share, consumed in zip(people, [50, 25, 25], map(D, used)):
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=person,
            source_sub2api_user_id=person.sub2api_user_id,
            share_percent=share,
            selected_cost=consumed * 10,
            raw_selected_cost=consumed * 10,
            charged_cycle_percent=consumed,
            charged_percent_lower=consumed,
            charged_percent_upper=consumed,
            current_balance_usd=person.latest_balance_usd,
        )
    return observation


@pytest.fixture
def riders(db):
    config = AppSettings.load()
    config.sub2api_base_url = "http://synthetic.invalid"
    config.sub2api_admin_token_encrypted = encrypt_secret("synthetic-burst-token")
    config.auto_apply_recommendations = False
    config.safety_factor = 1
    config.weekly_quota_model = "time_varying"
    config.save()
    account = create_monitored_account()
    people = [
        Participant.objects.create(
            name=name, sub2api_user_id=51 + i, latest_balance_usd=80
        )
        for i, name in enumerate("ABC")
    ]
    for person, share in zip(people, [50, 25, 25]):
        PoolParticipant.objects.create(
            pool=account.pool, participant=person, share_percent=share
        )
    now = timezone.now()
    reset = now + timedelta(days=1)
    old = record(account, people, now, reset, [33, 33, 34])
    return config, account, people, old


@pytest.mark.django_db
def test_mode_overrides_wallet_then_settles_once_and_returns_to_contract(riders):
    config, account, people, old = riders
    session = start_session()
    for person in people:
        aggregate, _ = aggregate_recommendation(person, config)
        assert aggregate["recommended_balance_usd"] == 9999
        assert aggregate["needs_manual_update"]
        assert aggregate["temporary_burst"]
    new = record(
        account,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, new, config)
    assert active_session(config) is None
    old_cycle = TemporaryBurstCycle.objects.get(session=session, is_burst_cycle=True)
    assert [D(row["next_adjustment"]) for row in old_cycle.settlement] == [17, -8, -9]
    for person, share in zip(people, [67, 17, 16]):
        aggregate, _ = aggregate_recommendation(person, config)
        assert aggregate["sources"][0]["effective_share_percent"] == share
        assert aggregate["recommended_balance_usd"] == share * 10
        assert not aggregate["temporary_burst"]
    reconcile_account(account, new, config)
    assert TemporaryBurstCycle.objects.count() == 2
    assert list(
        PoolParticipant.objects.order_by("participant_id").values_list(
            "share_percent", flat=True
        )
    ) == [50, 25, 25]
    record(
        account,
        people,
        new.upstream_resets_at - timedelta(minutes=1),
        new.upstream_resets_at,
        [67, 17, 16],
    )
    third = record(
        account,
        people,
        new.upstream_resets_at + timedelta(minutes=1),
        new.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, third, config)
    for person, share in zip(people, [50, 25, 25]):
        aggregate, _ = aggregate_recommendation(person, config)
        assert aggregate["sources"][0]["effective_share_percent"] == share
    assert TemporaryBurstCycle.objects.filter(settled_at__isnull=True).count() == 0


@pytest.mark.django_db
def test_first_account_reset_exits_globally_but_other_account_waits(riders):
    config, first, people, old = riders
    second = create_monitored_account(8, pool=first.pool)
    second_old = record(
        second,
        people,
        old.observed_at,
        old.upstream_resets_at + timedelta(days=2),
        [20, 30, 10],
    )
    session = start_session()
    new = record(
        first,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(first, new, config)
    assert active_session(config) is None
    assert (
        TemporaryBurstCycle.objects.get(session=session, account=second).settled_at
        is None
    )
    with pytest.raises(ValueError, match="周期|一轮"):
        start_session()
    later = record(
        second,
        people,
        second_old.upstream_resets_at + timedelta(minutes=1),
        second_old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(second, later, config)
    assert [
        D(row["next_adjustment"])
        for row in TemporaryBurstCycle.objects.get(
            session=session, account=second, is_burst_cycle=True
        ).settlement
    ] == [D("3.33333"), -5, D("1.66667")]


@pytest.mark.django_db
def test_manual_segment_does_not_exit_and_settlement_does_not_duplicate_segments(
    riders,
):
    config, account, people, old = riders
    start_session()
    # Two observations in the same segment must not both count toward the cycle total.
    updated = record(
        account,
        people,
        old.observed_at + timedelta(minutes=1),
        old.upstream_resets_at,
        [34, 33, 33],
    )
    reconcile_account(account, updated, config)
    assert active_session(config) is not None
    new = record(
        account,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, new, config)
    assert [
        D(row["used_percent"])
        for row in TemporaryBurstCycle.objects.get(is_burst_cycle=True).settlement
    ] == [34, 33, 33]


@pytest.mark.django_db
def test_manual_and_automatic_application_share_9999_then_restore(riders, monkeypatch):
    config, account, people, old = riders
    writes = []

    class Remote:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            writes.append((user_id, balance))
            return balance

    monkeypatch.setattr("monitor.balance_operations.Sub2APIClient", Remote)
    start_session()
    assert auto_apply_recommendations() == {"applied": 0, "failed": 0}
    assert writes == []
    apply_participant_recommendation(people[0].id)
    assert writes == [(people[0].sub2api_user_id, D("9999"))]
    config.auto_apply_recommendations = True
    config.save()
    assert auto_apply_recommendations()["applied"] == 2
    assert all(balance == 9999 for _, balance in writes)
    assert auto_apply_recommendations()["applied"] == 0
    new = record(
        account,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, new, config)
    assert auto_apply_recommendations()["applied"] == 3
    assert [balance for _, balance in writes[-3:]] == [670, 170, 160]


@pytest.mark.django_db
def test_segment_restart_keeps_previous_usage_and_ends_only_at_official_reset(riders):
    config, account, people, old = riders
    for snapshot, used in zip(
        old.participant_snapshots.order_by("participant_id"), [10, 5, 5]
    ):
        snapshot.charged_cycle_percent = used
        snapshot.save(update_fields=["charged_cycle_percent"])
    session = start_session()
    second = record(
        account,
        people,
        old.observed_at + timedelta(minutes=2),
        old.upstream_resets_at,
        [23, 28, 29],
        segment=old.observed_at + timedelta(minutes=1),
    )
    reconcile_account(account, second, config)
    assert active_session(config) is not None
    session.ended_at = timezone.now()
    session.save(update_fields=["ended_at"])
    aggregate, _ = aggregate_recommendation(people[0], config)
    assert aggregate["sources"][0]["consumed_entitlement_usd"] == 330
    new = record(
        account,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, new, config)
    cycle = TemporaryBurstCycle.objects.get(is_burst_cycle=True)
    assert [D(row["used_percent"]) for row in cycle.settlement] == [33, 33, 34]


@pytest.mark.django_db
def test_expired_mode_never_keeps_recommending_9999_without_new_sample(riders):
    config, account, people, old = riders
    session = start_session()
    session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save(update_fields=["expires_at"])
    aggregate, _ = aggregate_recommendation(people[0], config)
    assert not aggregate["temporary_burst"]
    assert aggregate["recommended_balance_usd"] != 9999


@pytest.mark.django_db
def test_activation_requires_admin_and_explicit_confirmation(riders):
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient
    from monitor.models.temporary_burst import TemporaryBurstSession

    user = get_user_model().objects.create_user(username="viewer")
    client = APIClient()
    client.force_authenticate(user)
    assert client.get("/api/dashboard/temporary-burst").status_code == 403
    assert (
        client.post(
            "/api/dashboard/temporary-burst", {"confirm": True}, format="json"
        ).status_code
        == 403
    )
    user.is_staff = True
    user.save()
    assert client.get("/api/dashboard/temporary-burst").status_code == 200
    assert (
        client.post("/api/dashboard/temporary-burst", {}, format="json").status_code
        == 400
    )
    assert TemporaryBurstSession.objects.count() == 0
    assert (
        client.post(
            "/api/dashboard/temporary-burst", {"confirm": True}, format="json"
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/dashboard/temporary-burst", {"confirm": True}, format="json"
        ).status_code
        == 400
    )
    assert TemporaryBurstSession.objects.count() == 1


@pytest.mark.django_db
def test_credit_identity_changes_do_not_silently_destroy_one_side_of_debt(riders):
    config, account, people, old = riders
    start_session()
    people[0].sub2api_user_id = 999
    people[0].save()
    new = record(
        account,
        people,
        old.upstream_resets_at + timedelta(minutes=1),
        old.upstream_resets_at + timedelta(days=7),
        [0, 0, 0],
    )
    reconcile_account(account, new, config)
    cycle = TemporaryBurstCycle.objects.get(is_burst_cycle=True)
    assert cycle.settled_at is None
    assert "绑定" in cycle.error
    assert TemporaryBurstCycle.objects.count() == 1
    assert active_session(config) is None
