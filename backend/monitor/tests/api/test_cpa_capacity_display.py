from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from monitor.cpa.billing import weekly_distribution
from monitor.cpa.participants import owner_index
from monitor.cpa.quota_status import quota_detail
from monitor.cpa.usage import refresh_cpa_history
from monitor.models import CPAAccountCollectionInterval, Observation
from monitor.particle_trajectory import particle_trajectory_data
from monitor.replay import rebuild_account
from monitor.reporting.recommendations import _capacity_values
from .test_cpa_participants import event, observation
from . import test_cpa_participants as fixtures
from .test_cpa_billing import scenario, report

setup = fixtures.setup
pytestmark = pytest.mark.django_db


def partial_cycle(setup):
    config, admin, account, a, b, keys, start = setup
    config.weekly_quota_model = "time_varying"
    config.save()
    official = start - timedelta(hours=2)
    connected = start + timedelta(minutes=30)
    now = start + timedelta(hours=1)
    event(account, keys[0], start + timedelta(minutes=10), tokens=2_000_000)
    event(account, keys[0], start + timedelta(minutes=40))
    CPAAccountCollectionInterval.objects.create(
        account=account, session_key="reconnected", connected_at=connected
    )
    observation(account, official, connected, 15, 20)
    observation(account, official, now, 16, 30)
    rebuild_account(account.fact_key, config)
    return config, account, a, b, now


def test_reconnect_capacity_matches_particle_trajectory_without_fake_balance(setup):
    config, account, a, b, now = partial_cycle(setup)
    trajectory = particle_trajectory_data(config, account)
    week = weekly_distribution(
        [account], {a.id: a, b.id: b}, config, now, owner_index()
    )[0]
    assert trajectory["available"]
    assert week["capacity_usd"] == trajectory["latest"]["capacity_usd"]
    assert (
        week["capacity_estimate"]["lower_usd"]
        == trajectory["latest"]["capacity_lower_usd"]
    )
    assert (
        week["capacity_estimate"]["upper_usd"]
        == trajectory["latest"]["capacity_upper_usd"]
    )
    assert week["coverage_complete"] is False
    assert week["remaining_usd"] is None
    assert week["upstream_remaining_percent"] == 84
    assert week["members"][0]["usage_percent"] > 0
    assert week["usage_usd"] == 30
    a.refresh_from_db()
    assert a.latest_balance_usd == Decimal("123")


def test_missing_history_does_not_hide_capacity_but_settlement_stays_unknown(setup):
    s = scenario(setup)
    account = s[2]
    CPAAccountCollectionInterval.objects.filter(account=account).delete()
    for obs in Observation.objects.filter(account_id=account.fact_key):
        obs.attribution_started_at = obs.upstream_resets_at - timedelta(days=7)
        obs.model_diagnostics = {"algorithm": "posterior"}
        obs.capacity_lower_usd = 900
        obs.capacity_upper_usd = 1100
        obs.save()
    result = report(s)
    assert result["capacity_usd"] == 4000
    assert result["members"][0]["entitlement_usd"] == 2000
    assert result["members"][0]["usage_percent"] == 12.5
    assert result["members"][0]["remaining_usd"] is None
    assert result["expired_usd"] is None
    assert result["available_usd"] is None
    assert result["members"][0]["recommended_usd"] is None


def test_quota_status_displays_same_capacity_even_with_extra_model_limits(setup):
    config, account, a, b, now = partial_cycle(setup)
    latest = Observation.objects.filter(account_id=account.fact_key).latest(
        "observed_at"
    )
    body = {
        "rate_limit": {
            "primary_window": {
                "limit_window_seconds": 604800,
                "used_percent": 16,
                "reset_at": latest.upstream_resets_at.timestamp(),
            }
        },
        "additional_rate_limits": [
            {
                "limit_name": "Spark",
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 18000,
                        "used_percent": 0,
                        "reset_at": (now + timedelta(hours=5)).timestamp(),
                    }
                },
            }
        ],
    }
    detail = quota_detail(account, config, now, body=body)
    week = detail["windows"][0]
    assert (
        week["capacity_estimate"]["capacity_usd"]
        == particle_trajectory_data(config, account)["latest"]["capacity_usd"]
    )
    assert week["prediction"] is None  # No invented request/token totals.
    assert detail["windows"][1]["capacity_estimate"] is None


@pytest.mark.parametrize("change", ["late_event", "pricing"])
def test_repricing_or_late_segment_events_require_replay_before_display(setup, change):
    config, account, a, b, now = partial_cycle(setup)
    if change == "pricing":
        config.cpa_model_pricing["gpt-test"]["input"] = "30"
        config.save()
    else:
        event(account, setup[5][0], now - timedelta(minutes=10))
    # Earlier prior may still be displayed, but never the stale latest posterior.
    latest = Observation.objects.filter(account_id=account.fact_key).latest(
        "observed_at"
    )
    week = weekly_distribution([account], {a.id: a}, config, now, owner_index())[0]
    assert week["quota_as_of"] != latest.observed_at.isoformat()
    refresh_cpa_history(config)
    week = weekly_distribution([account], {a.id: a}, config, now, owner_index())[0]
    assert (
        week["capacity_usd"]
        == particle_trajectory_data(config, account)["latest"]["capacity_usd"]
    )


def test_unobserved_account_has_no_invented_initial_capacity(setup):
    config, _, account, a, _, _, start = setup
    week = weekly_distribution([account], {a.id: a}, config, start, owner_index())[0]
    assert week["capacity_usd"] is None
    assert week["capacity_estimate"] is None


def test_first_reconnect_observation_is_explicit_prior(setup):
    config, account, a, b, now = partial_cycle(setup)
    first = Observation.objects.filter(account_id=account.fact_key).earliest(
        "observed_at"
    )
    week = weekly_distribution(
        [account], {a.id: a}, config, first.observed_at, owner_index()
    )[0]
    assert week["capacity_usd"] > 0
    assert week["capacity_estimate"]["prior_only"] is True
    assert week["capacity_estimate"]["as_of"] == first.observed_at.isoformat()
    assert week["remaining_usd"] is None


def test_average_model_keeps_its_capacity_after_reconnect(setup):
    config, account, a, b, now = partial_cycle(setup)
    config.weekly_quota_model = "constant_average"
    config.save()
    latest = Observation.objects.filter(account_id=account.fact_key).latest(
        "observed_at"
    )
    week = weekly_distribution([account], {a.id: a}, config, now, owner_index())[0]
    expected = _capacity_values(
        SimpleNamespace(observation=latest, selected_cost=Decimal(0)), config
    )[0]
    assert week["capacity_usd"] == float(expected)
    assert week["capacity_estimate"]["source"] == "quota_model"
    assert week["remaining_usd"] is None
