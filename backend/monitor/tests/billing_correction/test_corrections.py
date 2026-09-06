"""Policies are projections over raw evidence, not modifications of upstream cost."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.billing_correction.domain import BillingCorrectionRules
from monitor.billing_correction.facts import validate_interval_logs
from monitor.billing_correction.observations import interval_corrections
from monitor.billing_correction.persistence import persist_capture
from monitor.billing_correction.rules import (
    normalize_long_context_correction_rules, normalize_model_correction_rules,
)
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.persistence import apply_fast_interval
from monitor.fast_correction.prefix import FastCorrectionPrefix
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.historical_rebuild.contracts import config_digest, source_fact_digest
from monitor.integrations.sub2api import Sub2APIUsageLog
from monitor.models import (
    AppSettings, BillingUsageFact, Observation, ObservationBillingCapture,
    APIUsageRequestFact, ParticipantAPIUsageSnapshot,
)
from monitor.replay import rebuild_account
from monitor.reporting.costs import FastCorrectionBreakdownPresenter
from monitor.tests.helpers import create_monitored_account, create_participant, jwt_login

D = Decimal


def log(**kwargs):
    values = dict(id=1, user_id=51, account_id=7, created_at=timezone.now(),
                  service_tier="priority", model="gpt-6-astra",
                  total_cost=D("200"), actual_cost=D("100"), api_key_id=3,
                  api_key_name="public name", input_tokens=272001,
                  cache_creation_tokens=0, cache_read_tokens=0,
                  long_context_billing_applied=True)
    values.update(kwargs)
    return Sub2APIUsageLog(**values)


@pytest.mark.parametrize("basis,expected", [("actual", (25, -62.5, 50, 112.5)), ("standard", (50, -125, 100, 225))])
def test_three_stages_telescope_with_signed_long_context_reduction(basis, expected):
    result = BillingCorrectionRules(AppSettings()).calculate(log(), basis)
    assert (result.amounts.fast, result.amounts.long_context, result.amounts.model, result.corrected_cost) == tuple(D(str(v)) for v in expected)
    assert result.raw_cost + result.amounts.total == result.corrected_cost


@pytest.mark.parametrize("tier", ["priority", "fast", " FAST "])
def test_only_fast_tiers_receive_fast_correction(tier):
    result = BillingCorrectionRules(AppSettings()).calculate(log(service_tier=tier), "actual")
    assert result.amounts.fast == 25


@pytest.mark.parametrize("model,tier,expected", [
    ("gpt-5.6", "default", "50"), ("GPT-5.6-codex", "default", "50"),
    ("gpt-6", "default", "90"), ("gpt-6-astra", "default", "90"),
    ("gpt-5.4", "default", "100"), ("claude-opus", "default", "100"),
])
def test_default_model_matching_does_not_require_fast(model, tier, expected):
    assert BillingCorrectionRules(AppSettings()).calculate(log(model=model, service_tier=tier), "actual").corrected_cost == D(expected)


@pytest.mark.parametrize("flag,tokens,cache,expected,unknown", [
    (False, 900000, 0, "1", False),  # Explicit false outranks token inference.
    (True, 1, 0, "0.5", False),
    (None, 272000, 0, "1", False),  # Strict > boundary, no output tokens.
    (None, 271999, 2, "0.5", False),
    (None, 100, 0, "1", False),
    (None, None, None, "1", True),
    (None, 300000, None, "0.5", False),  # Known nonnegative subtotal suffices.
    (None, 100, None, "1", True),
])
def test_long_context_fact_priority_and_threshold(flag, tokens, cache, expected, unknown):
    result = BillingCorrectionRules(AppSettings()).calculate(log(
        long_context_billing_applied=flag, input_tokens=tokens, cache_read_tokens=cache,
    ), "actual")
    assert result.long_context_factor == D(expected)
    assert result.long_context_unknown is unknown


def test_first_match_wins_and_regex_metacharacters_are_literal():
    config = AppSettings(model_correction_rules=[
        {"model_pattern": "gpt-6-astra", "multiplier": "1"},
        {"model_pattern": "gpt-6*", "multiplier": "1.8"},
    ], long_context_correction_rules=[
        {"model_pattern": "gpt-6[.]", "source_multiplier": "2", "target_multiplier": "1"},
    ])
    result = BillingCorrectionRules(config).calculate(log(), "actual")
    assert result.model_factor == result.long_context_factor == 1
    config.model_correction_rules.reverse()
    assert BillingCorrectionRules(config).calculate(log(), "actual").model_factor == D("1.8")


@pytest.mark.parametrize("value", [None, {}, [None], [{"model_pattern": ""}], [{"model_pattern": "x" * 161, "multiplier": 1}], [{"model_pattern": "x", "multiplier": n} for n in range(101)]])
def test_rejects_invalid_rules(value):
    with pytest.raises(ValueError):
        normalize_model_correction_rules(value)


@pytest.mark.parametrize("value", [0, -1, 101, "NaN", "Infinity", "bad", True])
def test_rejects_invalid_multipliers(value):
    with pytest.raises(ValueError):
        normalize_model_correction_rules([{"model_pattern": "*", "multiplier": value}])


@pytest.mark.parametrize("threshold", [0, -1, True, 2.5, "272000", 100000001])
def test_rejects_invalid_thresholds(threshold):
    with pytest.raises(ValueError):
        normalize_long_context_correction_rules([{"model_pattern": "*", "source_multiplier": 2, "target_multiplier": 1, "threshold_tokens": threshold}])


def test_zero_identity_and_submicro_precision():
    config = AppSettings(fast_correction_enabled=False, long_context_correction_enabled=False, model_correction_enabled=False)
    for raw in ("0", "0.0000001", "123.123456789"):
        result = BillingCorrectionRules(config).calculate(log(actual_cost=D(raw)), "actual")
        assert result.corrected_cost == D(raw)
        assert result.amounts.total == 0
    config.long_context_correction_rules = [{"model_pattern": "*", "source_multiplier": 1, "target_multiplier": 1}]
    config.long_context_correction_enabled = True
    result = BillingCorrectionRules(config).calculate(log(input_tokens=None, cache_read_tokens=None, cache_creation_tokens=None, long_context_billing_applied=None), "actual")
    assert not result.long_context_unknown
    assert result.amounts.total == 0


def captured_observation(config, *, at=None, started=None, logs=None):
    at = at or timezone.now().replace(microsecond=0)
    started = started or at - timedelta(days=1)
    logs = logs if logs is not None else [log(created_at=at - timedelta(seconds=1))]
    observation = Observation.objects.create(
        account_id=7, observed_at=at, window_seconds=604800,
        upstream_resets_at=started + timedelta(days=7), attribution_started_at=started,
        upstream_used_percent=D("10"), interval_used_percent=D("10"),
        total_actual_cost=D("100"), total_standard_cost=D("200"),
        selected_total_cost=D("100"), raw_selected_total_cost=D("100"),
        effective_usd_per_percent=D("10"),
    )
    interval = aggregate_fast_logs(logs, started_at=started, ended_at=at, rules=FastCorrectionRuleSet(config.fast_correction_rules))
    apply_fast_interval(observation, interval)
    observation.save()
    return observation, interval


@pytest.mark.django_db
def test_primary_facts_unchanged_by_local_repricing_and_empty_capture_is_known():
    config = AppSettings.load()
    create_monitored_account()
    observation, interval = captured_observation(config)
    original = list(BillingUsageFact.objects.values())
    source_digest = source_fact_digest(7)
    assert interval_corrections(observation, config).amounts.total == D("12.5")
    config.model_correction_rules = [{"model_pattern": "*", "multiplier": "1"}]
    result = interval_corrections(observation, config, include_models=True)
    assert result.amounts.total == D("-37.5")
    assert result.model_details[0]["corrected_cost_usd"] == 62.5
    prefix = FastCorrectionPrefix(7, "actual", config)
    assert prefix.total_between(interval.started_at, observation) == D("-37.5")
    assert prefix.user_between(51, interval.started_at, interval.ended_at) == D("-37.5")
    assert list(BillingUsageFact.objects.values()) == original
    assert source_fact_digest(7) == source_digest
    with pytest.raises(ValueError, match="禁止覆盖"):
        persist_capture(observation, interval)
    empty, _ = captured_observation(config, at=observation.observed_at + timedelta(hours=1), logs=[])
    assert interval_corrections(empty, config).facts_complete
    assert interval_corrections(empty, config).amounts.total == 0
    assert empty.billing_capture.request_count == 0


@pytest.mark.django_db
@pytest.mark.parametrize("model", ["constant_average", "time_varying"])
def test_setting_patch_replays_history_atomically_without_upstream(monkeypatch, model):
    config = AppSettings.load()
    config.weekly_quota_model = model
    config.save()
    account = create_monitored_account()
    observation, _ = captured_observation(config)
    rebuild_account(7, config)
    observation.refresh_from_db()
    assert observation.selected_total_cost == D("112.5")
    source = list(BillingUsageFact.objects.values())
    captures = list(ObservationBillingCapture.objects.values())
    before_config_digest = config_digest(config, account)
    get_user_model().objects.create_superuser("owner", "owner@example.com", "very-strong-password")
    client = Client()
    headers, _ = jwt_login(client)
    def offline(*args, **kwargs):
        raise AssertionError("Changing policy must NEVER create an upstream client")
    monkeypatch.setattr("monitor.integrations.sub2api.Sub2APIClient.__init__", offline)
    response = client.patch("/api/settings", data=json.dumps({"model_correction_enabled": False}), content_type="application/json", **headers)
    assert response.status_code == 200, response.content
    observation.refresh_from_db()
    assert observation.selected_total_cost == D("62.5")
    assert observation.raw_selected_total_cost == D("100")
    config.refresh_from_db()
    assert list(BillingUsageFact.objects.values()) == source
    assert list(ObservationBillingCapture.objects.values()) == captures
    assert config_digest(config, account) != before_config_digest
    breakdown = FastCorrectionBreakdownPresenter(config, 7).for_observation(observation)
    assert breakdown["sub2api_cost_usd"] == 100
    assert breakdown["correction_total_usd"] == -37.5
    assert breakdown["total_cost_usd"] == 62.5
    # Corrupt evidence must roll back configuration rather than reprice partial data.
    BillingUsageFact.objects.all().delete()
    failed = client.patch("/api/settings", data=json.dumps({"model_correction_enabled": True}), content_type="application/json", **headers)
    assert failed.status_code == 409
    config.refresh_from_db()
    assert config.model_correction_enabled is False
    observation.refresh_from_db()
    assert observation.selected_total_cost == D("62.5")


@pytest.mark.django_db
def test_legacy_summaries_remain_explicitly_frozen_and_cpa_unaffected():
    config = AppSettings.load()
    config.fast_correction_enabled = False
    observation, _ = captured_observation(config)
    ObservationBillingCapture.objects.all().delete()
    observation = Observation.objects.get(pk=observation.pk)
    result = interval_corrections(observation, config)
    assert result.legacy_fast_only and not result.facts_complete
    assert result.amounts.fast == 25
    assert result.amounts.long_context == result.amounts.model == 0
    observation.account_id = -1
    assert not interval_corrections(observation, config).calculated
    assert interval_corrections(observation, config).amounts.total == 0


@pytest.mark.django_db
def test_api_key_repricing_uses_deduplicated_facts_and_no_credentials(monkeypatch):
    from monitor.api_usage import refresh_participant_api_usage, fresh_snapshot
    config = AppSettings.load()
    create_monitored_account()
    participant = create_participant(name="rider", sub2api_user_id=51, share_percent=100)
    observation, _ = captured_observation(config)
    now = observation.observed_at + timedelta(minutes=1)
    requests = [log(created_at=observation.observed_at - timedelta(minutes=1))]
    class Upstream:
        def list_user_api_keys(self, user):
            return [{"id": 3, "name": "public name", "status": "active", "key": "SECRET-MUST-NOT-PERSIST"}]
        def usage_logs(self, **kwargs):
            return requests
    for at in [now, now + timedelta(minutes=1)]:
        snapshot = refresh_participant_api_usage(client=Upstream(), participant=participant, observation=observation, config=config, observed_to=at)
        assert snapshot.participant_total_usd == D("112.5")
    assert APIUsageRequestFact.objects.count() == 1
    assert ParticipantAPIUsageSnapshot.objects.count() == 2
    saved = ParticipantAPIUsageSnapshot.objects.get(pk=snapshot.pk)
    assert "SECRET" not in str(saved.raw_api_keys)
    assert "model_correction_usd" not in str(saved.api_keys)
    raw = list(APIUsageRequestFact.objects.values())
    config.model_correction_enabled = False
    cached = fresh_snapshot(participant=participant, observation=observation, config=config, now=now + timedelta(minutes=2))
    assert cached.participant_total_usd == D("62.5")
    assert cached.api_keys[0]["correction_total_usd"] == -37.5
    assert list(APIUsageRequestFact.objects.values()) == raw
    config.cost_basis = "standard"
    cached = fresh_snapshot(participant=participant, observation=observation, config=config, now=now + timedelta(minutes=2))
    assert cached.participant_total_usd == 125
    requests[0] = replace(requests[0], actual_cost=D("101"))
    with pytest.raises(ValueError, match="冲突"):
        refresh_participant_api_usage(client=Upstream(), participant=participant, observation=observation, config=config, observed_to=now + timedelta(minutes=3))
    assert APIUsageRequestFact.objects.count() == 1
    assert ParticipantAPIUsageSnapshot.objects.count() == 2


@pytest.mark.parametrize("field,value", [("account_id",8), ("user_id",52), ("id",0), ("actual_cost",D("NaN")), ("input_tokens",-1), ("long_context_billing_applied","false")])
def test_invalid_primary_facts_fail_closed(field, value):
    at = timezone.now()
    row = log(created_at=at - timedelta(seconds=1), **{field:value})
    with pytest.raises(ValueError):
        validate_interval_logs([row], account_id=7, user_id=51, started_at=at-timedelta(hours=1), ended_at=at)


@pytest.mark.django_db
def test_account_status_clips_raw_correction_to_rolling_window():
    from monitor.views.account_status import _correction_totals
    config = AppSettings.load()
    at = timezone.now()
    cutoff = at - timedelta(days=30)
    captured_observation(config, at=at, started=cutoff-timedelta(days=1), logs=[
        log(id=1, created_at=cutoff-timedelta(seconds=1)),
        log(id=2, created_at=cutoff),
    ])
    total = _correction_totals([7], config=config, observed_after=cutoff, observed_before=at)[7]
    assert total["amounts"].total == D("12.5")
    assert total["missing_correction_intervals"] == 0


@pytest.mark.parametrize("raw,expected", [({}, None), ({"long_context_billing_applied": None}, None), ({"long_context_billing_applied": False}, False), ({"long_context_billing_applied": True}, True)])
def test_upstream_long_context_flag_preserves_absence_and_explicit_false(raw, expected):
    from monitor.integrations.sub2api.usage import _optional_long_context_flag
    assert _optional_long_context_flag(raw) is expected


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_upstream_long_context_flag_does_not_coerce_ambiguous_values(value):
    from monitor.integrations.sub2api.usage import _optional_long_context_flag
    from monitor.integrations.sub2api import Sub2APIError
    with pytest.raises(Sub2APIError):
        _optional_long_context_flag({"long_context_billing_applied": value})


@pytest.mark.parametrize("value", [True, -1, "272001", 1.5, 2**63])
def test_upstream_input_tokens_rejects_non_integer_or_out_of_range(value):
    from monitor.integrations.sub2api.usage import _optional_tokens
    from monitor.integrations.sub2api import Sub2APIError
    with pytest.raises(Sub2APIError):
        _optional_tokens({"input_tokens": value}, "input_tokens")
