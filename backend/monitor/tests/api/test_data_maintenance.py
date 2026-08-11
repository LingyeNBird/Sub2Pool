from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import importlib
import tracemalloc

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.utils import timezone

from monitor.fact_utils import canonical_digest, expected_user_digest
from monitor.historical_rebuild import (
    HistoricalRebuildConflict,
    apply_rebuild_plan,
    create_rebuild_plan,
    rollback_rebuild_plan,
)
from monitor.historical_rebuild.contracts import source_fact_digest
from monitor.historical_rebuild.planner import _scan_point
from monitor.history_state import LeaseGuard, LeaseLostError, bump_fact_revision
from monitor.integrations.sub2api import (
    Sub2APIError,
    Sub2APIUsageLog,
    UsageLogScan,
)
from monitor.models import (
    AppSettings,
    HistoricalRebuildRun,
    HistoricalRebuildPatch,
    HistoryMaintenanceState,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantBalanceSample,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    UsageSamplePoint,
)
from monitor.replay import rebuild_account
from monitor.tests.helpers import create_recommendation_snapshot, jwt_login


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
    config.sub2api_usage_log_query_horizon_days = 90
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
    logs = [
        Sub2APIUsageLog(
            id=1,
            user_id=USER_IDS[0],
            account_id=ACCOUNT_ID,
            created_at=window_started_at + timedelta(hours=1),
            service_tier="priority",
            total_cost=Decimal("60"),
            actual_cost=Decimal("60"),
        ),
        Sub2APIUsageLog(
            id=2,
            user_id=USER_IDS[1],
            account_id=ACCOUNT_ID,
            created_at=window_started_at + timedelta(hours=2),
            service_tier="standard",
            total_cost=total - Decimal("60"),
            actual_cost=total - Decimal("60"),
        ),
    ]
    return config, point, observation, users, logs


def _verified_client(logs, *, expected_users=USER_IDS):
    class VerifiedClient:
        calls = 0

        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def usage_log_scan(
            self, *, account_id, started_at, ended_at, timezone_name
        ):
            del timezone_name
            type(self).calls += 1
            selected = tuple(
                row
                for row in logs
                if row.account_id == account_id
                and started_at <= row.created_at < ended_at
            )
            status = "verified" if selected else "verified_empty"
            return UsageLogScan(
                rows=selected,
                started_at=started_at,
                ended_at=ended_at,
                returned_total=len(selected),
                returned_pages=1,
                scanned_pages=1,
                out_of_range_count=0,
                scan_digest=canonical_digest(
                    {"ids": [row.id for row in selected]}
                ),
                evidence_type="test_verified_fixture",
                coverage=tuple(
                    (dimension, "unavailable" if dimension == "api_key" else status)
                    for dimension in (
                        "account_cost",
                        "user_cost",
                        "fast_cost",
                        "request_count",
                        "api_key",
                    )
                ),
                expected_user_ids=expected_users,
            )

    return VerifiedClient


def _policy_only_client(logs):
    verified = _verified_client(logs)

    class PolicyOnlyClient(verified):
        def usage_log_scan(self, **kwargs):
            scan = super().usage_log_scan(**kwargs)
            return UsageLogScan(
                rows=scan.rows,
                started_at=scan.started_at,
                ended_at=scan.ended_at,
                returned_total=scan.returned_total,
                returned_pages=scan.returned_pages,
                scanned_pages=scan.scanned_pages,
                out_of_range_count=scan.out_of_range_count,
                scan_digest=scan.scan_digest,
                evidence_type="sub2api_consistent_pagination",
                coverage=(
                    ("account_cost", "policy_only"),
                    ("user_cost", "policy_only"),
                    ("fast_cost", "policy_only"),
                    ("request_count", "policy_only"),
                    ("api_key", "unavailable"),
                ),
                expected_user_ids=None,
            )

    return PolicyOnlyClient


@pytest.mark.django_db
def test_local_plan_is_persistent_zero_network_and_business_rollback(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, _observation, _users, _logs = _create_complete_history()
    before = source_fact_digest(ACCOUNT_ID)

    class UnexpectedNetworkClient:
        def __init__(self, _config):
            raise AssertionError("local plan/apply must not access Sub2API")

    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        UnexpectedNetworkClient,
    )
    created = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "audit_replay"},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    plan = created.json()["data"]
    assert plan["state"] == "ready"
    assert plan["safe_to_apply"] is True
    assert plan["patch_summary"]["total"] == 0
    assert {row["status"] for row in plan["coverage"]} >= {
        "captured_local",
        "unavailable",
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
    assert applied.json()["data"]["state"] == "applied"
    assert applied.json()["data"]["result_revision"] == 1
    point.refresh_from_db()
    assert point.account_actual_cost == Decimal("100")

    rolled_back = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/rollback",
        **headers,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["data"]["state"] == "rolled_back"
    assert rolled_back.json()["data"]["rollback_revision"] == 2
    assert source_fact_digest(ACCOUNT_ID) == before


@pytest.mark.django_db
def test_verified_remote_plan_patches_exact_facts_apply_is_zero_network_and_rollback_restores(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, observation, users, logs = _create_complete_history(
        total=Decimal("125")
    )
    rebuild_account(ACCOUNT_ID, AppSettings.load())
    before = source_fact_digest(ACCOUNT_ID)
    verified = _verified_client(logs)
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient", verified
    )
    created = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    plan = created.json()["data"]
    assert plan["state"] == "ready"
    assert plan["patch_summary"] == {
        "total": 4,
        "observation_cost": 1,
        "user_cost": 2,
        "fast_fact": 1,
    }
    run = HistoricalRebuildRun.objects.get(pk=plan["id"])
    assert all(patch.before_payload is not None for patch in run.patches.all())
    assert {patch.kind for patch in run.patches.all()} == {
        "observation_cost",
        "user_cost",
        "fast_fact",
    }

    class ApplyMustNotConnect:
        def __init__(self, _config):
            raise AssertionError("apply must use persisted patches only")

    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        ApplyMustNotConnect,
    )
    applied = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert applied.status_code == 200
    point.refresh_from_db()
    observation.refresh_from_db()
    for user in users:
        user.refresh_from_db()
    assert point.account_actual_cost == Decimal("125")
    assert point.fact_revision == 1
    assert [user.total_actual_cost for user in users] == [
        Decimal("60"),
        Decimal("65"),
    ]
    assert observation.fast_correction_request_count == 2
    assert observation.fast_correction_actual_cost == Decimal("15")
    observations_response = client.get("/api/observations", **headers)
    dashboard_response = client.get("/api/dashboard", **headers)
    statistics_response = client.get("/api/statistics", **headers)
    assert observations_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert statistics_response.status_code == 200

    reapplied = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert reapplied.status_code == 200
    assert reapplied.json()["data"]["result_revision"] == 1

    rolled_back = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/rollback",
        **headers,
    )
    assert rolled_back.status_code == 200, rolled_back.json()
    assert source_fact_digest(ACCOUNT_ID) == before


@pytest.mark.django_db
def test_verified_empty_coverage_can_repair_nonzero_facts_to_zero(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, _observation, users, _logs = _create_complete_history()
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        _verified_client([]),
    )
    created = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    )
    plan = created.json()["data"]
    assert created.status_code == 201
    assert plan["state"] == "ready"
    assert any(
        row["status"] == "verified_empty" for row in plan["coverage"]
    )
    response = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200
    point.refresh_from_db()
    for user in users:
        user.refresh_from_db()
    assert point.account_actual_cost == Decimal("0")
    assert [user.total_actual_cost for user in users] == [
        Decimal("0"),
        Decimal("0"),
    ]


@pytest.mark.django_db
def test_current_sub2api_pagination_is_not_treated_as_retention_coverage(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, _point, _observation, _users, logs = _create_complete_history()
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        _policy_only_client(logs),
    )
    response = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201
    plan = response.json()["data"]
    assert plan["state"] == "blocked"
    assert plan["safe_to_apply"] is False
    assert plan["unknown_coverage"] is True
    assert plan["patch_summary"]["total"] == 0
    assert {row["status"] for row in plan["coverage"]} == {
        "policy_only",
        "unavailable",
    }


@pytest.mark.django_db
def test_plan_digest_and_fact_revision_reject_stale_apply(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, _observation, _users, _logs = _create_complete_history()
    created = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "audit_replay"},
        content_type="application/json",
        **headers,
    ).json()["data"]
    point.account_actual_cost = Decimal("101")
    point.save(update_fields=["account_actual_cost"])

    response = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{created['id']}/apply",
        data={"digest": created["digest"]},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 409
    run = HistoricalRebuildRun.objects.get(pk=created["id"])
    assert run.state == "stale"
    assert any(item["code"] == "apply_stale" for item in run.blockers)


@pytest.mark.django_db
def test_typed_patch_failure_rolls_back_the_whole_fact_group(monkeypatch):
    _config, point, _observation, _users, logs = _create_complete_history(
        total=Decimal("125")
    )
    from monitor.historical_rebuild import executor
    from monitor.historical_rebuild.planner import create_rebuild_plan

    run = create_rebuild_plan(
        AppSettings.load(),
        "verified_remote_repair",
        client_factory=_verified_client(logs),
    )
    before = source_fact_digest(ACCOUNT_ID)
    original = executor.apply_typed_payload
    calls = 0

    def fail_after_first(patch, payload):
        nonlocal calls
        calls += 1
        original(patch, payload)
        if calls == 1:
            raise RuntimeError("fault injection after first typed patch")

    monkeypatch.setattr(executor, "apply_typed_payload", fail_after_first)
    with pytest.raises(RuntimeError, match="fault injection"):
        apply_rebuild_plan(run.id, run.plan_digest)
    assert source_fact_digest(ACCOUNT_ID) == before
    run.refresh_from_db()
    point.refresh_from_db()
    assert run.state == "ready"
    assert point.fact_revision == 0


@pytest.mark.django_db
def test_fence_token_rejects_an_expired_owner_after_reacquire():
    _config, _point, _observation, _users, _logs = _create_complete_history()
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
def test_rollback_is_strictly_lifo(admin_client):
    client, headers = admin_client
    _create_complete_history()

    def create_and_apply():
        plan = client.post(
            "/api/settings/data-maintenance/history-rebuild-plans",
            data={"mode": "audit_replay"},
            content_type="application/json",
            **headers,
        ).json()["data"]
        applied = client.post(
            f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
            data={"digest": plan["digest"]},
            content_type="application/json",
            **headers,
        )
        assert applied.status_code == 200
        return plan

    first = create_and_apply()
    second = create_and_apply()
    refused = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{first['id']}/rollback",
        **headers,
    )
    assert refused.status_code == 409
    assert client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{second['id']}/rollback",
        **headers,
    ).status_code == 200
    assert client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{first['id']}/rollback",
        **headers,
    ).status_code == 200


@pytest.mark.django_db
def test_remote_plan_blocks_unexpected_users_in_verified_logs(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, _observation, _users, logs = _create_complete_history()
    logs.append(
        Sub2APIUsageLog(
            id=9,
            user_id=99,
            account_id=ACCOUNT_ID,
            created_at=point.window_started_at + timedelta(minutes=30),
            service_tier="standard",
            total_cost=Decimal("1"),
            actual_cost=Decimal("1"),
        )
    )
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        _verified_client(logs),
    )
    response = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201
    plan = response.json()["data"]
    assert plan["state"] == "blocked"
    assert any(
        item["code"] == "candidate_generation_failed"
        for item in plan["blockers"]
    )


@pytest.mark.django_db
def test_api_clean_cutover_requires_new_modes_and_digest(admin_client):
    client, headers = admin_client
    _create_complete_history()
    unknown = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "force"},
        content_type="application/json",
        **headers,
    )
    assert unknown.status_code == 400
    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "audit_replay"},
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
        "/api/settings/data-maintenance/history-rebuild", **headers
    ).status_code == 404
    assert client.post(
        "/api/settings/data-maintenance/history-rebuild-preview", **headers
    ).status_code == 404


@pytest.mark.django_db
def test_mutating_persisted_patch_invalidates_plan_digest(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, _point, _observation, _users, logs = _create_complete_history(
        total=Decimal("125")
    )
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        _verified_client(logs),
    )
    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    ).json()["data"]
    run = HistoricalRebuildRun.objects.get(pk=plan["id"])
    patch = run.patches.order_by("sequence").first()
    changed = dict(patch.after_payload)
    changed["account_actual_cost"] = "999.000000"
    patch.after_payload = changed
    patch.save(update_fields=["after_payload"])

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
def test_rollback_preserves_complete_points_created_after_plan_cutoff(
    monkeypatch, admin_client
):
    client, headers = admin_client
    _config, point, _observation, users, logs = _create_complete_history(
        total=Decimal("125")
    )
    monkeypatch.setattr(
        "monitor.historical_rebuild.planner.Sub2APIClient",
        _verified_client(logs),
    )
    plan = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data={"mode": "verified_remote_repair"},
        content_type="application/json",
        **headers,
    ).json()["data"]
    assert client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/apply",
        data={"digest": plan["digest"]},
        content_type="application/json",
        **headers,
    ).status_code == 200

    observed_at = point.observed_at + timedelta(hours=1)
    later = UsageSamplePoint.objects.create(
        account_id=ACCOUNT_ID,
        observed_at=observed_at,
        window_started_at=point.window_started_at,
        window_ended_at=observed_at,
        window_resets_at=point.window_resets_at,
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=Decimal("125"),
        account_actual_cost=Decimal("125"),
        interval_started_at=point.observed_at,
        interval_standard_cost=Decimal("0"),
        interval_actual_cost=Decimal("0"),
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=2,
        expected_user_digest=expected_user_digest(USER_IDS),
        write_status="complete",
        reconciliation_status="reconciled",
        provenance={"source": "post_cutoff_capture"},
        fact_revision=2,
    )
    for user in users:
        user.refresh_from_db()
        Sub2APIUserUsageSample.objects.create(
            sample_point=later,
            account_id=ACCOUNT_ID,
            sub2api_user_id=user.sub2api_user_id,
            username=user.username,
            email=user.email,
            observed_at=observed_at,
            window_started_at=point.window_started_at,
            window_ended_at=observed_at,
            window_resets_at=point.window_resets_at,
            total_standard_cost=user.total_standard_cost,
            total_actual_cost=user.total_actual_cost,
            interval_started_at=point.observed_at,
            interval_standard_cost=Decimal("0"),
            interval_actual_cost=Decimal("0"),
            interval_source="snapshot_delta",
        )
    bump_fact_revision(ACCOUNT_ID)

    rolled_back = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{plan['id']}/rollback",
        **headers,
    )

    assert rolled_back.status_code == 200, rolled_back.json()
    assert UsageSamplePoint.objects.filter(pk=later.pk).exists()
    assert later.user_samples.count() == 2
    assert rolled_back.json()["data"]["rollback_revision"] == 3


@pytest.mark.django_db
def test_negative_residual_is_a_hard_local_audit_blocker(admin_client):
    client, headers = admin_client
    _config, point, observation, _users, _logs = _create_complete_history()
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
        data={"mode": "audit_replay"},
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
def test_remote_generation_error_persists_failed_plan():
    _create_complete_history()
    from monitor.historical_rebuild.planner import create_rebuild_plan

    class FailingClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            raise Sub2APIError("verified fixture unavailable")

        def __exit__(self, *_args):
            return None

    with pytest.raises(Sub2APIError, match="fixture unavailable"):
        create_rebuild_plan(
            AppSettings.load(),
            "verified_remote_repair",
            client_factory=FailingClient,
        )

    run = HistoricalRebuildRun.objects.get()
    assert run.state == "failed"
    assert run.plan_digest
    assert run.blockers == [
        {
            "code": "plan_generation_failed",
            "severity": "hard",
            "point_id": None,
            "message": "verified fixture unavailable",
        }
    ]



@pytest.mark.django_db
def test_local_audit_rejects_user_interval_and_observation_coordinate_drift():
    config, point, _observation, users, _logs = _create_complete_history()
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

    run = create_rebuild_plan(AppSettings.load(), "audit_replay")

    assert run.state == "blocked"
    codes = {item["code"] for item in run.blockers if item["severity"] == "hard"}
    assert "user_interval_discontinuity" in codes
    assert "user_interval_total_mismatch" in codes
    assert "interval_user_residual_mismatch" in codes
    assert "observation_point_window_mismatch" in codes


@pytest.mark.django_db
def test_remote_explicit_range_marks_healthy_old_points_out_of_scope():
    config, recent, _observation, users, logs = _create_complete_history(
        total=Decimal("125")
    )
    observed_at = recent.observed_at - timedelta(days=120)
    started_at = observed_at - timedelta(hours=4)
    reset_at = observed_at + timedelta(days=6)
    old = UsageSamplePoint.objects.create(
        account_id=ACCOUNT_ID,
        observed_at=observed_at,
        window_started_at=started_at,
        window_ended_at=observed_at,
        window_resets_at=reset_at,
        capture_started_at=observed_at - timedelta(seconds=1),
        capture_finished_at=observed_at + timedelta(seconds=1),
        account_standard_cost=Decimal("125"),
        account_actual_cost=Decimal("125"),
        interval_started_at=started_at,
        interval_standard_cost=Decimal("125"),
        interval_actual_cost=Decimal("125"),
        residual_standard_cost=Decimal("0"),
        residual_actual_cost=Decimal("0"),
        expected_user_count=2,
        expected_user_digest=expected_user_digest(USER_IDS),
        write_status="complete",
        reconciliation_status="reconciled",
        provenance={"source": "old_complete_fixture"},
    )
    for user in users:
        Sub2APIUserUsageSample.objects.create(
            sample_point=old,
            account_id=ACCOUNT_ID,
            sub2api_user_id=user.sub2api_user_id,
            username=user.username,
            email=user.email,
            observed_at=observed_at,
            window_started_at=started_at,
            window_ended_at=observed_at,
            window_resets_at=reset_at,
            total_standard_cost=user.total_standard_cost,
            total_actual_cost=user.total_actual_cost,
            interval_started_at=started_at,
            interval_standard_cost=user.total_standard_cost,
            interval_actual_cost=user.total_actual_cost,
            interval_source="window_total",
        )

    run = create_rebuild_plan(
        AppSettings.load(),
        "verified_remote_repair",
        started_at=recent.window_started_at,
        ended_at=recent.observed_at + timedelta(microseconds=1),
        client_factory=_verified_client(logs),
    )

    assert run.state == "ready", run.blockers
    assert set(
        run.coverage_rows.filter(point=old).values_list("status", flat=True)
    ) == {"out_of_scope"}
    assert not any(
        item.get("point_id") == old.id
        and item.get("code") == "coverage_not_verified"
        for item in run.blockers
    )


@pytest.mark.django_db
def test_rollback_rechecks_lifo_after_acquiring_lease(monkeypatch):
    config, _point, _observation, _users, _logs = _create_complete_history()
    older = create_rebuild_plan(AppSettings.load(), "audit_replay")
    assert older.state == "ready", older.blockers
    apply_rebuild_plan(older.id, older.plan_digest)
    newer = create_rebuild_plan(AppSettings.load(), "audit_replay")
    rollback_module = importlib.import_module(
        "monitor.historical_rebuild.rollback"
    )
    real_acquire = LeaseGuard.acquire

    class RacingLeaseGuard:
        @classmethod
        def acquire(cls, account_id, *, ttl):
            del cls
            apply_rebuild_plan(newer.id, newer.plan_digest)
            return real_acquire(account_id, ttl=ttl)

    monkeypatch.setattr(rollback_module, "LeaseGuard", RacingLeaseGuard)

    with pytest.raises(
        HistoricalRebuildConflict,
        match="逆序回滚最近一次维护",
    ):
        rollback_module.rollback_rebuild_plan(older.id)

    older.refresh_from_db()
    newer.refresh_from_db()
    assert older.state == "applied"
    assert newer.state == "applied"


@pytest.mark.django_db
def test_rollback_rejects_mutated_journal_even_after_later_revision():
    config, _point, _observation, _users, logs = _create_complete_history(
        total=Decimal("125")
    )
    run = create_rebuild_plan(
        AppSettings.load(),
        "verified_remote_repair",
        client_factory=_verified_client(logs),
    )
    assert run.state == "ready", run.blockers
    apply_rebuild_plan(run.id, run.plan_digest)
    patch = run.patches.order_by("sequence").first()
    patch.before_payload = patch.after_payload
    patch.save(update_fields=["before_payload"])
    bump_fact_revision(ACCOUNT_ID)

    with pytest.raises(HistoricalRebuildConflict, match="计划内容已发生变化"):
        rollback_rebuild_plan(run.id)

    run.refresh_from_db()
    assert run.state == "applied"


@pytest.mark.django_db
def test_rollback_rejects_appended_journal_rows():
    config, _point, _observation, _users, logs = _create_complete_history(
        total=Decimal("125")
    )
    run = create_rebuild_plan(
        AppSettings.load(),
        "verified_remote_repair",
        client_factory=_verified_client(logs),
    )
    assert run.state == "ready", run.blockers
    apply_rebuild_plan(run.id, run.plan_digest)
    patch = run.patches.order_by("sequence").first()
    HistoricalRebuildPatch.objects.create(
        run=run,
        sequence=run.patches.count() + 1,
        kind=patch.kind,
        sample_point=patch.sample_point,
        observation=patch.observation,
        user_sample=patch.user_sample,
        sub2api_user_id=patch.sub2api_user_id,
        natural_key=patch.natural_key,
        schema_version=patch.schema_version,
        before_payload=patch.after_payload,
        after_payload=patch.after_payload,
        required_coverage_ids=patch.required_coverage_ids,
    )
    bump_fact_revision(ACCOUNT_ID)

    with pytest.raises(HistoricalRebuildConflict, match="计划内容已发生变化"):
        rollback_rebuild_plan(run.id)


@pytest.mark.django_db
def test_admin_balance_event_advances_revision_and_survives_rollback(
    monkeypatch,
    admin_client,
):
    client, headers = admin_client
    config, _point, observation, _users, logs = _create_complete_history(
        total=Decimal("125")
    )
    participant = Participant.objects.get(sub2api_user_id=USER_IDS[0])
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("60"),
        selected_cost=Decimal("60"),
    )
    snapshot.current_balance_usd = Decimal("80")
    snapshot.recommended_balance_usd = Decimal("123.45")
    snapshot.balance_difference_usd = Decimal("43.45")
    snapshot.needs_manual_update = True
    snapshot.recommendation_applied = False
    snapshot.save(
        update_fields=[
            "current_balance_usd",
            "recommended_balance_usd",
            "balance_difference_usd",
            "needs_manual_update",
            "recommendation_applied",
        ]
    )
    run = create_rebuild_plan(
        AppSettings.load(),
        "verified_remote_repair",
        client_factory=_verified_client(logs),
    )
    apply_rebuild_plan(run.id, run.plan_digest)
    snapshot.refresh_from_db()
    confirmed_balance = snapshot.recommended_balance_usd
    assert confirmed_balance is not None

    class BalanceClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_user_balance_from_recommendation(self, user_id, balance):
            assert user_id == participant.sub2api_user_id
            assert balance == confirmed_balance
            return balance

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        BalanceClient,
    )
    applied_balance = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )
    assert applied_balance.status_code == 200
    state = HistoryMaintenanceState.objects.get(account_id=ACCOUNT_ID)
    assert state.fact_revision == 2

    rolled_back = client.post(
        f"/api/settings/data-maintenance/history-rebuild-plans/{run.id}/rollback",
        **headers,
    )

    assert rolled_back.status_code == 200, rolled_back.json()
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.recommendation_applied is True
    assert snapshot.current_balance_usd == confirmed_balance
    assert participant.latest_balance_usd == confirmed_balance
    assert rolled_back.json()["data"]["rollback_revision"] == 3


@pytest.mark.django_db(transaction=True)
def test_ninety_day_remote_scan_streams_rows_and_reuses_daily_chunks():
    window_started_at = (
        timezone.now().replace(microsecond=0) - timedelta(days=90)
    )
    cache = {}
    calls = 0

    class StreamingClient:
        def usage_log_scan(
            self,
            *,
            account_id,
            started_at,
            ended_at,
            timezone_name,
            row_consumer,
            collect_rows,
        ):
            nonlocal calls
            del timezone_name
            assert connection.in_atomic_block is False
            assert collect_rows is False
            calls += 1
            day_index = int(
                (started_at - window_started_at).total_seconds()
                // timedelta(days=1).total_seconds()
            )
            for index in range(1000):
                row_consumer(
                    Sub2APIUsageLog(
                        id=(day_index * 1000) + index + 1,
                        user_id=USER_IDS[0],
                        account_id=account_id,
                        created_at=started_at + timedelta(seconds=index),
                        service_tier=("priority" if index % 5 == 0 else ""),
                        total_cost=Decimal("0.001"),
                        actual_cost=Decimal("0.0008"),
                    )
                )
            return UsageLogScan(
                rows=(),
                started_at=started_at,
                ended_at=ended_at,
                returned_total=1000,
                returned_pages=1,
                scanned_pages=1,
                out_of_range_count=0,
                scan_digest=canonical_digest(
                    {
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "rows": 1000,
                    }
                ),
                evidence_type="streaming_fixture",
                coverage=tuple(
                    (dimension, "verified")
                    for dimension in (
                        "account_cost",
                        "user_cost",
                        "fast_cost",
                        "request_count",
                        "api_key",
                    )
                ),
                expected_user_ids=USER_IDS,
            )

    client = StreamingClient()
    tracemalloc.start()
    for day in range(1, 91):
        point = UsageSamplePoint(
            id=day,
            account_id=ACCOUNT_ID,
            observed_at=window_started_at + timedelta(days=day),
            window_started_at=window_started_at,
            window_ended_at=window_started_at + timedelta(days=day),
            window_resets_at=window_started_at + timedelta(days=91),
            interval_started_at=window_started_at + timedelta(days=day - 1),
        )
        aggregate, statuses, _kind, _digest, expected_users = _scan_point(
            client,
            point=point,
            started_at=window_started_at,
            ended_at=point.observed_at,
            timezone_name="UTC",
            cache=cache,
        )
        assert aggregate.total.request_count == day * 1000
        assert aggregate.interval.request_count == 1000
        assert set(statuses.values()) == {"verified"}
        assert expected_users == USER_IDS
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert calls == 90
    assert len(cache) == 90
    assert peak < 16 * 1024 * 1024