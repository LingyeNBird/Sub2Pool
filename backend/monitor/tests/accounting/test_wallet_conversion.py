"""Real model replay keeps resource inference while converting wallet outputs."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from monitor.models import AppSettings
from monitor.replay import rebuild_account
from monitor.tests.billing_correction.test_corrections import captured_observation, log
from monitor.tests.helpers import create_monitored_account, create_participant, create_participant_snapshot


@pytest.mark.django_db
def test_dynamic_wallet_conversion_preserves_model_inference_and_replay_stability():
    config = AppSettings.load()
    config.safety_factor = Decimal("1")
    config.save()
    create_monitored_account()
    participant = create_participant(name="dynamic wallet", sub2api_user_id=51, share_percent=100)
    at = datetime.fromisoformat("2026-09-07T12:00:00+00:00")
    observation, _ = captured_observation(config, at=at, logs=[log(
        created_at=at-timedelta(seconds=1), total_cost=Decimal("100"),
        long_context_billing_applied=False,
    )])
    snapshot = create_participant_snapshot(
        observation=observation, participant=participant,
        raw_selected_cost=Decimal("100"), selected_cost=Decimal("100"),
        current_balance_usd=Decimal("80"),
    )
    # Reference the old, unconverted output without replacing the actual particle model.
    with patch("monitor.accounting.dynamic_attribution.balance_conversion_factor", return_value=Decimal("1")):
        rebuild_account(7, config)
    observation.refresh_from_db()
    snapshot.refresh_from_db()
    inference = (observation.effective_usd_per_percent, snapshot.charged_cycle_percent, snapshot.selected_cost)
    fields = ("recommended_balance_usd", "recommended_balance_min_usd", "recommended_balance_max_usd", "deterministic_balance_min_usd", "deterministic_balance_max_usd")
    corrected_balances = {field: getattr(snapshot, field) for field in fields}

    rebuild_account(7, config)
    observation.refresh_from_db()
    snapshot.refresh_from_db()
    assert (observation.effective_usd_per_percent, snapshot.charged_cycle_percent, snapshot.selected_cost) == inference
    assert snapshot.selected_cost == Decimal("225")
    for field in fields:
        assert abs(getattr(snapshot, field) - corrected_balances[field]/Decimal("2.25")) <= Decimal("0.01")
    assert snapshot.balance_difference_usd == snapshot.recommended_balance_usd - Decimal("80")
    converted = {field: getattr(snapshot, field) for field in fields}
    rebuild_account(7, config)
    snapshot.refresh_from_db()
    assert {field: getattr(snapshot, field) for field in fields} == converted
