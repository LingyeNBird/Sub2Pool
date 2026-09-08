from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from monitor.cpa.quota_status import quota_detail, reset_credits
from monitor.integrations.cpa import CPAError
from monitor.models import (
    CPAAccountCollectionInterval,
    CPAQuotaResetRequest,
    SystemUserPageAccess,
    PagePermission,
)
from monitor.tests.api.test_cpa_participants import setup, event, member_client  # noqa: F401
from monitor.tests.helpers import jwt_login, create_cpa_account

pytestmark = pytest.mark.django_db


def body(start, used=10):
    return {
        "rate_limit": {
            "primary_window": {
                "used_percent": 20,
                "limit_window_seconds": 18000,
                "reset_at": int((start + timedelta(hours=5)).timestamp()),
            },
            "secondary_window": {
                "used_percent": used,
                "limit_window_seconds": 604800,
                "reset_at": int((start + timedelta(days=7)).timestamp()),
            },
        },
        "additional_rate_limits": [
            {
                "limit_name": "Spark",
                "rate_limit": {
                    "secondary_window": {
                        "used_percent": 0,
                        "limit_window_seconds": 604800,
                        "reset_at": int((start + timedelta(days=7)).timestamp()),
                    }
                },
            }
        ],
        "rate_limit_reset_credits": {"available_count": 1},
    }


def test_quota_cards_and_prediction_require_complete_priced_coverage(setup):
    config, admin, account, alice, bob, keys, start = setup
    start = start.replace(microsecond=0)
    event(account, keys[0], start + timedelta(minutes=10))
    detail = quota_detail(account, config, timezone.now(), body(start))
    assert len(detail["windows"]) == 3
    assert detail["windows"][0]["label"] == "周限额"
    assert detail["windows"][0]["prediction"] is None
    assert detail["windows"][0]["current"]["metrics"]["usage_usd"] == 10
    assert detail["windows"][2]["current"] is None
    assert detail["reset"]["available_count"] == 1
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="test", connected_at=start
    )
    detail = quota_detail(account, config, timezone.now(), body(start))
    assert detail["windows"][0]["prediction"] is None
    assert "模型范围" in detail["windows"][0]["notice"]
    standard_body = body(start)
    standard_body.pop("additional_rate_limits")
    detail = quota_detail(account, config, timezone.now(), standard_body)
    assert detail["windows"][0]["prediction"]["usage_usd"] == 100
    event(account, keys[0], start + timedelta(minutes=11), model="unknown")
    detail = quota_detail(account, config, timezone.now(), standard_body)
    assert detail["windows"][0]["prediction"] is None
    assert detail["windows"][0]["current"]["metrics"]["unpriced_request_count"] == 1


@pytest.mark.parametrize("value", [None, -1, "NaN", "Infinity", True, 1.5])
def test_invalid_credit_counts_stay_unknown(value):
    assert (
        reset_credits({"rate_limit_reset_credits": {"available_count": value}}) is None
    )


def test_expired_and_malformed_windows_do_not_invent_statistics(setup):
    config, admin, account, alice, bob, keys, start = setup
    result = quota_detail(
        account, config, timezone.now(), body(start - timedelta(days=8))
    )
    assert all(w["prediction"] is None for w in result["windows"])
    malformed = {
        "rate_limit": {
            "primary_window": {
                "used_percent": "NaN",
                "reset_at": 1e300,
                "limit_window_seconds": -1,
            }
        }
    }
    row = quota_detail(account, config, timezone.now(), malformed)["windows"][0]
    assert (
        row["reset_at"] is None
        and row["used_percent"] is None
        and row["current"] is None
    )


@pytest.fixture
def reset_client(setup, monkeypatch):
    config, admin, account, alice, bob, keys, start = setup
    calls = []

    class FakeClient:
        fail = False

        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def query_usage_payload(self, index):
            return {}, body(start)

        def consume_reset_credit(self, index, request_id):
            calls.append((index, request_id))
            if self.fail:
                raise CPAError("lost response")

    monkeypatch.setattr("monitor.views.cpa_quota_reset.CPAClient", FakeClient)
    return FakeClient, calls


def test_reset_requires_owner_and_confirmation_and_is_idempotent(setup, reset_client):
    config, admin, account, alice, bob, keys, start = setup
    fake, calls = reset_client
    alice.is_owner = True
    alice.save()
    user, client, headers = member_client(account, alice)
    SystemUserPageAccess.objects.create(
        user=user, page_code=PagePermission.ACCOUNT_STATUS
    )
    preview = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    )
    assert preview.status_code == 201, preview.content
    plan = preview.json()["data"]
    url = f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm"
    assert (
        client.post(url, {}, content_type="application/json", **headers).status_code
        == 400
    )
    assert not calls
    assert (
        client.post(
            url, {"confirmed": True}, content_type="application/json", **headers
        ).status_code
        == 200
    )
    assert (
        client.post(
            url, {"confirmed": True}, content_type="application/json", **headers
        ).status_code
        == 200
    )
    assert calls == [(account.cpa_auth_index, plan["id"])]
    peer_user, peer, auth = member_client(account, bob, "peer")
    SystemUserPageAccess.objects.create(
        user=peer_user, page_code=PagePermission.ACCOUNT_STATUS
    )
    assert (
        peer.post(
            f"/api/account-status/cpa/{account.pk}/reset-preview", **auth
        ).status_code
        == 403
    )
    assert peer.post(
        url, {"confirmed": True}, content_type="application/json", **auth
    ).status_code == 403
    other = create_cpa_account("other-reset")
    assert (
        client.post(
            f"/api/account-status/cpa/{other.pk}/reset-preview", **headers
        ).status_code
        == 404
    )


def test_admin_reset_retries_uncertain_result_with_same_id(setup, reset_client):
    config, admin, account, alice, bob, keys, start = setup
    fake, calls = reset_client
    client = Client()
    headers, _ = jwt_login(client)
    plan = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    url = f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm"
    fake.fail = True
    assert (
        client.post(
            url, {"confirmed": True}, content_type="application/json", **headers
        ).status_code
        == 502
    )
    assert CPAQuotaResetRequest.objects.get(pk=plan["id"]).status == "unknown"
    retry = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    assert retry["id"] == plan["id"]
    fake.fail = False
    assert (
        client.post(
            url, {"confirmed": True}, content_type="application/json", **headers
        ).status_code
        == 200
    )
    assert calls[0] == calls[1]


def test_role_revoked_after_preview_cannot_confirm(setup, reset_client):
    config, admin, account, alice, bob, keys, start = setup
    from monitor.models import PagePermission

    alice.is_owner = True
    alice.save()
    user, client, headers = member_client(account, alice)
    SystemUserPageAccess.objects.create(
        user=user, page_code=PagePermission.ACCOUNT_STATUS
    )
    plan = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    alice.is_owner = False
    alice.save()
    url = f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm"
    assert (
        client.post(
            url, {"confirmed": True}, content_type="application/json", **headers
        ).status_code
        == 403
    )
    assert not reset_client[1]


def test_expired_preview_cannot_consume_credit(setup, reset_client):
    account = setup[2]
    client = Client()
    headers, _ = jwt_login(client)
    plan = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    CPAQuotaResetRequest.objects.filter(pk=plan["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    response = client.post(
        f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm",
        {"confirmed": True}, content_type="application/json", **headers,
    )
    assert response.status_code == 400
    assert not reset_client[1]


def test_revoked_account_authorization_cannot_confirm(setup, reset_client):
    _, _, account, alice, *_ = setup
    alice.is_owner = True
    alice.save()
    user, client, headers = member_client(account, alice)
    SystemUserPageAccess.objects.create(user=user, page_code=PagePermission.ACCOUNT_STATUS)
    plan = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    account.authorized_users.remove(user)
    response = client.post(
        f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm",
        {"confirmed": True}, content_type="application/json", **headers,
    )
    assert response.status_code == 404
    assert not reset_client[1]


def test_admin_can_resolve_owner_uncertain_request(setup, reset_client):
    _, _, account, alice, *_ = setup
    alice.is_owner = True
    alice.save()
    user, client, headers = member_client(account, alice)
    SystemUserPageAccess.objects.create(user=user, page_code=PagePermission.ACCOUNT_STATUS)
    plan = client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **headers
    ).json()["data"]
    CPAQuotaResetRequest.objects.filter(pk=plan["id"]).update(status="unknown")
    admin_client = Client()
    admin_headers, _ = jwt_login(admin_client)
    preview = admin_client.post(
        f"/api/account-status/cpa/{account.pk}/reset-preview", **admin_headers
    ).json()["data"]
    assert preview["id"] == plan["id"]
    response = admin_client.post(
        f"/api/account-status/cpa/{account.pk}/resets/{plan['id']}/confirm",
        {"confirmed": True}, content_type="application/json", **admin_headers,
    )
    assert response.status_code == 200
    assert reset_client[1] == [(account.cpa_auth_index, plan["id"])]


def test_redemption_client_uses_verified_endpoint_and_stable_request_id(monkeypatch):
    import json
    from monitor.integrations.cpa import CPAClient
    from monitor.models import AppSettings

    client = CPAClient(AppSettings.load(), management_key="synthetic-key")
    sent = []
    monkeypatch.setattr(
        client,
        "get_codex_account",
        lambda key: {"chatgpt_account_id": "synthetic-account"},
    )

    def request(method, path, **kwargs):
        sent.append((method, path, kwargs["json_body"]))
        return {"status_code": 200}

    monkeypatch.setattr(client, "_request", request)
    try:
        client.consume_reset_credit("auth-test", "stable-redemption-id")
    finally:
        client.client.close()
    method, path, body = sent[0]
    assert method == "POST" and path == "api-call"
    assert (
        body["url"]
        == "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"
    )
    assert json.loads(body["data"]) == {"redeem_request_id": "stable-redemption-id"}
    assert body["header"]["Authorization"] == "Bearer $TOKEN$"
    assert body["auth_index"] == "auth-test"
