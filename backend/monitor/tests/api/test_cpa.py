from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from monitor.cpa.collector_state import mark_collector_error
from monitor.cpa.usage import cpa_event_cost
from monitor.integrations.cpa import CPAError
from monitor.integrations.sub2api.dto import WeeklyWindow
from monitor.models import (
    AccountParticipant,
    AppSettings,
    CPAUsageEvent,
    MonitoredAccount,
    Observation,
)
from monitor.secrets import encrypt_secret
from monitor.tests.helpers import create_participant, jwt_login


@pytest.mark.django_db
def test_cpa_pricing_defaults_and_manual_override_persist():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)

    defaults = client.get("/api/settings", **headers).json()["data"]
    assert defaults["cpa_fast_multiplier"] == 2.5
    assert defaults["cpa_double_billing_enabled"] is False
    assert defaults["cpa_double_billing_threshold_tokens"] == 272000
    assert defaults["cpa_double_billing_multiplier"] == 2.0

    custom_pricing = {
        "gpt-custom": {
            "input": "1.75",
            "cached_input": "0.175",
            "output": "14",
        }
    }
    updated = client.patch(
        "/api/settings",
        data=json.dumps({"cpa_model_pricing": custom_pricing}),
        content_type="application/json",
        **headers,
    )

    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["cpa_model_pricing"] == custom_pricing
    assert AppSettings.load().cpa_model_pricing == custom_pricing
    account = MonitoredAccount.objects.create(
        provider="cpa",
        cpa_auth_index="codex-pricing-test",
        name="CPA 价格账号",
    )
    event = CPAUsageEvent.objects.create(
        account=account,
        event_fingerprint="pricing-event",
        occurred_at=timezone.now(),
        model="gpt-custom",
        input_tokens=1_000_000,
        total_tokens=1_000_000,
    )
    changed = client.patch(
        "/api/settings",
        data=json.dumps(
            {
                "cpa_model_pricing": {
                    "gpt-other": {
                        "input": "1",
                        "cached_input": "0.1",
                        "output": "10",
                    }
                }
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert changed.status_code == 200, changed.json()
    latest_config = AppSettings.load()
    assert "gpt-custom" not in latest_config.cpa_model_pricing
    estimated_cost, unknown_model = cpa_event_cost(event, latest_config)
    assert estimated_cost == Decimal("0")
    assert unknown_model is True
    assert "estimated_cost_usd" not in {
        field.name for field in CPAUsageEvent._meta.fields
    }


@pytest.mark.django_db
def test_cpa_connection_test_probes_management_and_resp_paths(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)
    observed_values = []

    class FakeManagementClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def test_connection(self):
            return {
                "auth_files_api": "ok",
                "codex_account_count": 2,
                "usage_statistics_enabled": True,
            }

    class FakeSubscriber:
        def probe(self):
            return {"resp_transport": "ok", "resp_auth": "ok"}

    def temporary_client(_config, values):
        observed_values.append(dict(values))
        return FakeManagementClient()

    def temporary_subscriber(_config, values):
        observed_values.append(dict(values))
        return FakeSubscriber()

    monkeypatch.setattr(
        "monitor.views.settings._temporary_cpa_client",
        temporary_client,
    )
    monkeypatch.setattr(
        "monitor.views.settings._temporary_cpa_subscriber",
        temporary_subscriber,
    )

    response = client.post(
        "/api/settings/test-cpa",
        data=json.dumps(
            {
                "cpa_base_url": "https://unsaved-cpa.example",
                "cpa_management_key": "unsaved-secret",
                "verify_tls": False,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200, response.json()
    assert response.json()["data"] == {
        "auth_files_api": "ok",
        "codex_account_count": 2,
        "usage_statistics_enabled": True,
        "resp_transport": "ok",
        "resp_auth": "ok",
    }
    assert len(observed_values) == 2
    assert all(
        values["cpa_management_key"] == "unsaved-secret"
        for values in observed_values
    )


@pytest.mark.django_db
def test_cpa_collector_health_is_visible_without_public_error_detail():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)
    mark_collector_error(CPAError("connection refused"), pending_count=7)

    settings_response = client.get("/api/settings", **headers)
    public_health = client.get("/api/health")
    collector_status_response = client.get(
        "/api/settings/cpa-collector-status",
        **headers,
    )

    status = settings_response.json()["data"]["cpa_collector_status"]
    assert status["state"] == "error"
    assert status["connected"] is False
    assert status["pending_count"] == 7
    assert status["last_error"] == "CPAError: connection refused"
    assert collector_status_response.status_code == 200
    assert collector_status_response.json()["data"] == status
    public_status = public_health.json()["data"]["cpa_collector"]
    assert public_status["state"] == "error"
    assert "last_error" not in public_status

@pytest.mark.django_db
def test_cpa_account_is_supported_only_on_cpa_account_pages():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = create_participant(name="现有参与者", sub2api_user_id=91)
    client = Client()
    headers, _response = jwt_login(client)

    created = client.post(
        "/api/settings/monitored-accounts",
        data=json.dumps(
            {
                "provider": "cpa",
                "cpa_auth_index": "codex-auth-api-test",
                "name": "CPA 主账号",
                "enabled": True,
                "quota_profile": "pro_5x",
                "capacity_min_usd_override": 600,
                "capacity_max_usd_override": 1400,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert created.status_code == 201, created.json()
    account = created.json()["data"]
    assert account["provider"] == "cpa"
    assert account["source_account_id"] == "codex-auth-api-test"
    assert account["external_account_id"] is None
    assert account["quota_query_mode"] == "direct"
    assert account["capacity_min_usd"] == 600.0
    assert account["capacity_max_usd"] == 1400.0
    assert not AccountParticipant.objects.filter(participant=participant).exists()

    account_id = account["id"]
    dashboard = client.get(
        f"/api/dashboard?account_id={account_id}", **headers
    )
    assert dashboard.status_code == 200
    dashboard_data = dashboard.json()["data"]
    assert dashboard_data["selected_provider"] == "cpa"
    assert dashboard_data["participants"] == []
    assert dashboard_data["needs_manual_update_count"] == 0
    assert dashboard_data["fast_correction_enabled"] is False
    assert dashboard_data["cycle"] is None

    statistics = client.get(
        f"/api/statistics?account_id={account_id}", **headers
    )
    assert statistics.status_code == 200, statistics.json()
    statistics_data = statistics.json()["data"]
    assert statistics_data["account"]["provider"] == "cpa"
    assert statistics_data["participant_series"] == []
    assert statistics_data["cpa_api_key_series"] == []

    assert statistics_data["capacity_summary"]["cycle"] is None
    observations = client.get(
        f"/api/observations?account_id={account_id}", **headers
    )
    assert observations.status_code == 200
    assert observations.json()["data"]["account"]["provider"] == "cpa"
    assert observations.json()["data"]["fast_correction_enabled"] is False

    assert observations.json()["data"]["summary"]["total"] == 0

    trajectory = client.get(
        f"/api/particle-trajectory?account_id={account_id}", **headers
    )
    assert trajectory.status_code == 200
    assert trajectory.json()["data"]["available"] is False

    allocation = client.get("/api/quota-allocation", **headers)
    assert allocation.status_code == 200
    assert account_id not in {
        item["id"] for item in allocation.json()["data"]["accounts"]
    }

    maintenance = client.post(
        "/api/settings/data-maintenance/history-rebuild-plans",
        data=json.dumps({"account_id": account_id}),
        content_type="application/json",
        **headers,
    )
    assert maintenance.status_code == 404

    delete_response = client.delete(
        f"/api/settings/monitored-accounts/{account_id}",
        **headers,
    )
    assert delete_response.status_code == 405
    assert MonitoredAccount.objects.filter(pk=account_id).exists()


@pytest.mark.django_db
def test_cpa_account_status_uses_local_cost_without_sub2api_corrections(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)
    account = MonitoredAccount.objects.create(
        provider="cpa",
        cpa_auth_index="codex-status-test",
        name="CPA 状态账号",
    )
    config = AppSettings.load()
    config.cpa_management_key_encrypted = encrypt_secret("management-secret")
    config.cpa_model_pricing = {
        "gpt-test": {
            "input": "1.25",
            "cached_input": "0.125",
            "output": "10",
        }
    }
    config.cpa_fast_multiplier = Decimal("2.5")
    config.save()
    now = timezone.now()
    CPAUsageEvent.objects.create(
        account=account,
        event_fingerprint="status-event",
        occurred_at=now,
        model="gpt-test",
        input_tokens=1_000_000,
        total_tokens=1_000_000,
        requested_service_tier="priority",
    )

    class FakeCPAClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_codex_accounts(self):
            return [
                {
                    "auth_index": account.cpa_auth_index,
                    "name": account.name,
                    "plan_type": "pro",
                    "status": "active",
                    "status_message": "",
                    "disabled": False,
                    "unavailable": False,
                }
            ]

        def query_weekly_window(self, _auth_index):
            reset_at = now + timedelta(days=3)
            return WeeklyWindow(
                used_percent=Decimal("37"),
                window_seconds=604800,
                reset_after_seconds=3 * 86400,
                reset_at=int(reset_at.timestamp()),
                slot="secondary_window",
                sampled_at=now.isoformat(),
                plan_type="pro",
            )

    monkeypatch.setattr(
        "monitor.views.account_status.CPAClient",
        FakeCPAClient,
    )

    response = client.get("/api/account-status", **headers)

    assert response.status_code == 200, response.json()
    row = response.json()["data"]["accounts"][0]
    assert row["provider"] == "cpa"
    assert row["source_account_id"] == "codex-status-test"
    assert row["cycles"] == []
    assert row["usage"]["seven_day"]["account_cost_usd"] == 3.125
    assert row["usage"]["seven_day"]["standard_cost_usd"] is None
    assert row["usage"]["seven_day"]["user_cost_usd"] is None
    assert row["stats"]["account_cost_usd"] == 3.125
    assert row["stats"]["fast_correction_usd"] is None
    assert row["stats"]["account_cost_with_fast_correction_usd"] is None
    assert row["stats"]["standard_cost_usd"] is None
    assert row["stats"]["user_cost_usd"] is None

@pytest.mark.django_db
def test_cpa_observation_rejects_sub2api_fast_correction_actions():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)
    account_response = client.post(
        "/api/settings/monitored-accounts",
        data=json.dumps(
            {
                "provider": "cpa",
                "cpa_auth_index": "codex-fast-guard",
                "name": "CPA FAST guard",
            }
        ),
        content_type="application/json",
        **headers,
    )
    account_id = account_response.json()["data"]["id"]

    account = MonitoredAccount.objects.get(pk=account_id)
    observed_at = timezone.now()
    observation = Observation.objects.create(
        account_id=account.fact_key,
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=observed_at + timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("1"),
        selected_total_cost=Decimal("1"),
        total_standard_cost=Decimal("1"),
        total_actual_cost=Decimal("1"),
        effective_usd_per_percent=Decimal("16"),
        raw_window={"provider": "cpa"},
    )

    detail = client.get(
        f"/api/observations/{observation.id}/fast-correction", **headers
    )
    calculate = client.post(
        f"/api/observations/{observation.id}/fast-correction/calculate",
        **headers,
    )

    assert detail.status_code == 400
    assert calculate.status_code == 400
    assert "CPA" in detail.json()["message"]
    assert "CPA" in calculate.json()["message"]
