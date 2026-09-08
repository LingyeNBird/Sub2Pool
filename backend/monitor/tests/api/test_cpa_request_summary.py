from datetime import timedelta

import pytest

from monitor.api_auth import hash_api_key
from monitor.models import SystemUserAPIKey
from monitor.tests.api.test_cpa_participants import event, member_client
from monitor.tests.api.test_cpa_participants import setup as participant_setup

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    return participant_setup.__wrapped__()


def test_request_summary_full_scope_price_rules_and_pagination(setup):
    config, _admin, account, alice, _bob, keys, start = setup
    config.cpa_fast_multiplier = 3
    config.cpa_double_billing_enabled = True
    config.cpa_double_billing_threshold_tokens = 500_000
    config.cpa_double_billing_multiplier = 2
    config.save()
    first = event(account, keys[0], start)
    first.cached_input_tokens = 100_000
    first.output_tokens = 100_000
    first.reasoning_tokens = 20_000
    first.total_tokens = 1_100_000
    first.requested_service_tier = "priority"
    first.response_service_tier = "auto"
    first.latency_ms = 10_000
    first.ttft_ms = 1000
    first.save()
    event(account, keys[0], start + timedelta(minutes=1), model="unknown", failed=True)
    keys[0].bindings.update(started_at=start - timedelta(days=9))
    event(account, keys[0], start - timedelta(days=8))
    event(account, keys[1], start, tokens=99_000_000)
    _user, client, headers = member_client(account, alice)
    path = f"/api/cpa/requests?account_id={account.id}&include_summary=true&page_size=1"
    result = client.get(path, **headers).json()["data"]
    assert result["total"] == 2
    assert len(result["items"]) == 1
    summary = result["summary"]
    assert summary["request_count"] == 2
    assert summary["failed_count"] == 1
    assert summary["total_tokens"] == 2_100_000
    assert summary["cached_input_tokens"] == 100_000
    assert summary["reasoning_tokens"] == 20_000
    assert summary["unpriced_request_count"] == 1
    assert summary["usage_usd"] == pytest.approx((9 + 0.1 + 3) * 3 * 2)
    assert summary["average_latency_ms"] == 10_000
    assert summary["average_ttft_ms"] == 1000
    assert client.get(path + "&page=2", **headers).json()["data"]["summary"] == summary
    failed = client.get(path + "&failed=true", **headers).json()["data"]["summary"]
    assert failed["request_count"] == 1
    assert failed["average_latency_ms"] is None
    assert (
        client.get(path + "&model=does-not-exist", **headers).json()["data"]["summary"][
            "request_count"
        ]
        == 0
    )
    assert client.get(path + "&days=30", **headers).json()["data"]["total"] == 3
    assert client.get(path + "&days=91", **headers).status_code == 400


def test_summary_readonly_key_is_scoped_and_opt_in(setup):
    _config, _admin, account, alice, bob, keys, start = setup
    event(account, keys[0], start)
    event(account, keys[1], start, tokens=99_000_000)
    user, client, _headers = member_client(account, alice)
    SystemUserAPIKey.objects.create(
        user=user, key_hash=hash_api_key("summary-test-key"), hint="test"
    )
    headers = {"HTTP_AUTHORIZATION": "Bearer summary-test-key"}
    path = f"/api/v1/cpa/requests?account_id={account.id}"
    assert client.get(path, **headers).json()["data"]["summary"] is None
    response = client.get(path + "&include_summary=true", **headers)
    assert response.status_code == 200
    assert response.json()["data"]["summary"]["input_tokens"] == 1_000_000
    assert (
        client.get(
            path + f"&include_summary=true&key_id={keys[1].id}", **headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            path + f"&include_summary=true&participant_id={bob.id}", **headers
        ).status_code
        == 400
    )
