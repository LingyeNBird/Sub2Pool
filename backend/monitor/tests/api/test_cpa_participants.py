from datetime import timedelta
from decimal import Decimal
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from monitor.api_auth import hash_api_key
from monitor.cpa.participants import (
    apply_claim,
    bind_key,
    preview_claim,
    record_contract,
    unbind_key,
    ownership_filter,
)
from monitor.cpa.reporting import pool_summary
from monitor.cpa.usage import _api_key_identity, persist_usage_event
from monitor.models import (
    AppSettings,
    CPAAPIKey,
    CPAKeyBinding,
    CPAUsageEvent,
    CPAAccountCollectionInterval,
    Participant,
    ParticipantSnapshot,
    PoolParticipant,
    Observation,
    SystemUserAPIKey,
    SystemUserPageAccess,
)
from monitor.replay import rebuild_account
from monitor.serializers import (
    ParticipantWriteSerializer,
    QuotaAllocationWriteSerializer,
)
from monitor.tests.helpers import (
    create_cpa_account,
    create_monitored_account,
    jwt_login,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    config = AppSettings.load()
    config.cpa_model_pricing = {
        "gpt-test": {"input": "10", "cached_input": "1", "output": "30"}
    }
    config.save()
    admin = get_user_model().objects.create_superuser(
        "owner", "owner@example.com", "very-strong-password"
    )
    account = create_cpa_account()
    alice = Participant.objects.create(
        name="Alice", sub2api_user_id=51, latest_balance_usd=Decimal("123")
    )
    bob = Participant.objects.create(name="Bob")
    for person in (alice, bob):
        PoolParticipant.objects.create(
            pool=account.pool, participant=person, share_percent=50
        )
    start = timezone.now() - timedelta(hours=2)
    account.created_at = start
    account.save(update_fields=["created_at"])
    record_contract(account, start)
    keys = []
    for person, raw in ((alice, "alice-cpa-key"), (bob, "bob-cpa-key")):
        digest, hint = _api_key_identity(raw)
        key = CPAAPIKey.objects.create(key_hash=digest, hint=hint)
        CPAKeyBinding.objects.create(key=key, participant=person, started_at=start)
        keys.append(key)
    return config, admin, account, alice, bob, keys, start


def event(account, key, when, *, tokens=1_000_000, model="gpt-test", failed=False):
    return CPAUsageEvent.objects.create(
        account=account,
        event_fingerprint=uuid.uuid4().hex,
        request_id=uuid.uuid4().hex,
        occurred_at=when,
        api_key_hash=key.key_hash,
        api_key_hint=key.hint,
        model=model,
        input_tokens=tokens,
        total_tokens=tokens,
        failed=failed,
    )


def observation(account, start, when, percent, cost):
    return Observation.objects.create(
        account_id=account.fact_key,
        observed_at=when,
        upstream_resets_at=start + timedelta(days=7),
        window_seconds=604800,
        upstream_used_percent=percent,
        effective_usd_per_percent=Decimal("10"),
        raw_selected_total_cost=cost,
        selected_total_cost=cost,
        total_standard_cost=cost,
        total_actual_cost=cost,
        cost_window_started_at=start,
        cost_window_ended_at=when,
        raw_window={"provider": "cpa"},
    )


def member_client(account, participant, name="rider"):
    user = get_user_model().objects.create_user(name, password="very-strong-password")
    participant.authorized_users.add(user)
    account.authorized_users.add(user)
    for page in ("dashboard", "participants", "statistics", "observations"):
        SystemUserPageAccess.objects.create(user=user, page_code=page)
    client = Client()
    headers, _ = jwt_login(client, name)
    return user, client, headers


def test_nullable_identity_and_channel_partition(setup):
    config, admin, account, alice, bob, keys, start = setup
    serializer = ParticipantWriteSerializer(data={"name": "CPA only"})
    assert serializer.is_valid(), serializer.errors
    person = serializer.save()
    assert person.sub2api_user_id is None
    assert not person.account_memberships.exists()
    sub = create_monitored_account(7)
    payload = {
        "provider": "cpa",
        "pools": [{"account_ids": [account.id, sub.id], "allocations": []}],
    }
    serializer = QuotaAllocationWriteSerializer(data=payload)
    assert not serializer.is_valid()
    serializer = QuotaAllocationWriteSerializer(
        data={
            "provider": "cpa",
            "pools": [
                {
                    "id": account.pool_id,
                    "account_ids": [account.id],
                    "allocations": [{"participant_id": bob.id, "share_percent": 100}],
                }
            ],
        }
    )
    assert serializer.is_valid(), serializer.errors
    serializer.apply()
    sub.refresh_from_db()
    assert sub.pool_id != account.pool_id
    assert account.cpa_contracts.count() == 2


def test_multi_key_binding_no_plaintext_or_overlap_and_unbind(setup):
    config, admin, account, alice, bob, keys, start = setup
    binding = bind_key(
        participant=alice, user=admin, raw_key="new-private-cpa-key", name="Laptop"
    )
    assert binding.key.key_hash != "new-private-cpa-key"
    assert binding.key.hint == "-key"
    with pytest.raises(ValidationError):
        bind_key(participant=bob, user=admin, raw_key="new-private-cpa-key")
    unbind_key(binding.id)
    replacement = bind_key(participant=bob, user=admin, raw_key="new-private-cpa-key")
    assert replacement.started_at >= CPAKeyBinding.objects.get(pk=binding.id).ended_at
    assert alice.cpa_bindings.count() == 2


@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_model_replay_and_pool_summary_preserve_sub2api_balance(setup, model):
    config, admin, account, alice, bob, keys, start = setup
    config.weekly_quota_model = model
    config.save()
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="live", connected_at=start
    )
    observation(account, start, start, 0, 0)
    event(account, keys[0], start + timedelta(minutes=10))
    event(account, keys[1], start + timedelta(minutes=20), tokens=2_000_000)
    end = observation(account, start, start + timedelta(hours=1), 10, 30)
    rebuild_account(account.fact_key, config)
    alice.refresh_from_db()
    assert alice.latest_balance_usd == Decimal("123")
    first = ParticipantSnapshot.objects.get(observation=end, participant=alice)
    second = ParticipantSnapshot.objects.get(observation=end, participant=bob)
    assert first.raw_selected_cost == 10
    assert second.raw_selected_cost == 20
    assert first.charged_cycle_percent > 0
    assert first.charged_cycle_percent < second.charged_cycle_percent
    assert first.recommended_balance_usd is None
    assert not first.needs_manual_update
    summary = pool_summary(admin, account, config)
    assert all(row["quota_available"] for row in summary["members"])
    assert summary["accounts"][0]["quota_unavailable_reasons"] == []
    assert all(not row["is_self"] for row in summary["members"])
    assert all(
        row["account_breakdowns"][0]["quota_unavailable_reasons"] == []
        for row in summary["members"]
    )
    assert sum(row["usage_usd"] for row in summary["members"]) == 30
    before = list(
        end.participant_snapshots.order_by("participant_id").values(
            "selected_cost", "charged_cycle_percent"
        )
    )
    rebuild_account(account.fact_key, config)
    assert (
        list(
            end.participant_snapshots.order_by("participant_id").values(
                "selected_cost", "charged_cycle_percent"
            )
        )
        == before
    )


def test_peer_totals_own_requests_and_readonly_scope(setup):
    config, admin, account, alice, bob, keys, start = setup
    a = event(account, keys[0], start + timedelta(minutes=10))
    b = event(account, keys[1], start + timedelta(minutes=20), tokens=2_000_000)
    user, client, headers = member_client(account, alice)
    response = client.get(f"/api/cpa/summary?account_id={account.id}", **headers)
    assert response.status_code == 200, response.content
    rows = response.json()["data"]["members"]
    assert {row["participant_name"] for row in rows} == {"Alice", "Bob"}
    assert "key_hash" not in str(rows) and "request_id" not in str(rows)
    for route in ("cpa/requests", "v1/cpa/requests"):
        chosen_headers = headers
        if route.startswith("v1/"):
            SystemUserAPIKey.objects.create(
                user=user, key_hash=hash_api_key("user-read-key"), hint="-key"
            )
            chosen_headers = {"HTTP_AUTHORIZATION": "Bearer user-read-key"}
        own = client.get(f"/api/{route}?account_id={account.id}", **chosen_headers)
        assert own.status_code == 200, own.content
        assert [row["id"] for row in own.json()["data"]["items"]] == [a.id]
        assert (
            client.get(
                f"/api/{route}?account_id={account.id}&participant_id={bob.id}",
                **chosen_headers,
            ).status_code
            == 400
        )
        assert (
            client.get(
                f"/api/{route}?account_id={account.id}&key_id={keys[1].id}",
                **chosen_headers,
            ).status_code
            == 404
        )
    stats = client.get(
        f"/api/statistics?account_id={account.id}&usage_precision=raw", **headers
    )
    assert len(stats.json()["data"]["cpa_api_key_series"]) == 1
    other = create_cpa_account("private")
    other.authorized_users.add(user)
    assert (
        client.get(f"/api/cpa/summary?account_id={other.id}", **headers).status_code
        == 400
    )
    allocation = client.get("/api/quota-allocation?provider=cpa", **headers).json()[
        "data"
    ]
    assert [row["id"] for row in allocation["accounts"]] == [account.id]
    assert all(row["sub2api_email"] == "" for row in allocation["participants"])
    assert b.request_id not in str(
        client.get(f"/api/cpa/requests?account_id={account.id}", **headers).json()
    )


def test_admin_readonly_requests_principal(setup):
    config, admin, account, alice, bob, keys, start = setup
    config.readonly_api_key_hash = hash_api_key("admin-read-key")
    config.save()
    event(account, keys[1], start + timedelta(minutes=20))
    client = Client()
    response = client.get(
        f"/api/v1/cpa/requests?account_id={account.id}",
        HTTP_AUTHORIZATION="Bearer admin-read-key",
    )
    assert response.status_code == 200, response.content
    assert response.json()["data"]["total"] == 1


def test_claim_is_explicit_idempotent_and_does_not_invent_contract(setup):
    config, admin, account, alice, bob, keys, start = setup
    earlier = start - timedelta(hours=2)
    raw = event(account, keys[0], earlier + timedelta(minutes=5))
    old = observation(account, earlier, earlier + timedelta(hours=1), 10, 10)
    before = CPAUsageEvent.objects.get(pk=raw.id).__dict__.copy()
    plan = preview_claim(
        key=keys[0], participant=alice, started_at=earlier, ended_at=start, user=admin
    )
    assert plan.preview["accounts"][0]["request_count"] == 1
    assert not plan.preview["accounts"][0]["coverage"]["complete"]
    assert CPAKeyBinding.objects.count() == 2
    apply_claim(plan.id)
    apply_claim(plan.id)
    assert CPAKeyBinding.objects.count() == 3
    snapshot = ParticipantSnapshot.objects.get(observation=old, participant=alice)
    assert not snapshot.cpa_contract_known
    assert snapshot.quota_pool_id is None
    fresh = CPAUsageEvent.objects.get(pk=raw.id).__dict__
    assert {k: v for k, v in fresh.items() if k != "_state"} == {
        k: v for k, v in before.items() if k != "_state"
    }


def test_claim_does_not_automatically_own_late_historical_events(setup):
    config, admin, account, alice, bob, keys, start = setup
    earlier = start - timedelta(hours=2)
    original = event(account, keys[0], earlier + timedelta(minutes=5))
    old = observation(account, earlier, earlier + timedelta(hours=1), 10, 20)
    plan = preview_claim(
        key=keys[0], participant=alice, started_at=earlier, ended_at=start, user=admin
    )
    apply_claim(plan.id)
    late = event(account, keys[0], earlier + timedelta(minutes=6))
    rebuild_account(account.fact_key, config)
    assert (
        ParticipantSnapshot.objects.get(
            observation=old, participant=alice
        ).raw_selected_cost
        == 10
    )
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([alice.id])).values_list(
            "id", flat=True
        )
    ) == {original.id}
    with pytest.raises(ValidationError):
        preview_claim(
            key=keys[0], participant=bob, started_at=earlier, ended_at=start, user=admin
        )
    new_plan = preview_claim(
        key=keys[0], participant=alice, started_at=earlier, ended_at=start, user=admin
    )
    assert new_plan.preview["accounts"][0]["request_count"] == 1
    apply_claim(new_plan.id)
    apply_claim(new_plan.id)
    assert (
        ParticipantSnapshot.objects.get(
            observation=old, participant=alice
        ).raw_selected_cost
        == 20
    )
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([alice.id])).values_list(
            "id", flat=True
        )
    ) == {original.id, late.id}


@pytest.mark.parametrize("change", ["events", "pricing", "binding"])
def test_claim_rejects_changed_preview(setup, change):
    config, admin, account, alice, bob, keys, start = setup
    earlier = start - timedelta(hours=2)
    event(account, keys[0], earlier + timedelta(minutes=5))
    plan = preview_claim(
        key=keys[0], participant=alice, started_at=earlier, ended_at=start, user=admin
    )
    if change == "events":
        event(account, keys[0], earlier + timedelta(minutes=6))
    elif change == "pricing":
        config.cpa_fast_multiplier = 4
        config.save()
    else:
        unbind_key(keys[0].bindings.get().id)
    with pytest.raises(ValidationError, match="重新预览"):
        apply_claim(plan.id)


def test_unknown_models_and_collection_gap_do_not_show_remaining(setup):
    config, admin, account, alice, bob, keys, start = setup
    event(account, keys[0], start + timedelta(minutes=10), model="unpriced")
    observation(account, start, start + timedelta(hours=1), 10, 0)
    rebuild_account(account.fact_key, config)
    summary = pool_summary(admin, account, config)
    assert summary["members"][0]["remaining_entitlement_usd"] is None
    assert summary["members"][0]["unpriced_request_count"] == 1
    assert not summary["accounts"][0]["coverage"]["complete"]
    reasons = summary["accounts"][0]["quota_unavailable_reasons"]
    assert "1 次请求缺少模型价格" in reasons
    assert any("采集缺口" in reason for reason in reasons)
    assert set(reasons) <= set(
        summary["members"][0]["account_breakdowns"][0]["quota_unavailable_reasons"]
    )


def test_request_filters_and_participant_with_history_cannot_be_deleted(setup):
    config, admin, account, alice, bob, keys, start = setup
    event(account, keys[0], start + timedelta(minutes=10))
    failed = event(account, keys[0], start + timedelta(minutes=20), failed=True)
    client = Client()
    headers, _ = jwt_login(client)
    assert (
        client.get(f"/api/cpa/requests?account_id={account.id}", **headers).json()[
            "data"
        ]["total"]
        == 2
    )
    response = client.get(
        f"/api/cpa/requests?account_id={account.id}&failed=true&page_size=1", **headers
    )
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["id"] == failed.id
    assert (
        client.get(
            f"/api/cpa/requests?account_id={account.id}&page_size=101", **headers
        ).status_code
        == 400
    )
    assert client.delete(f"/api/participants/{bob.id}", **headers).status_code == 409


def test_late_usage_duplicate_and_repricing_refresh_participant_inputs(setup):
    from monitor.cpa.usage import refresh_cpa_history

    config, admin, account, alice, bob, keys, start = setup
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="live", connected_at=start
    )
    observation(account, start, start, 0, 0)
    end = observation(account, start, start + timedelta(hours=1), 10, 0)
    payload = {
        "auth_index": account.cpa_auth_index,
        "timestamp": (start + timedelta(minutes=20)).isoformat(),
        "request_id": "late-cpa-event",
        "model": "gpt-test",
        "api_key": "alice-cpa-key",
        "tokens": {"input_tokens": 1_000_000, "total_tokens": 1_000_000},
    }
    assert persist_usage_event(payload) == "created"
    assert persist_usage_event(payload) == "duplicate"
    assert CPAUsageEvent.objects.filter(request_id="late-cpa-event").count() == 1
    assert (
        ParticipantSnapshot.objects.get(
            observation=end, participant=alice
        ).raw_selected_cost
        == 10
    )
    config.cpa_model_pricing["gpt-test"]["input"] = "20"
    config.save()
    refresh_cpa_history(config)
    assert (
        ParticipantSnapshot.objects.get(
            observation=end, participant=alice
        ).raw_selected_cost
        == 20
    )
    alice.refresh_from_db()
    assert alice.latest_balance_usd == 123


def test_pool_partial_scope_does_not_expose_hidden_account_cost(setup):
    config, admin, account, alice, bob, keys, start = setup
    hidden = create_cpa_account("same-pool-hidden")
    hidden.pool = account.pool
    hidden.save(update_fields=["pool"])
    event(account, keys[0], start + timedelta(minutes=10))
    event(hidden, keys[0], start + timedelta(minutes=20), tokens=50_000_000)
    user, client, headers = member_client(account, alice)
    summary = client.get(f"/api/cpa/summary?account_id={account.id}", **headers).json()[
        "data"
    ]
    assert summary["partial_scope"]
    assert [a["account_id"] for a in summary["accounts"]] == [account.id]
    assert (
        next(row for row in summary["members"] if row["participant_id"] == alice.id)[
            "usage_usd"
        ]
        == 10
    )


def test_positive_baseline_does_not_claim_complete_personal_quota(setup):
    config, admin, account, alice, bob, keys, start = setup
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="live", connected_at=start
    )
    observation(account, start, start + timedelta(minutes=10), 10, 10)
    event(account, keys[0], start + timedelta(minutes=5))
    event(account, keys[1], start + timedelta(minutes=20))
    observation(account, start, start + timedelta(hours=1), 20, 20)
    rebuild_account(account.fact_key, config)
    assert all(
        not row["quota_available"]
        for row in pool_summary(admin, account, config)["members"]
    )


def test_cpa_membership_does_not_enter_sub2api_balance_guards(setup, monkeypatch):
    config, admin, account, alice, bob, keys, start = setup
    sub = create_monitored_account(7)
    PoolParticipant.objects.create(pool=sub.pool, participant=alice, share_percent=50)
    captured = []
    from monitor.history_state import LeaseBusyError

    def stop_after_scope(account_ids):
        captured.append(account_ids)
        raise LeaseBusyError("test scope")

    monkeypatch.setattr("monitor.views.dashboard._acquire_guards", stop_after_scope)
    client = Client()
    headers, _ = jwt_login(client)
    assert (
        client.post(
            f"/api/dashboard/participants/{alice.id}/apply-recommendation", **headers
        ).status_code
        == 409
    )
    assert captured == [{7}]
    assert (
        client.post(
            f"/api/dashboard/participants/{bob.id}/apply-recommendation", **headers
        ).status_code
        == 400
    )


def test_rebind_registered_key_without_events_and_keep_contract_identity(setup):
    config, admin, account, alice, bob, keys, start = setup
    binding = bind_key(participant=alice, user=admin, raw_key="never-used-key")
    unbind_key(binding.id)
    new = bind_key(participant=bob, user=admin, observed_hash=binding.key.key_hash)
    assert new.key_id == binding.key_id
    person = Participant.objects.create(name="Contract only")
    PoolParticipant.objects.create(
        pool=account.pool, participant=person, share_percent=0
    )
    record_contract(account, timezone.now())
    client = Client()
    headers, _ = jwt_login(client)
    assert client.delete(f"/api/participants/{person.id}", **headers).status_code == 409


@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_pool_combines_account_credits_and_keeps_unassigned_cost(setup, model):
    config, admin, account, alice, bob, keys, start = setup
    config.weekly_quota_model = model
    config.save()
    second = create_cpa_account("second-pool-account")
    second.pool = account.pool
    second.created_at = start
    second.save(update_fields=["pool", "created_at"])
    record_contract(second, start)
    unknown = CPAAPIKey.objects.create(key_hash="f" * 64, hint="0000")
    for selected in (account, second):
        CPAAccountCollectionInterval.objects.create(
            account=selected, session_key="live", connected_at=start
        )
        observation(selected, start, start, 0, 0)
    event(account, keys[0], start + timedelta(minutes=10), tokens=6_000_000)
    observation(account, start, start + timedelta(hours=1), 60, 60)
    event(second, unknown, start + timedelta(minutes=10), tokens=5_000_000)
    observation(second, start, start + timedelta(hours=1), 10, 50)
    for selected in (account, second):
        rebuild_account(selected.fact_key, config)
    summary = pool_summary(admin, account, config)
    member = next(
        row for row in summary["members"] if row["participant_id"] == alice.id
    )
    assert member["quota_available"]
    assert len(member["account_breakdowns"]) == 2
    assert member["usage_usd"] == 60
    assert member["remaining_entitlement_usd"] == pytest.approx(
        sum(row["remaining_entitlement_usd"] for row in member["account_breakdowns"])
    )
    assert not member["is_overused"]
    assert summary["unattributed"]["usage_usd"] == 50
    second.enabled = False
    second.save(update_fields=["enabled"])
    single = pool_summary(admin, account, config)
    assert next(row for row in single["members"] if row["participant_id"] == alice.id)[
        "is_overused"
    ]


@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_cpa_participant_cost_resets_with_official_cycle(setup, model):
    config, admin, account, alice, bob, keys, start = setup
    config.weekly_quota_model = model
    config.save()
    previous_start = start - timedelta(days=7)
    record_contract(account, previous_start)
    keys[0].bindings.update(started_at=previous_start)
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="live", connected_at=previous_start
    )
    observation(account, previous_start, previous_start, 0, 0)
    event(account, keys[0], previous_start + timedelta(hours=1), tokens=9_000_000)
    old = observation(account, previous_start, start - timedelta(minutes=5), 90, 90)
    observation(account, start, start, 0, 0)
    event(account, keys[0], start + timedelta(minutes=10))
    current = observation(account, start, start + timedelta(hours=1), 10, 10)
    rebuild_account(account.fact_key, config)
    assert (
        ParticipantSnapshot.objects.get(
            observation=old, participant=alice
        ).raw_selected_cost
        == 90
    )
    snapshot = ParticipantSnapshot.objects.get(observation=current, participant=alice)
    assert snapshot.raw_selected_cost == 10
    assert snapshot.charged_cycle_percent < 50
    member = next(
        row
        for row in pool_summary(admin, account, config)["members"]
        if row["participant_id"] == alice.id
    )
    assert member["usage_usd"] == 10
    assert not member["is_overused"]


def test_detached_sub2api_identity_keeps_historical_replay_subject(setup):
    from monitor.models import AccountParticipant
    from monitor.tests.helpers import create_participant_snapshot

    config, admin, account, alice, bob, keys, start = setup
    sub = create_monitored_account(7)
    AccountParticipant.objects.create(account=sub, participant=alice)
    PoolParticipant.objects.create(pool=sub.pool, participant=alice, share_percent=50)
    for when, percent, cost in ((start, 0, 0), (start + timedelta(hours=1), 10, 10)):
        row = observation(sub, start, when, percent, cost)
        create_participant_snapshot(
            observation=row,
            participant=alice,
            raw_selected_cost=cost,
            selected_cost=cost,
        )
    rebuild_account(sub.fact_key, config)
    before = list(
        ParticipantSnapshot.objects.filter(observation__account_id=7)
        .order_by("id")
        .values(
            "raw_selected_cost",
            "selected_cost",
            "charged_cycle_percent",
            "source_sub2api_user_id",
        )
    )
    serializer = ParticipantWriteSerializer(
        alice, data={"sub2api_user_id": None}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    rebuild_account(sub.fact_key, config)
    assert (
        list(
            ParticipantSnapshot.objects.filter(observation__account_id=7)
            .order_by("id")
            .values(
                "raw_selected_cost",
                "selected_cost",
                "charged_cycle_percent",
                "source_sub2api_user_id",
            )
        )
        == before
    )


def test_no_observation_explains_unknown_without_hiding_collected_usage(setup):
    config, admin, account, alice, bob, keys, start = setup
    event(account, keys[0], start + timedelta(minutes=10))
    summary = pool_summary(admin, account, config)
    assert "尚无额度观测" in summary["accounts"][0]["quota_unavailable_reasons"]
    member = next(
        row for row in summary["members"] if row["participant_id"] == alice.id
    )
    assert member["usage_usd"] == 10
    assert member["remaining_entitlement_usd"] is None
    assert (
        "缺少历史份额依据或对应额度观测"
        in member["account_breakdowns"][0]["quota_unavailable_reasons"]
    )


@pytest.mark.parametrize("readonly", [False, True])
def test_request_key_alias_and_local_note_fallback_are_scoped(setup, readonly):
    config, admin, account, alice, bob, keys, start = setup
    first = event(account, keys[0], start + timedelta(minutes=1))
    first.alias = 'Le'
    first.save(update_fields=['alias'])
    second = event(account, keys[0], start + timedelta(minutes=2))
    keys[0].name = 'Laptop'
    keys[0].save(update_fields=['name'])
    private = event(account, keys[1], start + timedelta(minutes=3))
    private.alias = 'Other private alias'
    private.save(update_fields=['alias'])
    user, client, headers = member_client(account, alice)
    path = 'cpa/requests'
    if readonly:
        SystemUserAPIKey.objects.create(user=user, key_hash=hash_api_key('alias-read-key'), hint='-key')
        headers = {'HTTP_AUTHORIZATION': 'Bearer alias-read-key'}
        path = 'v1/cpa/requests'
    response = client.get(f'/api/{path}?account_id={account.id}', **headers)
    assert response.status_code == 200
    rows = {row['id']: row for row in response.json()['data']['items']}
    assert rows[first.id]['api_key_alias'] == 'Le'
    assert rows[second.id]['api_key_alias'] == 'Laptop'
    assert private.id not in rows
    assert 'Other private alias' not in response.content.decode()
    assert keys[0].key_hash not in response.content.decode()
