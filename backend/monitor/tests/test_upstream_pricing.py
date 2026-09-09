"""Regression for actual HTTP pricing contracts, ownership and undo."""
from copy import deepcopy
import json
from unittest.mock import patch

import httpx
import pytest
from django.utils import timezone

from monitor.models import AppSettings, MonitoredAccount, QuotaPool, UpstreamPricingState
from monitor.secrets import encrypt_secret
from monitor.upstream_pricing.service import apply_policy, revert_policy, run_automatic_migration


@pytest.mark.django_db
def test_upstream_http_apply_retry_conflict_and_revert():
    config = AppSettings.load()
    config.sub2api_base_url = "http://synthetic.invalid"
    config.sub2api_admin_token_encrypted = encrypt_secret("synthetic-token")
    config.save()
    account = MonitoredAccount.objects.create(
        external_account_id=101, name="synthetic", pool=QuotaPool.for_new_account("synthetic")
    )
    state = UpstreamPricingState.load()
    state.status = "pending"
    state.attempted_at = None
    assert run_automatic_migration() is None
    state.selected_group_ids = [7, 8]
    state.save()
    groups = {group_id: {
        "id": group_id, "name": f"synthetic-{group_id}", "platform": "openai",
        "model_pricing": [], "long_context_pricing_enabled": True,
        "free_openai_fast": False, "rate_multiplier": 0.7,
    } for group_id in (7, 8)}
    original = deepcopy(groups)
    writes = []
    failing = {8}

    def respond(request):
        assert request.headers["x-api-key"] == "synthetic-token"
        path = request.url.path
        if path.endswith("/accounts/101"):
            data = {"id": 101, "platform": "openai", "group_ids": [7, 8]}
        elif path.endswith("/channels/pricing/sync-models"):
            data = {"models": ["gpt-6-astra", "gpt-5.6-sol"]}
        elif path.endswith("/channels/model-pricing"):
            data = {"found": True, "input_price": 0.000005, "output_price": 0.00003,
                    "cache_write_price": 0.00000625, "cache_write_1h_price": 0.00001,
                    "cache_read_price": 0.0000005, "image_input_price": None, "image_output_price": None}
        elif path.endswith("/channels"):
            data = {"items": [], "pages": 1}
        elif "/groups/" in path:
            group_id = int(path.rsplit("/", 1)[1])
            if request.method == "PUT":
                fields = json.loads(request.content)
                assert set(fields) == {"model_pricing", "long_context_pricing_enabled", "free_openai_fast"}
                writes.append((group_id, fields))
                if group_id in failing:
                    return httpx.Response(503, json={"code": 503, "message": "synthetic outage"})
                groups[group_id].update(fields)
            data = groups[group_id]
        else:
            raise AssertionError(f"Unexpected upstream route: {request.method} {path}")
        return httpx.Response(200, json={"code": 0, "data": data})

    client_class = httpx.Client
    with patch("monitor.integrations.sub2api.transport.httpx.Client", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs)):
        state = run_automatic_migration()
        assert state.status == "partial"
        account.refresh_from_db()
        assert not account.upstream_pricing_applied
        first_epoch = account.pricing_epoch
        count = len(writes)
        assert run_automatic_migration() is None
        assert len(writes) == count
        with pytest.raises(ValueError, match="至少选择"):
            apply_policy(None, group_ids=[], announcement=True)
        assert UpstreamPricingState.load().announcement_applied_at is None
        # Accepted partial failures consume the global announcement action,
        # without disabling ordinary settings retries or undo.
        state = apply_policy({"model_rules": []}, group_ids=[7, 8], announcement=True)
        assert state.status == "partial"
        assert state.announcement_applied_at is not None
        consumed_at = state.announcement_applied_at
        count = len(writes)
        with pytest.raises(ValueError, match="公告一键应用已确认过"):
            apply_policy(None, group_ids=[7], announcement=True)
        assert len(writes) == count
        failing.clear()
        state = apply_policy(state.policy, group_ids=[7, 8])
        assert state.status == "applied"
        account.refresh_from_db()
        assert account.upstream_pricing_applied and account.pricing_epoch != first_epoch
        card = next(row for row in groups[7]["model_pricing"] if row["models"] == ["gpt-6-astra"])
        assert card["input_price"] == 0.000009
        assert card["cache_write_1h_price"] == 0.000018
        assert card["fast_multiplier"] == 2
        other = next(row for row in groups[7]["model_pricing"] if row["models"] == ["gpt-5.6-sol"])
        assert other["fast_multiplier"] == 2.5
        count = len(writes)
        unchanged_epoch = account.pricing_epoch
        state = apply_policy(state.policy, group_ids=[7, 8])
        assert state.status == "applied" and len(writes) == count
        account.refresh_from_db()
        assert account.pricing_epoch == unchanged_epoch
        # A later failed edit may leave the previous owned value in place.
        # Undo must recognize that value, not mistake it for an external edit.
        failing.add(8)
        changed_policy = deepcopy(state.policy)
        changed_policy["fast_rules"][0]["multiplier"] = "3"
        state = apply_policy(changed_policy, group_ids=[7, 8])
        assert state.status == "partial"
        failing.clear()
        groups[7]["model_pricing"][0]["fast_multiplier"] = 9
        state = revert_policy(state.revision)
        assert state.status == "partial"
        assert groups[8] == original[8]
        assert groups[7]["model_pricing"][0]["fast_multiplier"] == 9
        journal = next(row for row in state.targets if row["group_id"] == 7)
        groups[7].update(deepcopy(journal["after"]))
        state = revert_policy(state.revision)
        assert state.status == "reverted"
        assert groups == original
        assert UpstreamPricingState.load().announcement_applied_at == consumed_at
        with pytest.raises(ValueError, match="公告一键应用已确认过"):
            apply_policy(None, group_ids=[7], announcement=True)
        count = len(writes)
        assert run_automatic_migration() is None
        assert len(writes) == count
        account.refresh_from_db()
        assert not account.upstream_pricing_applied
        # A completed undo relinquishes ownership; later operator changes are
        # the baseline of a new explicit application, including wildcard cards.
        groups[7]["model_pricing"] = [{
            "platform": "openai", "billing_mode": "token", "models": ["*"],
            "input_price": 0.000007, "fast_multiplier": 2,
        }]
        operator_values = deepcopy(groups)
        state = apply_policy({
            "fast_rules": [{"model_pattern": "*", "multiplier": "2.5"}],
            "model_rules": [], "long_context_pricing_enabled": None,
        }, group_ids=[7, 8])
        assert state.status == "applied"
        assert groups[7]["model_pricing"][0]["models"] == ["*"]
        assert groups[7]["model_pricing"][0]["input_price"] == 0.000007
        assert groups[7]["model_pricing"][0]["fast_multiplier"] == 2.5
        state = revert_policy(state.revision)
        assert groups == operator_values
        config.sub2api_base_url = "http://another-synthetic.invalid"
        config.save()
        state = apply_policy(state.policy, group_ids=[7, 8])
        assert state.status == "applied"
        assert state.base_url == "http://another-synthetic.invalid"
        # Account membership must never expand a manually selected write set.
        changed = deepcopy(state.policy)
        changed["fast_rules"][0]["multiplier"] = "3"
        count = len(writes)
        state = apply_policy(changed, group_ids=[7])
        assert state.status == "applied"
        assert [group_id for group_id, _ in writes[count:]] == [7]
        assert UpstreamPricingState.load().selected_group_ids == [7]
        account.refresh_from_db()
        assert not account.upstream_pricing_applied
        count = len(writes)
        state = apply_policy(changed, group_ids=[8])
        assert [group_id for group_id, _ in writes[count:]] == [8]
        assert {row["group_id"] for row in state.targets} == {7, 8}
        with pytest.raises(ValueError, match="至少选择"):
            apply_policy(changed, group_ids=[])
        state = revert_policy(state.revision)
        assert groups == operator_values
        # A manually chosen group need not belong to any monitored account.
        groups[9] = {**deepcopy(original[8]), "id": 9, "name": "unassociated"}
        count = len(writes)
        state = apply_policy(changed, group_ids=[9])
        assert state.status == "applied"
        assert [group_id for group_id, _ in writes[count:]] == [9]
        assert {key: groups[key] for key in (7, 8)} == operator_values


@pytest.mark.django_db
def test_pricing_epoch_replay_separates_raw_cost_baselines():
    from datetime import timedelta
    from decimal import Decimal
    from monitor.models import Observation
    from monitor.replay import rebuild_account
    from monitor.tests.billing_correction.test_corrections import captured_observation, log
    from monitor.tests.helpers import create_monitored_account

    config = AppSettings.load()
    create_monitored_account()
    at = timezone.now().replace(microsecond=0) - timedelta(hours=3)
    old, _ = captured_observation(config, at=at, logs=[
        log(created_at=at-timedelta(seconds=1), total_cost=Decimal("100"),
            long_context_billing_applied=False)
    ])
    new_rows = []
    for offset, actual, used in ((1, 200, 20), (2, 260, 25)):
        new_rows.append(Observation.objects.create(
            account_id=7, observed_at=at+timedelta(hours=offset),
            upstream_resets_at=old.upstream_resets_at,
            upstream_used_percent=used, total_actual_cost=actual,
            total_standard_cost=actual*2, raw_selected_total_cost=actual,
            selected_total_cost=actual, effective_usd_per_percent=20,
            correction_source="upstream", pricing_epoch="upstream-v1:1:applied",
        ))
    rebuild_account(7, config)
    old.refresh_from_db()
    for row in new_rows:
        row.refresh_from_db()
    assert old.selected_total_cost == Decimal("225")
    assert new_rows[0].attribution_started_at == new_rows[0].observed_at
    assert new_rows[1].attribution_started_at == new_rows[0].observed_at
    assert new_rows[0].selected_total_cost == 0
    assert new_rows[1].selected_total_cost == 60
    assert all(not row.is_manual_start for row in new_rows)


@pytest.mark.django_db
def test_current_pricing_uses_live_prices_and_keeps_categories_separate():
    from monitor.integrations.sub2api import Sub2APIClient
    from monitor.upstream_pricing.inspection import current_pricing

    config = AppSettings.load()
    config.sub2api_base_url = "http://synthetic.invalid"
    config.sub2api_admin_token_encrypted = encrypt_secret("synthetic-token")
    config.save()
    state = UpstreamPricingState.load()
    state.policy["model_rules"][0]["multiplier"] = "9"
    state.save()
    base = {"input_price": 0.000005, "output_price": 0.00003,
            "cache_write_price": 0.00000625, "cache_write_1h_price": 0.00001,
            "cache_read_price": 0.0000005}
    card = {key: value * 1.8 for key, value in base.items()}
    card.update(models=["gpt-6-astra"], platform="openai", fast_multiplier=2.5)
    group = {"id": 7, "name": "synthetic", "platform": "openai",
             "model_pricing": [card], "free_openai_fast": False,
             "long_context_pricing_enabled": False}

    def respond(request):
        assert request.method == "GET", "查看当前不应写入上游"
        if request.url.path.endswith("/groups/7"):
            data = group
        elif request.url.path.endswith("/channels"):
            data = {"items": [], "pages": 1}
        elif request.url.path.endswith("/channels/model-pricing"):
            data = {"found": True, **base}
        else:
            raise AssertionError(request.url)
        return httpx.Response(200, json={"code": 0, "data": data})

    client_class = httpx.Client
    with patch("monitor.integrations.sub2api.transport.httpx.Client", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs)):
        with Sub2APIClient(config) as client:
            fast = current_pricing(client, state, config.sub2api_base_url, 7, "fast")
            assert fast["rows"] == [{"models": ["gpt-6-astra"], "multiplier": 2.5}]
            assert "enabled" not in fast and "prices" not in fast["rows"][0]
            context = current_pricing(client, state, config.sub2api_base_url, 7, "context")
            assert context["enabled"] is False and "rows" not in context
            model = current_pricing(client, state, config.sub2api_base_url, 7, "model")
            assert abs(float(model["rows"][0]["multiplier"]) - 1.8) < 0.000001
            assert "free_fast" not in model
            card["output_price"] = 0.00006
            changed = current_pricing(client, state, config.sub2api_base_url, 7, "model")
            assert changed["rows"][0]["multiplier"] is None
            assert changed["rows"][0]["ratios"]["output_price"] == "2"


@pytest.mark.django_db
def test_model_inspection_does_not_fetch_catalog_for_fast_only_cards():
    from monitor.integrations.sub2api import Sub2APIClient
    from monitor.upstream_pricing.inspection import current_pricing

    config = AppSettings.load()
    config.sub2api_base_url = "http://synthetic.invalid"
    config.sub2api_admin_token_encrypted = encrypt_secret("synthetic-token")
    state = UpstreamPricingState.load()
    requests = []

    def respond(request):
        assert request.method == "GET"
        requests.append(request.url.path)
        if request.url.path.endswith("/groups/7"):
            data = {"id": 7, "platform": "openai", "model_pricing": [
                {"models": [f"model-{index}" for index in range(200)], "fast_multiplier": 2.5},
            ]}
        elif request.url.path.endswith("/channels"):
            data = {"items": [], "pages": 1}
        else:
            raise AssertionError("未改价模型不应查询目录价格")
        return httpx.Response(200, json={"code": 0, "data": data})

    client_class = httpx.Client
    with patch("monitor.integrations.sub2api.transport.httpx.Client", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs)):
        with Sub2APIClient(config) as client:
            result = current_pricing(client, state, config.sub2api_base_url, 7, "model")
    assert len(result["rows"]) == 200
    assert all(row["multiplier"] == "1" and not row["warning"] for row in result["rows"])
    assert len(requests) == 2


@pytest.mark.django_db
def test_model_inspection_fetches_unique_references_with_bounded_concurrency():
    from threading import Barrier, Lock
    from monitor.integrations.sub2api import Sub2APIClient
    from monitor.upstream_pricing.inspection import current_pricing

    config = AppSettings.load()
    config.sub2api_base_url = "http://synthetic.invalid"
    config.sub2api_admin_token_encrypted = encrypt_secret("synthetic-token")
    state = UpstreamPricingState.load()
    pair = Barrier(2)
    lock = Lock()
    active = peak = 0
    requested = []

    def respond(request):
        nonlocal active, peak
        assert request.method == "GET"
        if request.url.path.endswith("/groups/7"):
            data = {"id": 7, "platform": "openai", "model_pricing": [
                {"models": [f"model-{index}" for index in range(16)] + ["model-0"],
                 "input_price": 0.00001, "output_price": 0.00006},
            ]}
        elif request.url.path.endswith("/channels"):
            data = {"items": [], "pages": 1}
        elif request.url.path.endswith("/channels/model-pricing"):
            with lock:
                active += 1
                peak = max(peak, active)
                requested.append(request.url.params["model"])
            try:
                pair.wait(timeout=5)
                data = {"found": True, "input_price": 0.000005, "output_price": 0.00003}
            finally:
                with lock:
                    active -= 1
        else:
            raise AssertionError(request.url)
        return httpx.Response(200, json={"code": 0, "data": data})

    client_class = httpx.Client
    with patch("monitor.integrations.sub2api.transport.httpx.Client", side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs)):
        with Sub2APIClient(config) as client:
            result = current_pricing(client, state, config.sub2api_base_url, 7, "model")
    assert all(row["multiplier"] == "2" for row in result["rows"])
    assert len(requested) == len(set(requested)) == 16
    assert 2 <= peak <= 8
