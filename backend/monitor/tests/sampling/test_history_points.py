from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from monitor.engine import _persist_capture
from monitor.history_state import LeaseGuard, LeaseLostError
from monitor.integrations.sub2api import (
    Sub2APIUserUsage,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor.models import (
    AppSettings,
    HistoryMaintenanceState,
    Observation,
    Participant,
    ParticipantBalanceSample,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from monitor.replay import rebuild_observation_suffix
from monitor.sampling.local_usage import save_local_bundle
from monitor.sampling.types import (
    LocalBundle,
    LocalParticipantData,
    WindowReference,
)


ACCOUNT_ID = 7


def _bundle_fixture():
    config = AppSettings.load()
    config.openai_account_id = ACCOUNT_ID
    config.cost_basis = "actual"
    config.save()
    participant = Participant.objects.create(
        name="车主",
        sub2api_user_id=11,
        share_percent=Decimal("100"),
        is_owner=True,
    )
    checked_at = timezone.now().replace(microsecond=0)
    reference = WindowReference(
        account_id=ACCOUNT_ID,
        reset_at=checked_at + timedelta(days=6),
        window_seconds=604800,
    )
    usage = Sub2APIUserUsage(
        user_id=11,
        username="owner",
        email="owner@example.com",
        stats=UsageStats(Decimal("25"), Decimal("20")),
    )
    local = LocalBundle(
        total=UsageStats(Decimal("25"), Decimal("20")),
        participants=[
            LocalParticipantData(
                participant=participant,
                stats=usage.stats,
                balance=UserBalance(Decimal("180"), Decimal("0")),
            )
        ],
        users=[usage],
        checked_at=checked_at,
        cost_window_started_at=checked_at - timedelta(hours=3),
        cost_window_ended_at=checked_at,
    )
    return config, reference, local, participant


@pytest.mark.django_db
def test_save_local_bundle_commits_one_complete_fact_group():
    config, reference, local, participant = _bundle_fixture()
    point = save_local_bundle(
        config,
        reference,
        local,
        None,
        capture_started_at=local.checked_at - timedelta(seconds=1),
        capture_finished_at=local.checked_at + timedelta(seconds=1),
    )

    assert point.write_status == "complete"
    assert point.reconciliation_status == "reconciled"
    assert point.expected_user_count == 1
    assert point.user_samples.count() == 1
    assert point.participant_usage_samples.count() == 1
    assert point.balance_samples.count() == 1
    user = point.user_samples.get()
    assert user.interval_actual_cost == Decimal("20")
    assert user.interval_source == "window_total"
    participant.refresh_from_db()
    assert participant.latest_balance_usd == Decimal("180")
    assert participant.latest_selected_cost == Decimal("20")


@pytest.mark.django_db
def test_monitor_capture_commits_observation_and_revision_with_complete_point():
    config, reference, local, _participant = _bundle_fixture()
    HistoryMaintenanceState.objects.create(account_id=ACCOUNT_ID)
    guard = LeaseGuard.acquire(ACCOUNT_ID)
    observation = _persist_capture(
        config,
        reference,
        local,
        None,
        latest_raw=None,
        guard=guard,
        capture_started_at=local.checked_at - timedelta(seconds=1),
        interval_logs=None,
        window=WeeklyWindow(
            used_percent=Decimal("10"),
            window_seconds=reference.window_seconds,
            reset_after_seconds=int(
                (reference.reset_at - local.checked_at).total_seconds()
            ),
            reset_at=int(reference.reset_at.timestamp()),
            slot="primary",
            sampled_at=local.checked_at.isoformat(),
        ),
        source="manual",
    )
    guard.release()

    point = UsageSamplePoint.objects.get()
    state = HistoryMaintenanceState.objects.get(account_id=ACCOUNT_ID)
    assert observation.sample_point_id == point.id
    assert point.write_status == "complete"
    assert point.fact_revision == 1
    assert state.fact_revision == 1
    assert point.user_samples.count() == 1
    assert point.participant_usage_samples.count() == 1
    assert point.balance_samples.count() == 1


@pytest.mark.django_db
def test_save_local_bundle_rolls_back_earlier_rows_when_late_write_fails(
    monkeypatch,
):
    config, reference, local, participant = _bundle_fixture()

    def fail_balances(*_args, **_kwargs):
        raise RuntimeError("late balance write failed")

    monkeypatch.setattr(
        ParticipantBalanceSample.objects,
        "bulk_create",
        fail_balances,
    )
    with pytest.raises(RuntimeError, match="late balance write failed"):
        save_local_bundle(
            config,
            reference,
            local,
            None,
            capture_started_at=local.checked_at - timedelta(seconds=1),
            capture_finished_at=local.checked_at + timedelta(seconds=1),
        )

    assert not UsageSamplePoint.objects.exists()
    assert not Sub2APIUserUsageSample.objects.exists()
    assert not ParticipantUsageSample.objects.exists()
    assert not ParticipantBalanceSample.objects.exists()
    participant.refresh_from_db()
    assert participant.latest_balance_usd is None
    assert participant.latest_selected_cost is None
    assert participant.last_checked_at is None


@pytest.mark.django_db
def test_monitor_capture_rolls_back_complete_local_group_if_observation_fails(
    monkeypatch,
):
    config, reference, local, _participant = _bundle_fixture()
    HistoryMaintenanceState.objects.create(account_id=ACCOUNT_ID)
    guard = LeaseGuard.acquire(ACCOUNT_ID)

    def fail_observation(**_kwargs):
        raise RuntimeError("observation write failed")

    monkeypatch.setattr(
        "monitor.engine._create_raw_observation",
        fail_observation,
    )
    with pytest.raises(RuntimeError, match="observation write failed"):
        _persist_capture(
            config,
            reference,
            local,
            None,
            latest_raw=None,
            guard=guard,
            capture_started_at=local.checked_at - timedelta(seconds=1),
            interval_logs=None,
            window=object(),
        )
    guard.release()

    assert not UsageSamplePoint.objects.exists()
    assert not Sub2APIUserUsageSample.objects.exists()
    assert not ParticipantUsageSample.objects.exists()
    assert not Observation.objects.exists()
    state = HistoryMaintenanceState.objects.get(account_id=ACCOUNT_ID)
    assert state.fact_revision == 0


@pytest.mark.django_db
def test_monitor_replay_fails_closed_if_lease_expires_before_terminal_commit(
    monkeypatch,
):
    config, reference, local, _participant = _bundle_fixture()
    HistoryMaintenanceState.objects.create(account_id=ACCOUNT_ID)
    guard = LeaseGuard.acquire(ACCOUNT_ID)
    observation = _persist_capture(
        config,
        reference,
        local,
        None,
        latest_raw=None,
        guard=guard,
        capture_started_at=local.checked_at - timedelta(seconds=1),
        interval_logs=None,
        window=WeeklyWindow(
            used_percent=Decimal("10"),
            window_seconds=reference.window_seconds,
            reset_after_seconds=int(
                (reference.reset_at - local.checked_at).total_seconds()
            ),
            reset_at=int(reference.reset_at.timestamp()),
            slot="primary",
            sampled_at=local.checked_at.isoformat(),
        ),
        source="manual",
    )
    before = {
        "sample_note": observation.sample_note,
        "attribution_started_at": observation.attribution_started_at,
        "snapshots": observation.participant_snapshots.count(),
    }
    from monitor.accounting import replay as replay_module

    original = replay_module._replay_usage_samples

    def expire_lease_after_replay(*args, **kwargs):
        result = original(*args, **kwargs)
        HistoryMaintenanceState.objects.filter(account_id=ACCOUNT_ID).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        return result

    monkeypatch.setattr(
        replay_module,
        "_replay_usage_samples",
        expire_lease_after_replay,
    )

    with pytest.raises(LeaseLostError):
        rebuild_observation_suffix(observation, config, guard=guard)

    observation.refresh_from_db()
    assert observation.sample_note == before["sample_note"]
    assert observation.attribution_started_at == before["attribution_started_at"]
    assert observation.participant_snapshots.count() == before["snapshots"]
    guard.release()
