"""Recommendation consumers receive wallet dollars, not corrected-cost dollars."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from monitor.balance_operations import apply_participant_recommendation
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.persistence import apply_fast_interval
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.models import AppSettings, Observation, ParticipantBalanceOperation
from monitor.replay import rebuild_account
from monitor.reporting import aggregate_recommendation
from monitor.reporting.recommendations import display_snapshot_data
from monitor.tests.billing_correction.test_corrections import captured_observation, log
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
)

D = Decimal
AT = datetime.fromisoformat("2026-09-07T12:00:00+00:00")


@pytest.mark.django_db(transaction=True)
def test_constant_recommendation_and_application_use_actual_wallet_dollars(monkeypatch):
    config = AppSettings.load()
    config.weekly_quota_model = "constant_average"
    config.safety_factor = D("1")
    config.fast_correction_enabled = False
    config.long_context_correction_enabled = False
    config.model_correction_enabled = False
    config.save()
    account = create_monitored_account()
    participant = create_participant(
        name="wallet rider", sub2api_user_id=51, share_percent=100,
        latest_balance_usd=D("80"),
    )
    observation, _ = captured_observation(config, at=AT, logs=[log(
        created_at=AT-timedelta(seconds=1), total_cost=D("100"),
        long_context_billing_applied=False,
    )])
    create_participant_snapshot(
        observation=observation, participant=participant,
        raw_selected_cost=D("100"), selected_cost=D("100"),
        current_balance_usd=D("80"),
    )
    rebuild_account(7, config)
    baseline, _ = aggregate_recommendation(participant, config)
    baseline_display = display_snapshot_data(participant, config, account)
    participant.latest_balance_usd = D(str(baseline["recommended_balance_usd"]))
    participant.save()
    config.fast_correction_enabled = True
    config.long_context_correction_enabled = True
    config.model_correction_enabled = True
    config.save()
    rebuild_account(7, config)
    observation.refresh_from_db()
    converted, _ = aggregate_recommendation(participant, config)
    converted_display = display_snapshot_data(participant, config, account)
    assert observation.selected_total_cost == D("225")
    assert observation.raw_selected_total_cost == D("100")
    for field in (
        "recommended_balance_usd", "recommended_balance_min_usd",
        "recommended_balance_max_usd",
    ):
        assert converted[field] == baseline[field]
        assert converted_display[field] == baseline_display[field]
    assert converted["balance_difference_usd"] == 0
    assert not converted["needs_manual_update"]

    participant.latest_balance_usd = D("80")
    participant.save()
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
    operation = apply_participant_recommendation(participant.id)
    assert writes == [(51, D(str(baseline["recommended_balance_usd"])))]
    assert operation.confirmed_balance_usd == writes[0][1]
    participant.refresh_from_db()
    after, _ = aggregate_recommendation(participant, config)
    assert not after["needs_manual_update"]
    assert ParticipantBalanceOperation.objects.get().state == "committed"


@pytest.mark.django_db
def test_standard_cost_recommendation_converts_to_actual_debit_even_without_corrections():
    config = AppSettings.load()
    config.weekly_quota_model = "constant_average"
    config.safety_factor = D("1")
    config.fast_correction_enabled = False
    config.long_context_correction_enabled = False
    config.model_correction_enabled = False
    config.save()
    create_monitored_account()
    participant = create_participant(
        name="different group pricing", sub2api_user_id=51, share_percent=100,
        latest_balance_usd=D("80"),
    )
    observation, _ = captured_observation(config, at=AT, logs=[log(
        created_at=AT-timedelta(seconds=1), total_cost=D("200"), actual_cost=D("100"),
    )])
    create_participant_snapshot(
        observation=observation, participant=participant,
        raw_selected_cost=D("100"), selected_cost=D("100"),
        current_balance_usd=D("80"),
    )
    from monitor.models import Sub2APIUserUsageSample
    Sub2APIUserUsageSample.objects.create(
        account_id=7, sub2api_user_id=51, observed_at=AT,
        window_resets_at=observation.upstream_resets_at,
        total_standard_cost=D("200"), total_actual_cost=D("100"),
    )
    rebuild_account(7, config)
    actual, _ = aggregate_recommendation(participant, config)
    config.cost_basis = "standard"
    config.save()
    rebuild_account(7, config)
    standard, _ = aggregate_recommendation(participant, config)
    assert standard["recommended_balance_usd"] == actual["recommended_balance_usd"]
    assert standard["recommended_balance_min_usd"] == actual["recommended_balance_min_usd"]
    assert standard["recommended_balance_max_usd"] == actual["recommended_balance_max_usd"]


@pytest.mark.django_db
def test_pool_converts_each_signed_source_before_netting_and_applies_safety_once():
    config = AppSettings.load()
    config.weekly_quota_model = "time_varying"
    config.safety_factor = D("0.9")
    config.fast_correction_enabled = False
    config.long_context_correction_enabled = False
    config.model_correction_rules = [{"model_pattern": "gpt-6*", "multiplier": "2"}]
    config.save()
    first = create_monitored_account(7)
    second = create_monitored_account(8)
    participant = create_participant(
        name="cross-account", sub2api_user_id=51, share_percent=50,
        account=first, latest_balance_usd=D("80"),
    )
    from monitor.models import AccountParticipant, PoolParticipant
    AccountParticipant.objects.create(account=second, participant=participant)
    PoolParticipant.objects.create(pool=second.pool, participant=participant, share_percent=50)

    for account, capacity, charged, raw_user, raw_total, model in (
        (first, D("2000"), D("20"), D("200"), D("400"), "gpt-6"),
        (second, D("1000"), D("60"), D("600"), D("800"), "gpt-5.4"),
    ):
        factor = D("2") if account == first else D("1")
        start = AT - timedelta(days=1)
        observation = Observation.objects.create(
            account_id=account.external_account_id, observed_at=AT,
            upstream_resets_at=start+timedelta(days=7), attribution_started_at=start,
            upstream_used_percent=40 if account == first else 80,
            interval_used_percent=40 if account == first else 80,
            raw_selected_total_cost=raw_total, selected_total_cost=raw_total*factor,
            total_actual_cost=raw_total, total_standard_cost=raw_total,
            effective_usd_per_percent=capacity/100,
            capacity_lower_usd=capacity, capacity_upper_usd=capacity,
        )
        request = log(
            account_id=account.external_account_id, created_at=AT-timedelta(seconds=1),
            model=model, service_tier="default", total_cost=raw_user,
            actual_cost=raw_user, long_context_billing_applied=False,
        )
        interval = aggregate_fast_logs(
            [request], started_at=start, ended_at=AT,
            rules=FastCorrectionRuleSet(config.fast_correction_rules),
        )
        apply_fast_interval(observation, interval)
        observation.save()
        create_participant_snapshot(
            observation=observation, participant=participant,
            raw_selected_cost=raw_user, selected_cost=raw_user*factor,
            current_balance_usd=D("80"), charged_cycle_percent=charged,
            charged_percent_lower=charged, charged_percent_upper=charged,
        )
    recommendation, _ = aggregate_recommendation(participant, config)
    sources = {source["external_account_id"]: source for source in recommendation["sources"]}
    assert sources[7]["net_position_usd"] == 300
    assert sources[8]["net_position_usd"] == -100
    assert recommendation["recommended_balance_usd"] == 180
    assert recommendation["recommended_balance_min_usd"] == 180
    assert recommendation["recommended_balance_max_usd"] == 180
    assert sources[7]["contribution_usd"] == 180
    assert sources[8]["contribution_usd"] == 0
