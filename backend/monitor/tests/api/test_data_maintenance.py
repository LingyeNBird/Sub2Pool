from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.fact_utils import expected_user_digest
from monitor.historical_rebuild import apply_rebuild_plan, create_rebuild_plan
from monitor.historical_rebuild.contracts import source_fact_digest
from monitor.history_state import LeaseGuard, LeaseLostError
from monitor.models import (
    AppSettings,
    HistoricalRebuildRun,
    HistoryMaintenanceState,
    Observation,
    Participant,
    ParticipantBalanceSample,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from monitor.replay import rebuild_account
from monitor.tests.helpers import jwt_login

ACCOUNT_ID = 7
USER_IDS = (11, 12)


@pytest.fixture
def admin_client():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    return client, headers


def _configure() -> AppSettings:
    config = AppSettings.load()
    config.openai_account_id = ACCOUNT_ID
    config.timezone = "Asia/Shanghai"
    config.cost_basis = "actual"
    config.fast_correction_enabled = True
    config.save()
    return config


def _create_complete_history(*, total: Decimal = Decimal("100")):
    config = _configure()
    observed_at = timezone.now().replace(microsecond=0)
    window_started_at = observed_at - timedelta(hours=4)
    reset_at = observed_at + timedelta(days=6)
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=USER_IDS[0],
        share_percent=Decimal("50"),
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=USER_IDS[1],
        share_percent=Decimal("50"),
    )
    point = UsageSamplePoint.objects.create(
        account_id=ACCOUNT_ID,
        observed_at=observed_at,
        window_started_at=window_started_at,
        window_ended_at=observed_at,
        window_resets_at=reset_at,
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=total,
        account_actual_cost=total,
        interval_started_at=window_started_at,
        interval_standard_cost=total,
        interval_actual_cost=total,
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=2,
        expected_user_digest=expected_user_digest(USER_IDS),
        write_status="complete",
        reconciliation_status="reconciled",
        provenance={"source": "test_complete_capture"},
    )
    observation = Observation.objects.create(
        sample_point=point,
        account_id=ACCOUNT_ID,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=Decimal("10"),
        attribution_started_at=reset_at - timedelta(days=7),
        raw_selected_total_cost=total,
        selected_total_cost=total,
        total_standard_cost=total,
        total_actual_cost=total,
        cost_window_started_at=window_started_at,
        cost_window_ended_at=observed_at,
        interval_cost_started_at=window_started_at,
        interval_standard_cost=total,
        interval_actual_cost=total,
        interval_cost_source="saved_interval",
        fast_correction_started_at=window_started_at,
        fast_correction_request_count=0,
        fast_correction_standard_cost=Decimal("0"),
        fast_correction_actual_cost=Decimal("0"),
        effective_usd_per_percent=Decimal("10"),
        raw_window={"rate_method": "fixture"},
    )
    costs = (Decimal("60"), total - Decimal("60"))
    users = []
    for participant, user_id, cost in zip(
        (owner, rider), USER_IDS, costs, strict=True
    ):
        users.append(
            Sub2APIUserUsageSample.objects.create(
                sample_point=point,
                account_id=ACCOUNT_ID,
                sub2api_user_id=user_id,
                username=f"user-{user_id}",
                email=f"user-{user_id}@example.com",
                observed_at=observed_at,
                window_started_at=window_started_at,
                window_ended_at=observed_at,
                window_resets_at=reset_at,
                total_standard_cost=cost,
                total_actual_cost=cost,
                interval_started_at=window_started_at,
                interval_standard_cost=cost,
                interval_actual_cost=cost,
                interval_source="saved_interval",
            )
        )
        ParticipantUsageSample.objects.create(
            participant=participant,
            account_id=ACCOUNT_ID,
            sample_point=point,
            attribution_started_at=reset_at - timedelta(days=7),
            observed_at=observed_at,
            balance_usd=Decimal("200") - cost,
            selected_cost=cost,
            raw_selected_cost=cost,
        )
        ParticipantBalanceSample.objects.create(
            point=point,
            participant=participant,
            balance_usd=Decimal("200") - cost,
            captured_at=observed_at,
            provenance="test_capture",
        )
    HistoryMaintenanceState.objects.create(account_id=ACCOUNT_ID)
    rebuild_account(ACCOUNT_ID, config)
    return config, point, observation, users


@pytest.mark.django_db
def test_local_plan_is_persistent_zero_network_and_replays(admin_client):
    client, headers = admin_client
    _config, point, _observation, _users = _create_complete_history()
    before = source_fact_digest(ACCOUNT_ID)

    created = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    plan = created.json()["data"]
    assert plan["state"] == "ready"
    assert plan["safe_to_apply"] is True
    assert set(plan) == {
        "id",
        "account_id",
        "state",
        "digest",
        "created_at",
        "expires_at",
        "base_revision",
        "result_revision",
        "blockers",
        "replay_summary",
        "safe_to_apply",
        "algorithm_version",
        "build_id",
    }

    detail = client.get(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}",
        **headers,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["digest"] == plan["digest"]

    applied = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert applied.status_code == 200
    applied_data = applied.json()["data"]
    assert applied_data["state"] == "applied"
    assert applied_data["result_revision"] == 1
    assert applied_data["replay_summary"]["rebuilt_observations"] == 1
    point.refresh_from_db()
    assert point.account_actual_cost == Decimal("100")
    assert source_fact_digest(ACCOUNT_ID) == before

    repeated = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["result_revision"] == 1


@pytest.mark.django_db
def test_api_clean_cutover_rejects_removed_modes_and_requires_digest(admin_client):
    client, headers = admin_client
    _create_complete_history()
    removed_mode = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    )
    assert removed_mode.status_code == 400

    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={},
        content_type="application/json",
        **headers,
    ).json()["data"]
    missing_digest = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={},
        content_type="application/json",
        **headers,
    )
    assert missing_digest.status_code == 400
    assert client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/rollback",
        **headers,
    ).status_code == 404
    assert client.post(
        "/api/settings/data-maintenance/history-rebuild", **headers
    ).status_code == 404
    assert client.post(
        "/api/settings/data-maintenance/history-rebuild-preview", **headers
    ).status_code == 404


@pytest.mark.django_db
def test_plan_rejects_source_changes_after_creation(admin_client):
    client, headers = admin_client
    _config, point, _observation, _users = _create_complete_history()
    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={},
        content_type="application/json",
        **headers,
    ).json()["data"]
    point.account_actual_cost = Decimal("101")
    point.save(update_fields=["account_actual_cost"])

    response = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 409
    run = HistoricalRebuildRun.objects.get(pk=plan["id"])
    assert run.state == "stale"
    assert any(item["code"] == "apply_stale" for item in run.blockers)


@pytest.mark.django_db
def test_mutating_persisted_plan_invalidates_digest(admin_client):
    client, headers = admin_client
    _create_complete_history()
    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={},
        content_type="application/json",
        **headers,
    ).json()["data"]
    run = HistoricalRebuildRun.objects.get(pk=plan["id"])
    run.blockers = [
        {
            "code": "tampered",
            "severity": "warning",
            "point_id": None,
            "message": "tampered",
        }
    ]
    run.save(update_fields=["blockers"])

    response = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 409
    run.refresh_from_db()
    assert run.state == "stale"


@pytest.mark.django_db
def test_replay_failure_rolls_back_plan_and_revision(monkeypatch):
    _create_complete_history()
    run = create_rebuild_plan(AppSettings.load())

    def fail_replay(*_args, **_kwargs):
        raise RuntimeError("fault injection during replay")

    monkeypatch.setattr(
        "monitor.historical_rebuild.executor.rebuild_account",
        fail_replay,
    )
    with pytest.raises(RuntimeError, match="fault injection"):
        apply_rebuild_plan(run.id, run.plan_digest)

    run.refresh_from_db()
    state = HistoryMaintenanceState.objects.get(account_id=ACCOUNT_ID)
    assert run.state == "ready"
    assert run.result_revision is None
    assert state.fact_revision == 0


@pytest.mark.django_db
def test_fence_token_rejects_an_expired_owner_after_reacquire():
    _create_complete_history()
    old_guard = LeaseGuard.acquire(ACCOUNT_ID, ttl=timedelta(minutes=1))
    HistoryMaintenanceState.objects.filter(account_id=ACCOUNT_ID).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    new_guard = LeaseGuard.acquire(ACCOUNT_ID, ttl=timedelta(minutes=1))
    state = HistoryMaintenanceState.objects.get(account_id=ACCOUNT_ID)
    assert new_guard.token > old_guard.token
    with pytest.raises(LeaseLostError):
        old_guard.assert_owned(state)
    new_guard.release()


@pytest.mark.django_db
def test_negative_residual_is_a_hard_local_audit_blocker(admin_client):
    client, headers = admin_client
    _config, point, observation, _users = _create_complete_history()
    point.account_standard_cost = Decimal("99")
    point.account_actual_cost = Decimal("99")
    point.residual_standard_cost = Decimal("-1")
    point.residual_actual_cost = Decimal("-1")
    point.reconciliation_status = "conflict"
    point.save(
        update_fields=[
            "account_standard_cost",
            "account_actual_cost",
            "residual_standard_cost",
            "residual_actual_cost",
            "reconciliation_status",
        ]
    )
    observation.raw_selected_total_cost = Decimal("99")
    observation.total_standard_cost = Decimal("99")
    observation.total_actual_cost = Decimal("99")
    observation.save(
        update_fields=[
            "raw_selected_total_cost",
            "total_standard_cost",
            "total_actual_cost",
        ]
    )

    response = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201
    plan = response.json()["data"]
    assert plan["state"] == "blocked"
    assert any(
        item["code"] == "negative_cost" and item["severity"] == "hard"
        for item in plan["blockers"]
    )


@pytest.mark.django_db
def test_local_audit_rejects_interval_and_observation_coordinate_drift():
    _config, point, _observation, users = _create_complete_history()
    observed_at = point.observed_at + timedelta(hours=1)
    second = UsageSamplePoint.objects.create(
        account_id=ACCOUNT_ID,
        observed_at=observed_at,
        window_started_at=point.window_started_at,
        window_ended_at=observed_at,
        window_resets_at=point.window_resets_at,
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=Decimal("110"),
        account_actual_cost=Decimal("110"),
        interval_started_at=point.observed_at,
        interval_standard_cost=Decimal("10"),
        interval_actual_cost=Decimal("10"),
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=2,
        expected_user_digest=expected_user_digest(USER_IDS),
        write_status="complete",
        reconciliation_status="reconciled",
        provenance={"source": "invalid_interval_fixture"},
    )
    Observation.objects.create(
        sample_point=second,
        account_id=ACCOUNT_ID,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=point.window_resets_at + timedelta(minutes=1),
        upstream_used_percent=Decimal("11"),
        selected_total_cost=Decimal("110"),
        raw_selected_total_cost=Decimal("110"),
        total_standard_cost=Decimal("110"),
        total_actual_cost=Decimal("110"),
        cost_window_started_at=point.window_started_at,
        cost_window_ended_at=observed_at,
        interval_cost_started_at=point.observed_at,
        interval_standard_cost=Decimal("10"),
        interval_actual_cost=Decimal("10"),
        interval_cost_source="saved_interval",
        raw_window={"rate_method": "fixture"},
        effective_usd_per_percent=Decimal("10"),
    )
    for user, total in zip(
        users,
        (Decimal("65"), Decimal("45")),
        strict=True,
    ):
        Sub2APIUserUsageSample.objects.create(
            sample_point=second,
            account_id=ACCOUNT_ID,
            sub2api_user_id=user.sub2api_user_id,
            username=user.username,
            email=user.email,
            observed_at=observed_at,
            window_started_at=point.window_started_at,
            window_ended_at=observed_at,
            window_resets_at=point.window_resets_at,
            total_standard_cost=total,
            total_actual_cost=total,
            interval_started_at=point.observed_at - timedelta(minutes=5),
            interval_standard_cost=Decimal("50"),
            interval_actual_cost=Decimal("50"),
            interval_source="invalid_fixture",
        )

    run = create_rebuild_plan(AppSettings.load())

    assert run.state == "blocked"
    codes = {item["code"] for item in run.blockers if item["severity"] == "hard"}
    assert "user_interval_discontinuity" in codes
    assert "user_interval_total_mismatch" in codes
    assert "interval_user_residual_mismatch" in codes
    assert "observation_point_window_mismatch" in codes
