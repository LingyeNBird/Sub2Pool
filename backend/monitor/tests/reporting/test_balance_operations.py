import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import Client
from django.utils import timezone

from monitor.integrations.sub2api import Sub2APIError, UserBalance
from monitor.models import (
    AppSettings,
    HistoryMaintenanceState,
    Observation,
    ParticipantBalanceOperation,
)
from monitor.secrets import encrypt_secret
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
    create_recommendation_snapshot,
    jwt_login,
)


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
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
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
    expected = Decimal(
        str(
            client.get("/api/participants", **headers).json()["data"][0][
                "snapshot"
            ]["recommended_balance_usd"]
        )
    )

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert applied.status_code == 200
    assert applied.json()["data"]["applied_balance_usd"] == float(expected)
    assert captured == {"user_id": 51, "balance": expected}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.recommendation_applied is True
    assert snapshot.needs_manual_update is False
    assert snapshot.current_balance_usd == expected
    assert snapshot.balance_difference_usd == Decimal("0")
    assert participant.latest_balance_usd == expected
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
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
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
                data=json.dumps({"notes": "blocked"}),
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
    assert participant.notes == ""
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
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
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
    expected = Decimal(
        str(
            client.get("/api/participants", **headers).json()["data"][0][
                "snapshot"
            ]["recommended_balance_usd"]
        )
    )

    first = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert first.status_code == 503, first.json()
    operation = ParticipantBalanceOperation.objects.get()
    assert operation.state == "remote_confirmed"
    assert operation.confirmed_balance_usd == expected
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
    assert remote_calls == [expected]


@pytest.mark.django_db(transaction=True)
def test_ambiguous_remote_failure_reconciles_before_idempotent_retry(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
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
        data=json.dumps({"notes": "blocked"}),
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
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,
    latest_balance_usd=Decimal("80"),)
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
    snapshot = create_participant_snapshot(observation=observation,
    participant=participant,
    raw_selected_cost=Decimal("100"),
    selected_cost=Decimal("100"),
    current_balance_usd=Decimal("80"),
    recommended_balance_usd=Decimal("722"),
    needs_manual_update=True,)
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
    expected = Decimal(
        str(
            client.get("/api/participants", **headers).json()["data"][0][
                "snapshot"
            ]["recommended_balance_usd"]
        )
    )

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert applied.status_code == 200
    assert applied.json()["data"]["applied_balance_usd"] == float(expected)
    assert captured == {"user_id": 51, "balance": expected}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.current_balance_usd == expected
    assert participant.latest_balance_usd == expected

@pytest.mark.django_db
def test_apply_recommendation_failure_keeps_snapshot_actionable(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=50,)
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
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

