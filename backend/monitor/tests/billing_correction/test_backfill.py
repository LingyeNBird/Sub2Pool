"""A missing correction total must be repairable even when old FAST is known."""

import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.billing_correction.observations import interval_corrections
from monitor.fast_correction.repair import calculate_missing_fast_correction
from monitor.integrations.sub2api import Sub2APIError
from monitor.models import AppSettings, BillingUsageFact, Observation, ObservationBillingCapture, ObservationFastCorrection
from monitor.tests.helpers import create_monitored_account, jwt_login
from monitor.tests.billing_correction.test_corrections import captured_observation, log

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin():
    get_user_model().objects.create_superuser("owner", "owner@example.com", "very-strong-password")
    client = Client()
    headers, _ = jwt_login(client)
    return client, headers


def seed_interval(legacy=False):
    account = create_monitored_account()
    start = timezone.now().replace(microsecond=0) - timedelta(hours=4)
    observations = []
    for index in range(1, 4):
        observations.append(Observation.objects.create(
            account_id=account.fact_key, observed_at=start + timedelta(hours=index),
            window_seconds=604800, upstream_resets_at=start + timedelta(days=7),
            attribution_started_at=start, upstream_used_percent=D(10 * index),
            total_standard_cost=D(200 * index), total_actual_cost=D(100 * index),
            raw_selected_total_cost=D(100 * index), selected_total_cost=D(100 * index),
            effective_usd_per_percent=D(10), raw_window={"query_mode": "passive"},
        ))
    previous, target, later = observations
    if legacy:
        target.fast_correction_started_at = previous.observed_at
        target.fast_correction_standard_cost = D(50)
        target.fast_correction_actual_cost = D(25)
        target.fast_correction_request_count = 1
        target.save()
        ObservationFastCorrection.objects.create(
            observation=target, sub2api_user_id=51, request_count=1,
            fast_request_count=1, fast_standard_cost=D(200), fast_actual_cost=D(100),
            standard_correction_cost=D(50), actual_correction_cost=D(25),
        )
    return account, previous, target, later


def install_upstream(monkeypatch, *, rows=None, failure=None):
    calls = []
    class Upstream:
        def __init__(self, _config):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def usage_logs(self, **kwargs):
            calls.append(kwargs)
            if failure is not None:
                raise failure
            return rows if rows is not None else [log(created_at=kwargs["ended_at"] - timedelta(seconds=1))]
    monkeypatch.setattr("monitor.fast_correction.repair.Sub2APIClient", Upstream)
    return calls


def post(admin, target):
    client, headers = admin
    return client.post(f"/api/observations/{target.id}/fast-correction/calculate", **headers)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
@pytest.mark.parametrize("basis,scale", [("actual", 1), ("standard", 2)])
def test_backfill_single_interval_all_three_corrections_and_replay_suffix(monkeypatch, admin, legacy, model, basis, scale):
    config = AppSettings.load()
    config.weekly_quota_model, config.cost_basis = model, basis
    config.save()
    account, previous, target, later = seed_interval(legacy)
    raw_names = ("id", "observed_at", "upstream_resets_at", "upstream_used_percent", "total_actual_cost", "total_standard_cost", "raw_selected_total_cost")
    original = list(Observation.objects.order_by("id").values(*raw_names))
    original_windows = {item.id: dict(item.raw_window) for item in Observation.objects.all()}
    client, headers = admin
    listed = client.get(f"/api/observations?account_id={account.id}", **headers).json()["data"]["items"]
    item = next(row for row in listed if row["id"] == target.id)
    assert item["correction_calculated"] is False
    assert item["correction_total_usd"] is None
    assert item["fast_correction_calculated"] is legacy
    assert item["legacy_fast_only"] is legacy
    if legacy:
        assert item["fast_correction_usd"] == 25 * scale
    calls = install_upstream(monkeypatch)
    response = post(admin, target)
    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["correction_calculated"] and data["correction_facts_complete"]
    assert not data["legacy_fast_only"]
    assert [data[key] for key in ("fast_correction_usd", "long_context_correction_usd", "model_correction_usd", "correction_total_usd")] == [25 * scale, -62.5 * scale, 50 * scale, 12.5 * scale]
    assert len(calls) == 1
    assert calls[0] == dict(account_id=7, started_at=previous.observed_at, ended_at=target.observed_at, timezone_name=config.timezone)
    assert ObservationBillingCapture.objects.count() == 1
    capture = ObservationBillingCapture.objects.get(observation=target)
    assert (capture.started_at, capture.ended_at, capture.request_count) == (previous.observed_at, target.observed_at, 1)
    assert BillingUsageFact.objects.get(capture=capture).model == "gpt-6-astra"
    target.refresh_from_db()
    later.refresh_from_db()
    assert target.selected_total_cost == D("212.5") * scale
    assert later.selected_total_cost == D("312.5") * scale
    assert list(Observation.objects.order_by("id").values(*raw_names)) == original
    # Replay can add derived diagnostics, but preserves every upstream key.
    for item in Observation.objects.all():
        for key, value in original_windows[item.id].items():
            assert item.raw_window[key] == value
    previous.refresh_from_db()
    assert previous.fast_correction_actual_cost is None
    assert later.fast_correction_actual_cost is None
    facts = list(BillingUsageFact.objects.values())
    assert post(admin, target).status_code == 200
    assert len(calls) == 1
    assert list(BillingUsageFact.objects.values()) == facts
    calls.clear()
    changed = client.patch("/api/settings", data=json.dumps({"model_correction_enabled": False}), content_type="application/json", **headers)
    assert changed.status_code == 200, changed.content
    assert not calls
    target.refresh_from_db()
    assert target.selected_total_cost == D("162.5") * scale
    assert list(BillingUsageFact.objects.values()) == facts


@pytest.mark.parametrize("legacy", [False, True])
def test_upstream_failure_preserves_interval_and_allows_retry(monkeypatch, admin, legacy):
    _, _, target, _ = seed_interval(legacy)
    before = list(Observation.objects.values())
    old_fast = list(ObservationFastCorrection.objects.values())
    install_upstream(monkeypatch, failure=Sub2APIError("上游暂时不可用"))
    response = post(admin, target)
    assert response.status_code == 502
    assert list(Observation.objects.values()) == before
    assert list(ObservationFastCorrection.objects.values()) == old_fast
    assert not ObservationBillingCapture.objects.exists()
    calls = install_upstream(monkeypatch)
    assert post(admin, target).status_code == 200
    assert len(calls) == 1


def test_replay_failure_rolls_back_capture_and_legacy_fast(monkeypatch, admin):
    _, _, target, _ = seed_interval(True)
    before = list(Observation.objects.values())
    old_fast = list(ObservationFastCorrection.objects.values())
    install_upstream(monkeypatch)
    def fail(*_args, **_kwargs):
        raise ValueError("合成的重放错误")
    monkeypatch.setattr("monitor.fast_correction.repair.rebuild_observation_suffix", fail)
    assert post(admin, target).status_code == 400
    assert list(Observation.objects.values()) == before
    assert list(ObservationFastCorrection.objects.values()) == old_fast
    assert not BillingUsageFact.objects.exists()
    assert not ObservationBillingCapture.objects.exists()


def test_legacy_log_retention_gap_cannot_erase_old_fast(monkeypatch, admin):
    _, _, target, _ = seed_interval(True)
    install_upstream(monkeypatch, rows=[])
    response = post(admin, target)
    assert response.status_code == 400
    assert "数量" in response.json()["message"]
    target.refresh_from_db()
    assert target.fast_correction_actual_cost == 25
    assert not ObservationBillingCapture.objects.exists()


def test_saved_legacy_interval_start_is_used_even_without_previous_sample(monkeypatch, admin):
    _, previous, target, _ = seed_interval(True)
    start = previous.observed_at
    previous.delete()
    calls = install_upstream(monkeypatch)
    assert post(admin, target).status_code == 200
    assert calls[0]["started_at"] == start


def test_invalid_saved_interval_does_not_query_or_overwrite(monkeypatch, admin):
    _, _, target, _ = seed_interval(True)
    target.fast_correction_started_at = target.observed_at + timedelta(seconds=1)
    target.save()
    calls = install_upstream(monkeypatch)
    assert post(admin, target).status_code == 400
    assert not calls
    assert not ObservationBillingCapture.objects.exists()


@pytest.mark.parametrize("zero_length", [False, True])
def test_empty_capture_becomes_known_zero_and_is_idempotent(monkeypatch, admin, zero_length):
    _, previous, target, _ = seed_interval()
    if zero_length:
        target.observed_at = previous.observed_at
        target.save()
    calls = install_upstream(monkeypatch, rows=[])
    response = post(admin, target)
    assert response.status_code == 200, response.content
    data = response.json()["data"]
    assert data["correction_calculated"] and data["correction_facts_complete"]
    assert data["correction_total_usd"] == 0
    assert ObservationBillingCapture.objects.get(observation=target).request_count == 0
    assert len(calls) == (0 if zero_length else 1)
    assert post(admin, target).status_code == 200
    assert len(calls) == (0 if zero_length else 1)


@pytest.mark.parametrize("flag,expected", [("long_context_correction_enabled", -50), ("model_correction_enabled", 80)])
def test_backfill_works_with_fast_disabled(monkeypatch, admin, flag, expected):
    config = AppSettings.load()
    config.fast_correction_enabled = config.long_context_correction_enabled = config.model_correction_enabled = False
    setattr(config, flag, True)
    config.save()
    _, _, target, _ = seed_interval(True)
    install_upstream(monkeypatch)
    data = post(admin, target).json()["data"]
    assert data["fast_correction_usd"] == 0
    assert data["correction_total_usd"] == expected


def test_all_disabled_does_not_fetch(monkeypatch, admin):
    config = AppSettings.load()
    config.fast_correction_enabled = config.long_context_correction_enabled = config.model_correction_enabled = False
    config.save()
    _, _, target, _ = seed_interval(True)
    calls = install_upstream(monkeypatch)
    assert post(admin, target).status_code == 400
    assert not calls


def test_existing_capture_is_not_overwritten_even_if_old_fast_fields_missing(monkeypatch, admin):
    config = AppSettings.load()
    create_monitored_account()
    observation, _ = captured_observation(config)
    Observation.objects.filter(pk=observation.pk).update(fast_correction_actual_cost=None, fast_correction_standard_cost=None)
    calls = install_upstream(monkeypatch)
    before = list(BillingUsageFact.objects.values())
    assert post(admin, observation).status_code == 200
    assert not calls
    assert list(BillingUsageFact.objects.values()) == before
    BillingUsageFact.objects.all().delete()
    assert post(admin, observation).status_code == 400
    assert not calls
    assert ObservationBillingCapture.objects.count() == 1


def test_retry_on_same_object_ignores_cached_missing_capture(monkeypatch):
    _, _, target, _ = seed_interval(True)
    config = AppSettings.load()
    assert interval_corrections(target, config).facts_complete is False
    calls = install_upstream(monkeypatch)
    assert calculate_missing_fast_correction(target, config)["correction_facts_complete"]
    assert calculate_missing_fast_correction(target, config)["correction_facts_complete"]
    assert len(calls) == 1


def test_readonly_cpa_and_anonymous_cannot_fetch(monkeypatch, admin):
    account, _, target, _ = seed_interval(True)
    calls = install_upstream(monkeypatch)
    get_user_model().objects.create_user("viewer", password="Viewer-Access-2026!secure")
    viewer = Client()
    view_headers, _ = jwt_login(viewer, username="viewer", password="Viewer-Access-2026!secure")
    assert viewer.post(f"/api/observations/{target.id}/fast-correction/calculate", **view_headers).status_code == 403
    assert Client().post(f"/api/observations/{target.id}/fast-correction/calculate").status_code in (401, 403)
    target.account_id = -account.id
    target.save()
    assert post(admin, target).status_code == 400
    assert not calls
