from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from monitor.cpa.billing import billing_bounds, billing_summary, cycle_rows
from monitor.cpa.participants import owner_index
from monitor.models import (
    CPAAccountCollectionInterval,
    CPAQuotaContract,
)
from .test_cpa_participants import event, observation, member_client
from . import test_cpa_participants as fixtures

setup = fixtures.setup

pytestmark = pytest.mark.django_db
UTC = timezone.utc


def test_anchor_short_month_and_leap():
    anchor = date(2024, 1, 31)
    assert billing_bounds(anchor, "UTC", datetime(2024, 2, 29, tzinfo=UTC)) == (
        datetime(2024, 2, 29, tzinfo=UTC),
        datetime(2024, 3, 31, tzinfo=UTC),
    )
    assert billing_bounds(anchor, "Asia/Shanghai", datetime(2025, 2, 28, tzinfo=UTC))[
        1
    ] == datetime(2025, 3, 30, 16, tzinfo=UTC)
    start, end = billing_bounds(
        date(2024, 1, 10), "America/New_York", datetime(2024, 3, 11, tzinfo=UTC)
    )
    assert end - start == timedelta(days=31, hours=-1)


def scenario(setup, spend=(500, 500), manual=False):
    config, admin, account, alice, bob, keys, _ = setup
    start = datetime(2025, 2, 1, tzinfo=UTC)  # exactly four natural weeks
    now = start + timedelta(days=7, hours=1)
    account.created_at = start
    account.save()
    pool = account.pool
    pool.cpa_billing_anchor = start.date()
    pool.cpa_billing_timezone = "UTC"
    pool.save()
    config.weekly_quota_model = "time_varying"
    config.save()
    account.cpa_contracts.update(effective_at=start)
    for key in keys:
        key.bindings.update(started_at=start)
    CPAAccountCollectionInterval.objects.create(
        account=account,
        connected_at=start,
        disconnected_at=now,
        end_reliable=True,
        session_key="billing-test",
    )
    for key, value in zip(keys, spend):
        event(account, key, start + timedelta(hours=1), tokens=value * 100000)
    reset = start + timedelta(days=3 if manual else 7)
    old = observation(
        account,
        start,
        reset - timedelta(seconds=1),
        Decimal(sum(spend)) / 10,
        Decimal(sum(spend)),
    )
    old.valid_sample = True
    old.interval_used_percent = old.upstream_used_percent
    old.save()
    current = observation(account, reset, now, Decimal(0), Decimal(0))
    current.valid_sample = True
    current.save()
    return config, admin, account, alice, bob, keys, start, now


def report(s):
    config, admin, account, a, b, _, _, now = s
    return billing_summary(
        admin, account, config, now, owner_index(), {a.id: a, b.id: b}
    )


def test_four_cycles_compensation_and_no_balance_write(setup):
    s = scenario(setup, spend=(500, 250))
    config, admin, account, a, b, keys, start, now = s
    # Two members own 1/3 each; reserve the last third explicitly.
    account.cpa_contracts.update(
        allocations=[
            {"participant_id": a.id, "share_percent": "33.33333333333333"},
            {"participant_id": b.id, "share_percent": "33.33333333333333"},
        ]
    )
    r = report(s)
    assert r["reasons"] == []
    assert r["capacity_usd"] == 4000
    assert r["expired_usd"] == 250
    assert r["available_usd"] == 3000
    assert r["members"][0]["remaining_usd"] == pytest.approx(833.333333)
    assert r["members"][0]["completed_overuse_usd"] == pytest.approx(166.666667)
    assert r["members"][0]["projected_overuse"] is False
    a.refresh_from_db()
    assert a.latest_balance_usd == Decimal("123")


def test_manual_reset_counts_full_capacity_without_overlap(setup):
    s = scenario(setup, spend=(100, 100), manual=True)
    r = report(s)
    assert r["reasons"] == []
    assert r["cycles"][0]["capacity_usd"] == 1000
    assert r["expired_usd"] == 800
    assert r["capacity_usd"] == pytest.approx(1000 + 25000 / 7)
    assert r["cycles"][0]["ended_at"] == r["cycles"][1]["started_at"]


def test_reconnect_does_not_duplicate_cycle(setup):
    s = scenario(setup)
    config, _, account, _, _, _, start, now = s
    observation(account, start + timedelta(days=7), now - timedelta(minutes=1), 0, 0)
    assert len(cycle_rows(account, config, now)) == 2


def test_price_change_and_gap_fail_closed_but_keep_usage(setup):
    s = scenario(setup)
    config, _, account, *_ = s
    config.cpa_model_pricing["gpt-test"]["input"] = "20"
    r = report(s)
    assert r["capacity_usd"] is None
    assert r["usage_usd"] == 2000
    assert r["members"][0]["recommended_usd"] is None
    CPAAccountCollectionInterval.objects.filter(account=account).delete()
    assert "采集覆盖不完整" in report(s)["reasons"]


def test_unknown_contract_and_no_observation(setup):
    s = scenario(setup)
    s[2].cpa_contracts.all().delete()
    assert report(s)["members"][0]["entitlement_usd"] is None
    from monitor.models import Observation

    Observation.objects.all().delete()
    assert report(s)["capacity_usd"] is None


def test_admin_config_and_readonly_scope(setup):
    from django.test import Client
    from monitor.tests.helpers import jwt_login

    config, admin, account, alice, bob, keys, start = setup
    user, client, headers = member_client(account, alice)
    url = f"/api/cpa/billing-config?account_id={account.id}"
    payload = {"anchor_date": "2025-01-31", "timezone": "Asia/Shanghai"}
    assert (
        client.put(url, payload, content_type="application/json", **headers).status_code
        == 403
    )
    client = Client()
    headers, _ = jwt_login(client, "owner")
    assert (
        client.put(url, payload, content_type="application/json", **headers).status_code
        == 200
    )
    assert (
        client.put(
            url,
            {**payload, "timezone": "../invalid"},
            content_type="application/json",
            **headers,
        ).status_code
        == 400
    )


def test_three_equal_members_exact_example(setup):
    from monitor.models import Participant, CPAAPIKey, CPAKeyBinding

    s = scenario(setup, spend=(500, 250))
    config, admin, account, a, b, _, start, now = s
    c = Participant.objects.create(name="C")
    key = CPAAPIKey.objects.create(key_hash="c" * 64, hint="cccc")
    CPAKeyBinding.objects.create(key=key, participant=c, started_at=start)
    event(account, key, start + timedelta(hours=2), tokens=25000000)
    account.cpa_contracts.update(
        allocations=[
            {"participant_id": p.id, "share_percent": "33.3333333333333333"}
            for p in (a, b, c)
        ]
    )
    from monitor.models import Observation

    Observation.objects.filter(
        account_id=account.fact_key, observed_at__lt=start + timedelta(days=7)
    ).update(
        selected_total_cost=1000, upstream_used_percent=100, interval_used_percent=100
    )
    r = billing_summary(
        admin, account, config, now, owner_index(), {p.id: p for p in (a, b, c)}
    )
    assert r["reasons"] == []
    assert r["members"][0]["recommended_usd"] / 3 == pytest.approx(277.777777)
    assert r["members"][0]["recommended_percent"] == pytest.approx(27.777777)
    assert sum(m["recommended_usd"] for m in r["members"]) <= r["available_usd"]


@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_weighted_capacity_and_share_change(setup, model):
    s = scenario(setup)
    config, _, account, a, b, _, start, now = s
    config.weekly_quota_model = model
    from monitor.models import Observation

    Observation.objects.filter(account_id=account.fact_key, observed_at=now).update(
        effective_usd_per_percent=20
    )
    CPAQuotaContract.objects.create(
        account=account,
        effective_at=start + timedelta(days=7),
        pool_id_at_capture=account.pool_id,
        pool_name=account.pool.name,
        revision=2,
        allocations=[
            {"participant_id": a.id, "share_percent": "25"},
            {"participant_id": b.id, "share_percent": "75"},
        ],
    )
    r = report(s)
    assert r["capacity_usd"] == 7000
    assert r["members"][0]["entitlement_usd"] == 2000  # 1000*50% + 6000*25%
    assert r["members"][0]["remaining_usd"] == 1500


def test_period_overuse_and_unallocated_reserve(setup):
    s = scenario(setup)
    account, a, b = s[2:5]
    account.cpa_contracts.update(
        allocations=[
            {"participant_id": a.id, "share_percent": "10"},
            {"participant_id": b.id, "share_percent": "50"},
        ]
    )
    r = report(s)
    assert r["members"][0]["projected_overuse"] is True
    assert r["members"][0]["remaining_usd"] == -100
    assert r["members"][0]["recommended_usd"] == 0
    assert r["unallocated_usd"] == 1600
    assert sum(m["recommended_usd"] for m in r["members"]) < r["available_usd"]


def test_cross_billing_boundary_capacity_prorated_requests_not_prorated(setup):
    s = scenario(setup)
    account = s[2]
    account.pool.cpa_billing_anchor = date(2025, 2, 4)
    account.pool.save()
    r = report(s)
    assert r["cycles"][0]["capacity_usd"] == pytest.approx(4000 / 7)
    assert r["usage_usd"] == 0  # all requests occurred before February 4
    assert r["capacity_usd"] == 4000


def test_zero_consumption_and_missing_price(setup):
    s = scenario(setup, spend=(0, 0))
    assert report(s)["usage_usd"] == 0
    assert report(s)["expired_usd"] == 1000
    event(s[2], s[5][0], s[6] + timedelta(hours=3), model="missing")
    r = report(s)
    assert r["capacity_usd"] is None
    assert "请求缺少模型价格" in r["reasons"]


def test_historical_pool_move_filters_usage_and_entitlement(setup):
    from monitor.models import QuotaPool

    s = scenario(setup)
    account, a, b = s[2:5]
    old = QuotaPool.objects.create(name="former")
    account.cpa_contracts.update(pool_id_at_capture=old.id)
    CPAQuotaContract.objects.create(
        account=account,
        effective_at=s[6] + timedelta(days=7),
        pool_id_at_capture=account.pool_id,
        pool_name=account.pool.name,
        revision=2,
        allocations=[
            {"participant_id": a.id, "share_percent": "50"},
            {"participant_id": b.id, "share_percent": "50"},
        ],
    )
    r = report(s)
    assert r["usage_usd"] == 0
    assert r["capacity_usd"] == 3000
    assert r["expired_usd"] == 0


def test_former_member_and_unattributed_are_separate(setup):
    s = scenario(setup)
    from monitor.models import CPAUsageEvent

    CPAUsageEvent.objects.filter(account=s[2], api_key_hash=s[5][1].key_hash).update(
        api_key_hash="unbound"
    )
    r = billing_summary(s[1], s[2], s[0], s[7], owner_index(), {})
    assert r["unattributed_usd"] == 500
    assert r["other_members_usd"] == 500


def test_authorized_multi_account_and_readonly_identical(setup, monkeypatch):
    from monitor.tests.helpers import create_cpa_account
    from monitor.models import SystemUserAPIKey
    from monitor.api_auth import hash_api_key
    from monitor.cpa.reporting import pool_summary

    s = scenario(setup)
    config, admin, account, a, b, _, start, now = s
    other = create_cpa_account(auth_index="other")
    other.pool = account.pool
    other.save()
    # No observations for unauthorized account must not poison or reveal its summary.
    user, client, headers = member_client(account, a)
    monkeypatch.setattr("monitor.cpa.reporting.timezone.now", lambda: now)
    data = pool_summary(user, account, config)
    assert data["partial_scope"] is True
    assert data["billing_summary"]["capacity_usd"] == 4000
    assert {r["account_id"] for r in data["billing_summary"]["cycles"]} == {account.id}
    SystemUserAPIKey.objects.create(
        user=user, key_hash=hash_api_key("billing-test-token"), hint="test"
    )
    response = client.get(
        f"/api/v1/cpa/summary?account_id={account.id}",
        HTTP_AUTHORIZATION="Bearer billing-test-token",
    )
    assert response.status_code == 200
    assert response.json()["data"]["billing_summary"] == data["billing_summary"]
    assert client.get(
        f"/api/v1/cpa/summary?account_id={other.id}",
        HTTP_AUTHORIZATION="Bearer billing-test-token",
    ).status_code in (400, 403, 404)


def test_future_fallback_uses_median_of_three_reliable_completed_cycles(setup):
    s = scenario(setup)
    from monitor.models import Observation

    config, _, account, _, _, _, start, now = s
    CPAAccountCollectionInterval.objects.filter(account=account).update(
        connected_at=start - timedelta(days=21)
    )
    for weeks, rate in ((3, 1), (2, 3), (1, 2)):
        begin = start - timedelta(days=7 * weeks)
        row = observation(account, begin, begin + timedelta(days=6), 0, 0)
        row.valid_sample = True
        row.effective_usd_per_percent = rate
        row.save()
    Observation.objects.filter(account_id=account.fact_key, observed_at=now).update(
        valid_sample=False
    )
    r = report(s)
    assert r["capacity_usd"] is None
    assert r["future_capacity_usd"] == 600  # median(300, 200, 1000) * two future weeks
    assert all(
        c["full_capacity_usd"] == 300 for c in r["cycles"] if c["kind"] == "future"
    )
    assert r["members"][0]["recommended_usd"] is None


def test_late_request_invalidates_capacity_without_duplicate_accounting(setup):
    s = scenario(setup)
    initial = report(s)
    assert report(s) == initial
    event(s[2], s[5][0], s[6] + timedelta(days=2))
    changed = report(s)
    assert changed["usage_usd"] == initial["usage_usd"] + 10
    assert changed["capacity_usd"] is None
    assert report(s) == changed


def test_future_anchor_has_a_valid_first_period():
    assert billing_bounds(
        date(2027, 1, 31), "UTC", datetime(2026, 9, 8, tzinfo=UTC)
    ) == (datetime(2027, 1, 31, tzinfo=UTC), datetime(2027, 2, 28, tzinfo=UTC))


def test_authorized_accounts_sum_dollars_not_percentages(setup):
    from monitor.tests.helpers import create_cpa_account
    from monitor.cpa.participants import record_contract

    s = scenario(setup)
    config, admin, account, a, b, _, start, now = s
    other = create_cpa_account(auth_index="second-authorized")
    other.pool = account.pool
    other.created_at = start
    other.save()
    record_contract(other, start)
    CPAAccountCollectionInterval.objects.create(
        account=other,
        session_key="second",
        connected_at=start,
        disconnected_at=now,
        end_reliable=True,
    )
    for begin, at in (
        (start, start + timedelta(days=6)),
        (start + timedelta(days=7), now),
    ):
        row = observation(other, begin, at, 0, 0)
        row.valid_sample = True
        row.effective_usd_per_percent = 20
        row.save()
    user, _, _ = member_client(account, a)
    other.authorized_users.add(user)
    result = billing_summary(
        user, account, config, now, owner_index(), {a.id: a, b.id: b}
    )
    assert result["capacity_usd"] == 12000
    assert result["members"][0]["usage_percent"] == pytest.approx(500 / 12000 * 100)
    assert result["expired_usd"] == 2000


def test_history_claim_is_visible_on_next_billing_read(setup):
    from monitor.cpa.participants import preview_claim, apply_claim
    from monitor.models import CPAUsageEvent

    s = scenario(setup)
    config, admin, account, a, b, keys, start, now = s
    keys[0].bindings.update(started_at=start + timedelta(days=7))
    initial = report(s)
    assert initial["unattributed_usd"] == 500
    raw = list(CPAUsageEvent.objects.filter(account=account).order_by("id").values())
    plan = preview_claim(
        key=keys[0],
        participant=a,
        started_at=start,
        ended_at=start + timedelta(days=7),
        user=admin,
    )
    apply_claim(plan.id)
    apply_claim(plan.id)
    result = report(s)
    assert result["unattributed_usd"] == 0
    assert result["members"][0]["usage_usd"] == 500
    assert result["usage_usd"] == initial["usage_usd"]
    assert (
        list(CPAUsageEvent.objects.filter(account=account).order_by("id").values())
        == raw
    )
