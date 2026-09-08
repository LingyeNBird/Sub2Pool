from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from monitor.api_auth import hash_api_key
from monitor.cpa.account_owner import (
    sync_account_owner,
    preview_unassigned_claim,
    account_owner_data,
)
from monitor.cpa.participants import (
    apply_claim,
    event_owner,
    owner_index,
    ownership_filter,
)
from monitor.cpa.reporting import pool_summary
from monitor.models import (
    CPAAccountOwnerBinding,
    CPAAPIKey,
    CPAClaimEvent,
    CPAUsageEvent,
    SystemUserAPIKey,
)
from monitor.serializers import ParticipantWriteSerializer
from monitor.tests.helpers import create_cpa_account, jwt_login
from monitor.tests.api.test_cpa_participants import (
    setup,
    event,
    member_client,
    observation,
)  # noqa: F401
from monitor.replay import rebuild_account

pytestmark = pytest.mark.django_db


def designate(person, account, when):
    person.is_owner = True
    person.save(update_fields=["is_owner"])
    return sync_account_owner(account, when)


def change_role(person, is_owner):
    serializer = ParticipantWriteSerializer(
        person, data={"is_owner": is_owner}, partial=True
    )
    assert serializer.is_valid()
    return serializer.save()


def test_role_is_account_scoped_key_priority_and_read_permissions(setup):
    config, admin, account, alice, bob, keys, start = setup
    designate(alice, account, start)
    other = create_cpa_account("other-owner-scope")
    keyless = CPAAPIKey(key_hash="", hint="")
    unknown = CPAAPIKey(key_hash="f" * 64, hint="9999")
    a = event(account, keyless, start + timedelta(minutes=10))
    u = event(account, unknown, start + timedelta(minutes=11))
    b = event(account, keys[1], start + timedelta(minutes=12))
    excluded = event(other, keyless, start + timedelta(minutes=13))
    bindings = owner_index()
    assert [event_owner(e, bindings) for e in [a, u, b, excluded]] == [
        alice.pk,
        alice.pk,
        bob.pk,
        None,
    ]
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([alice.pk])).values_list(
            "pk", flat=True
        )
    ) == {a.pk, u.pk}
    user, client, headers = member_client(account, alice)
    SystemUserAPIKey.objects.create(
        user=user, key_hash=hash_api_key("owner-read-key"), hint="-key"
    )
    for route, auth in [
        ("cpa/requests", headers),
        ("v1/cpa/requests", {"HTTP_AUTHORIZATION": "Bearer owner-read-key"}),
    ]:
        result = client.get(f"/api/{route}?account_id={account.pk}", **auth)
        assert result.status_code == 200
        assert {r["id"] for r in result.json()["data"]["items"]} == {a.pk, u.pk}
        assert (
            client.get(
                f"/api/{route}?account_id={account.pk}&participant_id={bob.pk}", **auth
            ).status_code
            == 400
        )
    _, peer, auth = member_client(account, bob, "peer")
    assert [
        r["id"]
        for r in peer.get(f"/api/cpa/requests?account_id={account.pk}", **auth).json()[
            "data"
        ]["items"]
    ] == [b.pk]
    summary = pool_summary(user, account, config)
    assert summary["accounts"][0]["owner"]["participant_id"] == alice.pk
    assert {r["participant_name"] for r in summary["members"]} == {"Alice", "Bob"}
    assert (
        client.post(
            f"/api/cpa/unassigned/preview?account_id={account.pk}", **headers
        ).status_code
        == 403
    )


@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_claim_is_frozen_idempotent_and_replay_stable(setup, model):
    config, admin, account, alice, bob, keys, start = setup
    config.weekly_quota_model = model
    config.save()
    keyless = CPAAPIKey(key_hash="", hint="")
    a = event(account, keyless, start + timedelta(minutes=10))
    b = event(account, keys[1], start + timedelta(minutes=20))
    designate(alice, account, timezone.now())
    raw = list(CPAUsageEvent.objects.order_by("pk").values())
    observation(account, start, start + timedelta(hours=1), 10, 20)
    plan = preview_unassigned_claim(account, alice, admin)
    assert plan.preview["accounts"][0]["request_count"] == 1
    assert not plan.preview["accounts"][0]["coverage"]["complete"]
    apply_claim(plan.pk)
    apply_claim(plan.pk)
    assert CPAClaimEvent.objects.filter(plan=plan).count() == 1
    assert list(CPAUsageEvent.objects.order_by("pk").values()) == raw
    assert event_owner(a, owner_index()) == alice.pk
    assert event_owner(b, owner_index()) == bob.pk
    first = pool_summary(admin, account, config)
    rebuild_account(account.fact_key, config)
    second = pool_summary(admin, account, config)
    assert first["members"] == second["members"]
    assert first["members"][0]["usage_usd"] == 10
    assert first["members"][0]["remaining_entitlement_usd"] is None
    late = event(account, keyless, start + timedelta(minutes=15))
    assert event_owner(late, owner_index()) is None
    assert (
        preview_unassigned_claim(account, alice, admin).preview["accounts"][0][
            "request_count"
        ]
        == 1
    )


@pytest.mark.parametrize("change", ["event", "price", "role", "binding"])
def test_preview_invalidates_changed_evidence(setup, change):
    config, admin, account, alice, bob, keys, start = setup
    keyless = CPAAPIKey(key_hash="", hint="")
    event(account, keyless, start + timedelta(minutes=10))
    designate(alice, account, timezone.now())
    plan = preview_unassigned_claim(account, alice, admin)
    if change == "event":
        event(account, keyless, start + timedelta(minutes=11))
    elif change == "price":
        config.cpa_fast_multiplier = 4
        config.save()
    elif change == "binding":
        binding = keys[0].bindings.get()
        binding.ended_at = timezone.now()
        binding.save()
    else:
        change_role(alice, False)
    with pytest.raises(ValidationError, match="重新预览"):
        apply_claim(plan.pk)
    assert not CPAClaimEvent.objects.filter(plan=plan).exists()


def test_role_changes_keep_history_and_ambiguous_owner_has_no_fallback(setup):
    config, admin, account, alice, bob, keys, start = setup
    designate(alice, account, start)
    keyless = CPAAPIKey(key_hash="", hint="")
    old = event(account, keyless, start + timedelta(minutes=10))
    switch = timezone.now()
    with patch("django.utils.timezone.now", return_value=switch):
        change_role(alice, False)
        change_role(bob, True)
    new = event(account, keyless, switch)
    assert event_owner(old, owner_index()) == alice.pk
    assert event_owner(new, owner_index()) == bob.pk
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([bob.pk])).values_list(
            "pk", flat=True
        )
    ) == {new.pk}
    designate(alice, account, switch + timedelta(seconds=1))
    assert account_owner_data(account)["status"] == "ambiguous"
    unmatched = event(account, keyless, switch + timedelta(seconds=2))
    assert event_owner(unmatched, owner_index()) is None
    assert CPAAccountOwnerBinding.objects.filter(account=account).count() == 2


def test_preview_api_derives_owner_from_role(setup):
    config, admin, account, alice, bob, keys, start = setup
    designate(alice, account, timezone.now())
    event(account, CPAAPIKey(key_hash="", hint=""), start + timedelta(minutes=10))
    client = Client()
    headers, _ = jwt_login(client)
    result = client.post(
        f"/api/cpa/unassigned/preview?account_id={account.pk}",
        {"participant_id": bob.pk},
        content_type="application/json",
        **headers,
    )
    assert result.status_code == 201
    assert result.json()["data"]["participant_id"] == alice.pk


def test_ended_key_binding_yields_to_owner_without_reassigning_old_requests(setup):
    config, admin, account, alice, bob, keys, start = setup
    designate(alice, account, start)
    binding = keys[1].bindings.get()
    binding.ended_at = start + timedelta(minutes=15)
    binding.save()
    before = event(account, keys[1], start + timedelta(minutes=14))
    after = event(account, keys[1], start + timedelta(minutes=15))
    assert event_owner(before, owner_index()) == bob.pk
    assert event_owner(after, owner_index()) == alice.pk
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([alice.pk])).values_list(
            "pk", flat=True
        )
    ) == {after.pk}
    assert set(
        CPAUsageEvent.objects.filter(ownership_filter([bob.pk])).values_list(
            "pk", flat=True
        )
    ) == {before.pk}
