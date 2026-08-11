import json
import sqlite3
from io import BytesIO, StringIO

from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from monitor.engine import run_monitor
from monitor.management.commands.runmonitor import schedule_next_run
from monitor.models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    NotificationEvent,
    HistoryMaintenanceState,
    ParticipantBalanceOperation,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.notifications import send_notification
from monitor.replay import (
    RATE_METHOD,
    exclude_observation,
    rebuild_account,
    rebuild_observation_suffix,
)
from monitor.secrets import encrypt_secret
from monitor.integrations.sub2api import (
    Sub2APIClient,
    Sub2APIError,
    Sub2APIUserUsage,
    Sub2APIUsageLog,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor import database_transfer
from monitor.tests.helpers import create_recommendation_snapshot, jwt_login

@pytest.mark.django_db
def test_dashboard_only_lists_participants_that_need_manual_adjustment():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    actionable = Participant.objects.create(
        name="需要调整",
        sub2api_user_id=51,
        share_percent=50,
    )
    settled = Participant.objects.create(
        name="当前无需调整",
        sub2api_user_id=52,
        share_percent=40,
    )
    Participant.objects.create(
        name="等待测算",
        sub2api_user_id=53,
        share_percent=10,
    )
    create_recommendation_snapshot(actionable)
    settled_snapshot = create_recommendation_snapshot(settled)
    settled_snapshot.needs_manual_update = False
    settled_snapshot.save(update_fields=["needs_manual_update"])
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers)

    assert dashboard.status_code == 200
    assert [
        item["id"] for item in dashboard.json()["data"]["participants"]
    ] == [actionable.id]


@pytest.mark.django_db
def test_usage_deviation_is_not_a_dashboard_suggestion_and_can_clear():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.weekly_quota_model = "time_varying"
    config.save()
    confirmed = Participant.objects.create(
        name="确认偏差",
        sub2api_user_id=61,
        share_percent=40,
    )
    uncertain = Participant.objects.create(
        name="尚不确定",
        sub2api_user_id=62,
        share_percent=40,
    )
    now = timezone.now()
    starts_at = now - timedelta(days=4)
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=starts_at,
        upstream_used_percent=Decimal("50"),
        interval_used_percent=Decimal("50"),
        raw_selected_total_cost=Decimal("1000"),
        selected_total_cost=Decimal("1000"),
        total_standard_cost=Decimal("1000"),
        total_actual_cost=Decimal("1000"),
        effective_usd_per_percent=Decimal("20"),
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=confirmed,
        raw_selected_cost=Decimal("860"),
        selected_cost=Decimal("860"),
        charged_cycle_percent=Decimal("43"),
        charged_percent_lower=Decimal("41"),
        charged_percent_upper=Decimal("45"),
        remaining_share_percent=Decimal("0"),
        needs_manual_update=False,
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=uncertain,
        raw_selected_cost=Decimal("820"),
        selected_cost=Decimal("820"),
        charged_cycle_percent=Decimal("41"),
        charged_percent_lower=Decimal("39"),
        charged_percent_upper=Decimal("43"),
        remaining_share_percent=Decimal("0"),
        needs_manual_update=False,
    )
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["participants"] == []
    visible = client.get("/api/participants", **headers).json()["data"]
    confirmed_snapshot = next(
        item["snapshot"] for item in visible if item["id"] == confirmed.id
    )
    uncertain_snapshot = next(
        item["snapshot"] for item in visible if item["id"] == uncertain.id
    )
    assert confirmed_snapshot["is_overused"] is True
    assert confirmed_snapshot["overused_percent"] == 3.0
    assert confirmed_snapshot["overused_percent_min"] == 1.0
    assert confirmed_snapshot["overused_percent_max"] == 5.0
    assert uncertain_snapshot["is_overused"] is False

    revised = Observation.objects.create(
        account_id=7,
        observed_at=now + timedelta(minutes=5),
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=starts_at,
        upstream_used_percent=Decimal("51"),
        interval_used_percent=Decimal("51"),
        raw_selected_total_cost=Decimal("1020"),
        selected_total_cost=Decimal("1020"),
        total_standard_cost=Decimal("1020"),
        total_actual_cost=Decimal("1020"),
        effective_usd_per_percent=Decimal("21"),
    )
    ParticipantSnapshot.objects.create(
        observation=revised,
        participant=confirmed,
        raw_selected_cost=Decimal("870"),
        selected_cost=Decimal("870"),
        charged_cycle_percent=Decimal("39"),
        charged_percent_lower=Decimal("38"),
        charged_percent_upper=Decimal("40"),
        remaining_share_percent=Decimal("1"),
        needs_manual_update=False,
    )

    revised_visible = client.get("/api/participants", **headers).json()["data"]
    revised_snapshot = next(
        item["snapshot"]
        for item in revised_visible
        if item["id"] == confirmed.id
    )
    assert revised_snapshot["is_overused"] is False



@pytest.mark.django_db
def test_dashboard_uses_particle_filter_residual_attribution():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.weekly_quota_model = "time_varying"
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
    )
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=now - timedelta(days=4),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
        estimated_used_percent=Decimal("20"),
        model_diagnostics={"residual_attributed_percent": 7.25},
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("200"),
        selected_cost=Decimal("200"),
        charged_cycle_percent=Decimal("12"),
        remaining_share_percent=Decimal("38"),
    )
    client = Client()
    headers, _ = jwt_login(client)

    dashboard = client.get("/api/dashboard", **headers)

    assert dashboard.status_code == 200
    assert (
        dashboard.json()["data"]["cycle"]["unattributed_used_percent"]
        == 7.25
    )
@pytest.mark.django_db
def test_constant_average_model_changes_only_presented_attribution():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.weekly_quota_model = "constant_average"
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=4),
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("15"),
    )
    stored = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        charged_cycle_percent=Decimal("12"),
        remaining_share_percent=Decimal("38"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=Decimal("722"),
        needs_manual_update=True,
    )
    client = Client()
    headers, _ = jwt_login(client)

    constant = client.get("/api/participants", **headers).json()["data"][0]

    assert constant["snapshot"]["allocation_model"] == "constant_average"
    assert constant["snapshot"]["charged_cycle_percent"] == 5.0
    assert constant["snapshot"]["remaining_share_percent"] == 45.0
    assert constant["snapshot"]["recommended_balance_usd"] == 834.55
    assert constant["snapshot"]["recommended_balance_min_usd"] == 814.09
    assert constant["snapshot"]["recommended_balance_max_usd"] == 855.0
    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["weekly_quota_model"] == "constant_average"
    assert dashboard["cycle"]["effective_usd_per_percent"] == 20.0
    assert dashboard["cycle"]["interval_used_percent"] == 20.0
    assert dashboard["cycle"]["rate_calculated"] is True
    assert dashboard["cycle"]["estimated_used_percent"] == 20.0
    assert dashboard["cycle"]["unattributed_used_percent"] == 15.0
    assert dashboard["participants"][0]["snapshot"][
        "recommended_balance_usd"
    ] == 834.55
    assert dashboard["participants"][0]["snapshot"][
        "recommended_balance_min_usd"
    ] == 814.09
    assert dashboard["participants"][0]["snapshot"][
        "recommended_balance_max_usd"
    ] == 855.0
    stored.refresh_from_db()
    assert stored.charged_cycle_percent == Decimal("12")
    assert stored.recommended_balance_usd == Decimal("722")

    config.weekly_quota_model = "time_varying"
    config.save(update_fields=["weekly_quota_model"])
    time_varying = client.get(
        "/api/participants",
        **headers,
    ).json()["data"][0]
    assert time_varying["snapshot"]["allocation_model"] == "time_varying"
    assert time_varying["snapshot"]["charged_cycle_percent"] == 12.0
    time_varying_dashboard = client.get(
        "/api/dashboard",
        **headers,
    ).json()["data"]
    assert (
        time_varying_dashboard["cycle"]["effective_usd_per_percent"] == 15.0
    )
    assert time_varying_dashboard["cycle"]["rate_calculated"] is False
    assert time_varying_dashboard["participants"][0]["snapshot"][
        "recommended_balance_usd"
    ] == 722.0
    assert time_varying_dashboard["participants"][0]["snapshot"][
        "recommended_balance_min_usd"
    ] is None
    assert time_varying_dashboard["participants"][0]["snapshot"][
        "recommended_balance_max_usd"
    ] is None

@pytest.mark.django_db(transaction=True)
def test_apply_recommendation_updates_balance_and_hides_current_snapshot(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_base_url = "https://admin.example:8443/internal/path"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    captured: dict = {}

    class FakeClient:
        def __init__(self, received_config):
            assert received_config.pk == config.pk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            assert connection.in_atomic_block is False
            captured.update(user_id=user_id, balance=balance)
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _ = jwt_login(client)

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert applied.status_code == 200
    assert applied.json()["data"]["applied_balance_usd"] == 123.45
    assert captured == {"user_id": 51, "balance": Decimal("123.45")}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.recommendation_applied is True
    assert snapshot.needs_manual_update is False
    assert snapshot.current_balance_usd == Decimal("123.45")
    assert snapshot.balance_difference_usd == Decimal("0")
    assert participant.latest_balance_usd == Decimal("123.45")
    state = HistoryMaintenanceState.objects.get(account_id=7)
    assert state.fact_revision == 1

    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["sub2api_admin_url"] == "https://admin.example:8443"
    assert dashboard["participants"] == []


@pytest.mark.django_db(transaction=True)
def test_balance_rpc_blocks_concurrent_participant_policy_write(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    client = Client()
    headers, _ = jwt_login(client)
    concurrent_status: list[int] = []

    class RacingClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_user_balance_from_recommendation(self, _user_id, balance):
            concurrent = Client().put(
                f"/api/participants/{participant.id}",
                data=json.dumps({"share_percent": "40"}),
                content_type="application/json",
                **headers,
            )
            concurrent_status.append(concurrent.status_code)
            return balance

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        RacingClient,
    )

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert concurrent_status == [409]
    assert applied.status_code == 200, applied.json()
    participant.refresh_from_db()
    snapshot.refresh_from_db()
    assert participant.share_percent == Decimal("50")
    assert snapshot.recommendation_applied is True


@pytest.mark.django_db(transaction=True)
def test_remote_success_survives_local_commit_failure_and_retries_idempotently(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    remote_calls: list[Decimal] = []

    class BalanceClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_user_balance_from_recommendation(self, _user_id, balance):
            remote_calls.append(balance)
            return balance

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        BalanceClient,
    )
    from monitor.views import dashboard as dashboard_view

    real_commit = dashboard_view._commit_balance_operation

    def fail_local_commit(_operation_id, _guard):
        raise DatabaseError("injected local commit failure")

    monkeypatch.setattr(
        dashboard_view,
        "_commit_balance_operation",
        fail_local_commit,
    )
    client = Client()
    headers, _ = jwt_login(client)

    first = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert first.status_code == 503, first.json()
    operation = ParticipantBalanceOperation.objects.get()
    assert operation.state == "remote_confirmed"
    assert operation.confirmed_balance_usd == Decimal("123.450000")
    snapshot.refresh_from_db()
    assert snapshot.recommendation_applied is False
    monkeypatch.setattr(
        dashboard_view,
        "_commit_balance_operation",
        real_commit,
    )

    class NoNetworkClient:
        def __init__(self, _config):
            raise AssertionError("remote-confirmed retry must not call Sub2API")

    monkeypatch.setattr(
        dashboard_view,
        "Sub2APIClient",
        NoNetworkClient,
    )
    retried = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert retried.status_code == 200, retried.json()
    operation.refresh_from_db()
    snapshot.refresh_from_db()
    assert operation.state == "committed"
    assert snapshot.recommendation_applied is True
    assert remote_calls == [Decimal("123.45")]


@pytest.mark.django_db(transaction=True)
def test_ambiguous_remote_failure_reconciles_before_idempotent_retry(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    remote_balance = {"value": Decimal("80")}
    calls = {"set": 0, "read": 0}

    class AmbiguousClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def user_balance(self, _user_id):
            calls["read"] += 1
            return UserBalance(remote_balance["value"], Decimal("0"))

        def set_user_balance_from_recommendation(self, _user_id, balance):
            calls["set"] += 1
            remote_balance["value"] = balance
            if calls["set"] == 1:
                raise Sub2APIError("connection lost after remote commit")
            return balance

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        AmbiguousClient,
    )
    client = Client()
    headers, _ = jwt_login(client)

    first = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert first.status_code == 502, first.json()
    operation = ParticipantBalanceOperation.objects.get()
    assert operation.state == "reconciliation_required"
    blocked_write = client.put(
        f"/api/participants/{participant.id}",
        data=json.dumps({"share_percent": "40"}),
        content_type="application/json",
        **headers,
    )
    assert blocked_write.status_code == 409

    retried = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert retried.status_code == 200, retried.json()
    operation.refresh_from_db()
    snapshot.refresh_from_db()
    assert operation.state == "committed"
    assert snapshot.recommendation_applied is True
    assert calls == {"set": 1, "read": 1}

@pytest.mark.django_db
def test_constant_average_one_click_applies_recommendation_midpoint(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.weekly_quota_model = "constant_average"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=4),
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("15"),
    )
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=Decimal("722"),
        needs_manual_update=True,
    )
    captured: dict = {}

    class FakeClient:
        def __init__(self, received_config):
            assert received_config.pk == config.pk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            captured.update(user_id=user_id, balance=balance)
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _ = jwt_login(client)

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert applied.status_code == 200
    assert applied.json()["data"]["applied_balance_usd"] == 834.55
    assert captured == {"user_id": 51, "balance": Decimal("834.55")}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.current_balance_usd == Decimal("834.55")
    assert participant.latest_balance_usd == Decimal("834.55")

@pytest.mark.django_db
def test_apply_recommendation_failure_keeps_snapshot_actionable(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])
    snapshot = create_recommendation_snapshot(participant)

    class FailingClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, _user_id, _balance):
            raise Sub2APIError("上游拒绝更新")

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        FailingClient,
    )
    client = Client()
    headers, _ = jwt_login(client)

    failed = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert failed.status_code == 502
    assert failed.json()["message"] == "上游拒绝更新"
    snapshot.refresh_from_db()
    assert snapshot.recommendation_applied is False
    assert snapshot.needs_manual_update is True

@pytest.mark.django_db
def test_initial_observation_respects_capacity_bounds_and_builds_recommendations(monkeypatch):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.quota_query_mode = "passive"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = Participant.objects.create(name="车主", sub2api_user_id=1, share_percent=50, is_owner=True)
    rider = Participant.objects.create(name="车友", sub2api_user_id=2, share_percent=50)
    reset_at = datetime(2026, 8, 14, tzinfo=datetime_timezone.utc)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, account_id, mode):
            assert account_id == 7
            assert mode == "passive"
            return WeeklyWindow(Decimal("40"), 604800, 345600, int(reset_at.timestamp()), "passive_snapshot")

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("400"), 1: Decimal("300"), 2: Decimal("100")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("300") if user_id == 1 else Decimal("100")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    owner_snapshot = snapshots[owner.id]
    rider_snapshot = snapshots[rider.id]
    assert owner_snapshot.charged_cycle_percent > rider_snapshot.charged_cycle_percent
    assert float(
        owner_snapshot.charged_cycle_percent
        + rider_snapshot.charged_cycle_percent
    ) == pytest.approx(40, abs=1)
    assert (
        owner_snapshot.charged_percent_lower
        <= owner_snapshot.charged_cycle_percent
        <= owner_snapshot.charged_percent_upper
    )
    assert (
        rider_snapshot.charged_percent_lower
        <= rider_snapshot.charged_cycle_percent
        <= rider_snapshot.charged_percent_upper
    )
    assert owner_snapshot.recommended_balance_usd is not None
    assert rider_snapshot.recommended_balance_usd is not None
    assert ParticipantUsageSample.objects.count() == 2


@pytest.mark.django_db
def test_constant_average_removes_safety_factor_for_only_remaining_participant():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.weekly_quota_model = "constant_average"
    config.safety_factor = Decimal("0.5")
    config.save()
    exhausted = Participant.objects.create(
        name="已跑完",
        sub2api_user_id=71,
        share_percent=50,
    )
    remaining = Participant.objects.create(
        name="唯一剩余",
        sub2api_user_id=72,
        share_percent=50,
    )
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=now - timedelta(days=4),
        upstream_used_percent=Decimal("80"),
        interval_used_percent=Decimal("80"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("5"),
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=exhausted,
        raw_selected_cost=Decimal("300"),
        selected_cost=Decimal("300"),
        current_balance_usd=Decimal("5"),
    )
    ParticipantSnapshot.objects.create(
        observation=observation,
        participant=remaining,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        current_balance_usd=Decimal("0"),
    )
    client = Client()
    headers, _ = jwt_login(client)

    rows = client.get("/api/participants", **headers).json()["data"]
    exhausted_snapshot = next(
        item["snapshot"] for item in rows if item["id"] == exhausted.id
    )
    assert exhausted_snapshot["remaining_share_percent"] == 0.0
    assert exhausted_snapshot["recommended_balance_usd"] == 0.0
    assert exhausted_snapshot["recommended_balance_min_usd"] == 0.0
    assert exhausted_snapshot["recommended_balance_max_usd"] == 0.0
    assert exhausted_snapshot["needs_manual_update"] is True
    assert exhausted_snapshot["reason"] == (
        "百分比权益已用尽，建议清零 Sub2API 用户余额"
    )
    snapshot = next(
        item["snapshot"] for item in rows if item["id"] == remaining.id
    )

    assert snapshot["remaining_share_percent"] == 30.0
    assert snapshot["recommended_balance_min_usd"] == 147.22
    assert snapshot["recommended_balance_max_usd"] == 150.0
    assert snapshot["recommended_balance_usd"] == 148.61
    assert snapshot["reason"] == (
        "当前 Sub2API 用户余额接近耗尽，但仍有百分比权益"
    )
