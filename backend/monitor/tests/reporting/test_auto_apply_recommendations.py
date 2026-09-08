"""Automatic writes obey the same actionable-recommendation and journal contract."""
from decimal import Decimal

import pytest

from monitor.balance_operations import auto_apply_recommendations
from monitor.history_state import LeaseGuard
from monitor.models import AppSettings, ParticipantBalanceOperation
from monitor.tests.helpers import create_participant, create_recommendation_snapshot


@pytest.mark.django_db(transaction=True)
def test_automatic_application_can_be_disabled_paused_and_does_not_repeat(monkeypatch):
    participant = create_participant(
        name="自动应用", sub2api_user_id=51, share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    writes = []

    class BalanceClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            writes.append((user_id, balance))
            return balance

    monkeypatch.setattr("monitor.balance_operations.Sub2APIClient", BalanceClient)
    config = AppSettings.load()
    config.auto_apply_recommendations = False
    config.save()
    auto_apply_recommendations()
    assert writes == []
    assert not ParticipantBalanceOperation.objects.exists()

    config.auto_apply_recommendations = True
    config.monitoring_enabled = False
    config.save()
    auto_apply_recommendations()
    assert writes == []

    config.monitoring_enabled = True
    config.save()
    assert auto_apply_recommendations() == {"applied": 1, "failed": 0}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert writes == [(51, participant.latest_balance_usd)]
    assert snapshot.recommendation_applied
    assert not snapshot.needs_manual_update
    assert ParticipantBalanceOperation.objects.get().state == "committed"

    assert auto_apply_recommendations() == {"applied": 0, "failed": 0}
    assert len(writes) == 1


@pytest.mark.django_db(transaction=True)
def test_automatic_application_without_suggestion_never_connects(monkeypatch):
    config = AppSettings.load()
    config.auto_apply_recommendations = True
    config.save()
    create_participant(name="尚无测算", sub2api_user_id=52, share_percent=50)

    def unexpected_client(_config):
        pytest.fail("没有有效建议时不应连接上游")

    monkeypatch.setattr("monitor.balance_operations.Sub2APIClient", unexpected_client)
    assert auto_apply_recommendations() == {"applied": 0, "failed": 0}
    assert not ParticipantBalanceOperation.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_automatic_application_skips_busy_account_without_failure(monkeypatch):
    participant = create_participant(name="采样中", sub2api_user_id=53, share_percent=50)
    snapshot = create_recommendation_snapshot(participant)
    config = AppSettings.load()
    config.auto_apply_recommendations = True
    config.save()

    def unexpected_client(_config):
        pytest.fail("采样锁未释放时不应修改上游余额")

    monkeypatch.setattr("monitor.balance_operations.Sub2APIClient", unexpected_client)
    guard = LeaseGuard.acquire(7)
    try:
        assert auto_apply_recommendations() == {"applied": 0, "failed": 0}
    finally:
        guard.release()
    snapshot.refresh_from_db()
    assert not snapshot.recommendation_applied
    assert snapshot.needs_manual_update
    assert not ParticipantBalanceOperation.objects.exists()
