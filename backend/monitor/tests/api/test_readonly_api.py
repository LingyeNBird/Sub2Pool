from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.api_auth import API_KEY_PREFIX, hash_readonly_api_key
from monitor.models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    Observation,
    Participant,
    ParticipantAPIUsageSnapshot,
)
from monitor.tests.helpers import jwt_login


@pytest.mark.django_db
def test_readonly_api_key_lifecycle_and_scope():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    admin_headers, _ = jwt_login(client)
    config = AppSettings.load()

    account = MonitoredAccount.objects.create(
        external_account_id=7,
        name="主账号",
    )
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=22,
        sub2api_email="rider@example.com",
        share_percent=Decimal("40"),
    )
    AccountParticipant.objects.create(
        account=account,
        participant=participant,
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
    )
    ParticipantAPIUsageSnapshot.objects.create(
        participant=participant,
        observation=observation,
        account_id=7,
        attribution_started_at=observation.attribution_started_at,
        observed_at=now,
        cost_basis="actual",
        fast_correction_enabled=True,
        participant_total_usd=Decimal("120"),
        weekly_total_estimate_usd=Decimal("2000"),
        participant_weekly_percent=Decimal("6"),
        api_keys=[
            {
                "api_key_id": 8,
                "name": "主密钥",
                "status": "active",
                "usage_usd": 120.0,
                "participant_usage_percent": 100.0,
                "weekly_quota_percent": 6.0,
            },
            {
                "api_key_id": None,
                "name": "未识别或已删除的 API 密钥",
                "status": "",
                "usage_usd": 0.0,
                "participant_usage_percent": 0.0,
                "weekly_quota_percent": 0.0,
            },
        ],
    )

    assert client.get("/api/v1").status_code == 401
    assert client.get("/api/v1/openapi.json").status_code == 401
    assert client.get("/api/v1/participants").status_code == 401

    generated_response = client.post(
        "/api/settings/readonly-api-key",
        **admin_headers,
    )
    assert generated_response.status_code == 200
    generated = generated_response.json()["data"]
    api_key = generated["api_key"]
    assert api_key.startswith(API_KEY_PREFIX)
    assert len(api_key) >= 90

    config.refresh_from_db()
    assert config.readonly_api_key_hash == hash_readonly_api_key(api_key)
    assert api_key not in config.readonly_api_key_hash
    assert config.readonly_api_key_hint == api_key[-4:]

    settings_data = client.get("/api/settings", **admin_headers).json()["data"]
    assert settings_data["readonly_api_key_configured"] is True
    assert settings_data["readonly_api_key_hint"] == api_key[-4:]
    assert "readonly_api_key_hash" not in settings_data
    assert "api_key" not in settings_data

    api_headers = {"HTTP_AUTHORIZATION": f"Bearer {api_key}"}
    api_index = client.get("/api/v1", **api_headers)
    assert api_index.status_code == 200
    index_data = api_index.json()["data"]
    assert index_data["openapi"] == "/api/v1/openapi.json"
    assert index_data["authentication"]["scheme"] == "bearer"
    assert {
        endpoint["path"] for endpoint in index_data["endpoints"]
    } == {
        "/api/v1/participants",
        "/api/v1/statistics",
        "/api/v1/statistics/participants/{participant_id}/api-usage",
    }

    openapi_response = client.get("/api/v1/openapi.json", **api_headers)
    assert openapi_response.status_code == 200
    openapi = openapi_response.json()
    assert openapi["openapi"] == "3.1.0"
    assert openapi["servers"] == [{"url": "/api"}]
    assert set(openapi["paths"]) == {
        "/v1",
        "/v1/openapi.json",
        "/v1/participants",
        "/v1/statistics",
        "/v1/statistics/participants/{participant_id}/api-usage",
    }
    assert openapi["security"] == [{"ReadOnlyApiKey": []}]
    assert (
        openapi["components"]["securitySchemes"]["ReadOnlyApiKey"]["scheme"]
        == "bearer"
    )
    schemas = openapi["components"]["schemas"]
    assert schemas["CapacityPoint"]["properties"]["basis"]["type"] == [
        "object",
        "null",
    ]
    assert schemas["ApiKeyUsage"]["properties"]["api_key_id"]["type"] == [
        "integer",
        "null",
    ]
    assert schemas["Participant"]["properties"]["account_breakdowns"]["items"][
        "$ref"
    ].endswith("/AccountBreakdown")
    assert schemas["AggregateRecommendation"]["properties"]["allocation_model"][
        "const"
    ] == "pooled_account_sum"

    participants = client.get("/api/v1/participants", **api_headers)
    assert participants.status_code == 200
    assert participants.json()["data"][0]["id"] == participant.id
    participant_data = participants.json()["data"][0]
    assert participant_data["account_breakdowns"][0]["account_id"] == account.id

    assert client.get("/api/v1/statistics", **api_headers).status_code == 400
    statistics = client.get(
        f"/api/v1/statistics?account_id={account.id}"
        "&capacity_period=day&capacity_days=30"
        "&usage_days=7&usage_precision=hour",
        **api_headers,
    )
    assert statistics.status_code == 200
    assert statistics.json()["data"]["capacity_period"] == "day"
    monthly_statistics = client.get(
        f"/api/v1/statistics?account_id={account.id}"
        "&capacity_period=month&capacity_days=365",
        **api_headers,
    )
    assert monthly_statistics.status_code == 200
    monthly_points = monthly_statistics.json()["data"]["capacity_series"]
    assert monthly_points[0]["basis"] is None

    assert (
        client.get(
            f"/api/v1/statistics/participants/{participant.id}/api-usage",
            **api_headers,
        ).status_code
        == 400
    )
    api_usage = client.get(
        f"/api/v1/statistics/participants/{participant.id}/api-usage"
        f"?account_id={account.id}",
        **api_headers,
    )
    assert api_usage.status_code == 200
    assert api_usage.json()["data"]["participant_total_usd"] == 120.0
    assert api_usage.json()["data"]["api_keys"][1]["api_key_id"] is None

    assert client.post("/api/v1/participants", **api_headers).status_code == 405
    assert (
        client.post("/api/v1/openapi.json", **api_headers).status_code
        == 405
    )
    assert client.get("/api/settings", **api_headers).status_code == 401

    rotated_response = client.post(
        "/api/settings/readonly-api-key",
        **admin_headers,
    )
    rotated_key = rotated_response.json()["data"]["api_key"]
    assert rotated_key != api_key
    assert client.get("/api/v1/participants", **api_headers).status_code == 401
    rotated_headers = {"HTTP_AUTHORIZATION": f"Bearer {rotated_key}"}
    assert client.get("/api/v1/participants", **rotated_headers).status_code == 200

    revoked = client.delete("/api/settings/readonly-api-key", **admin_headers)
    assert revoked.status_code == 200
    assert revoked.json()["data"] == {"revoked": True}
    assert client.get("/api/v1/participants", **rotated_headers).status_code == 401
    config.refresh_from_db()
    assert config.readonly_api_key_hash == ""
    assert config.readonly_api_key_hint == ""
    assert config.readonly_api_key_created_at is None
