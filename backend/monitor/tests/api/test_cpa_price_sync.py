from datetime import timedelta

import httpx
import pytest
from django.test import Client

from monitor.cpa.price_sync import (
    PriceCatalogError,
    catalog_price,
    fetch_catalog,
    sync_missing_prices,
)
from monitor.models import AppSettings, CPAUsageEvent, ParticipantSnapshot
from monitor.replay import rebuild_account
from monitor.tests.api.test_cpa_participants import (
    event,
    member_client,
    observation,
)
from monitor.tests.api.test_cpa_participants import (
    setup as participant_setup,
)
from monitor.tests.helpers import create_cpa_account, jwt_login

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    return participant_setup.__wrapped__()


def catalog():
    return {
        "new-model": {"cost": {"input": 10, "cache_read": 1, "output": 50}},
        "gpt-test": {"cost": {"input": 99, "cache_read": 99, "output": 99}},
    }


def test_sync_missing_only_and_replay_existing_owned_events(setup, monkeypatch):
    config, _admin, account, alice, _bob, keys, start = setup
    observation(account, start, start, 0, 0)
    raw = event(account, keys[0], start + timedelta(minutes=10), model="new-model")
    last = observation(account, start, start + timedelta(hours=1), 10, 0)
    event(account, keys[1], start + timedelta(minutes=10), model="unknown-alias")
    rebuild_account(account.fact_key, config)
    original = dict(CPAUsageEvent.objects.filter(pk=raw.pk).values().get())
    monkeypatch.setattr("monitor.cpa.price_sync.fetch_catalog", catalog)
    client = Client()
    headers, _ = jwt_login(client)
    inventory = client.get(
        f"/api/settings/cpa-pricing?account_id={account.id}", **headers
    ).json()["data"]
    assert inventory["missing_model_count"] == 2
    response = client.post(
        f"/api/settings/cpa-pricing?account_id={account.id}", **headers
    )
    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert [row["model"] for row in data["added"]] == ["new-model"]
    assert data["missing_model_count"] == 1
    assert data["unresolved"][0]["model"] == "unknown-alias"
    config.refresh_from_db()
    assert config.cpa_model_pricing["gpt-test"]["input"] == "10"
    assert config.cpa_model_pricing["new-model"] == {
        "input": "10",
        "cached_input": "1",
        "output": "50",
    }
    assert (
        ParticipantSnapshot.objects.get(
            observation=last, participant=alice
        ).raw_selected_cost
        == 10
    )
    alice.refresh_from_db()
    assert alice.latest_balance_usd == 123
    assert CPAUsageEvent.objects.filter(pk=raw.pk).values().get() == original
    assert not client.post(
        f"/api/settings/cpa-pricing?account_id={account.id}", **headers
    ).json()["data"]["added"]


def test_sync_respects_account_scope_and_manual_edit_while_fetching(setup, monkeypatch):
    _config, _admin, account, _alice, _bob, keys, start = setup
    hidden = create_cpa_account("other")
    event(account, keys[0], start, model="new-model")
    event(hidden, keys[0], start, model="other-model")

    def fetch_after_manual_save():
        latest = AppSettings.load()
        latest.cpa_model_pricing["new-model"] = {
            "input": "7",
            "cached_input": "0.7",
            "output": "8",
        }
        latest.save()
        return {**catalog(), "other-model": catalog()["new-model"]}

    monkeypatch.setattr("monitor.cpa.price_sync.fetch_catalog", fetch_after_manual_save)
    result = sync_missing_prices(account.id)
    assert not result["added"]
    assert result["pricing"]["new-model"]["input"] == "7"
    assert "other-model" not in result["pricing"]


def test_pricing_admin_only_and_failures_do_not_change_settings(setup, monkeypatch):
    config, _admin, account, alice, _bob, keys, start = setup
    event(account, keys[0], start, model="new-model")
    _user, client, headers = member_client(account, alice)
    for method in (client.get, client.post):
        assert method("/api/settings/cpa-pricing", **headers).status_code == 403
    headers, _ = jwt_login(client)
    before = dict(config.cpa_model_pricing)

    def fail():
        raise PriceCatalogError("同步失败")

    monkeypatch.setattr("monitor.cpa.price_sync.fetch_catalog", fail)
    assert client.post("/api/settings/cpa-pricing", **headers).status_code == 502
    config.refresh_from_db()
    assert config.cpa_model_pricing == before
    monkeypatch.setattr("monitor.cpa.price_sync.fetch_catalog", catalog)

    def fail_replay(*args, **kwargs):
        raise ValueError("bad replay")

    monkeypatch.setattr("monitor.cpa.price_sync.rebuild_account", fail_replay)
    assert client.post("/api/settings/cpa-pricing", **headers).status_code == 409
    config.refresh_from_db()
    assert config.cpa_model_pricing == before


@pytest.mark.parametrize("invalid", [None, -1, "NaN", "Infinity", True])
def test_catalog_rejects_incomplete_invalid_prices(invalid):
    models = {"new-model": {"cost": {"input": 10, "cache_read": invalid, "output": 50}}}
    assert catalog_price(models, "new-model") is None
    assert catalog_price(catalog(), "my-new-model") is None
    assert catalog_price(catalog(), "new-model-2026-09-08") is None
    assert catalog_price(catalog(), "new-model-latest")[0] == "new-model"
    assert catalog_price(catalog(), "openai/new-model")[1]["cached_input"] == "1"


def test_no_missing_prices_does_not_fetch_catalog(setup, monkeypatch):
    _config, _admin, account, _alice, _bob, keys, start = setup
    event(account, keys[0], start)

    def fail():
        raise AssertionError("unnecessary network request")

    monkeypatch.setattr("monitor.cpa.price_sync.fetch_catalog", fail)
    assert sync_missing_prices()["added"] == []


def test_catalog_fetch_validates_shape_and_does_not_send_usage(monkeypatch):
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"openai": {"models": catalog()}, "other-provider": {"models": {}}},
        )

    client = httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr("monitor.cpa.price_sync.httpx.stream", client.stream)
    assert fetch_catalog() == catalog()
    assert str(calls[0].url) == "https://models.dev/api.json"
    assert calls[0].content == b""
    client.close()
