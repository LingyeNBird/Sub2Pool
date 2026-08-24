from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.api_auth import API_KEY_PREFIX, hash_api_key
from monitor.fast_correction.rules import fast_correction_rules_digest
from monitor.models import (
    AccountParticipant,
    AppSettings,
    MonitoredAccount,
    NotificationEvent,
    Observation,
    PagePermission,
    Participant,
    ParticipantAPIUsageSnapshot,
    PoolParticipant,
    SystemUserAPIKey,
    SystemUserPageAccess,
)
from monitor.tests.helpers import (
    create_participant,
    create_recommendation_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_api_key_lifecycle_and_scope():
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
    )
    PoolParticipant.objects.create(
        pool=account.pool,
        participant=participant,
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
        fast_correction_enabled=config.fast_correction_enabled,
        fast_correction_rules_hash=fast_correction_rules_digest(
            config.fast_correction_rules
        ),
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
    notification = NotificationEvent.objects.create(
        event_type="test",
        participant=participant,
        dedupe_key="api-test",
        recipient="rider@example.com",
        subject="额度测试",
        body="API 通知正文",
        status="sent",
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
    assert config.readonly_api_key_hash == hash_api_key(api_key)
    assert api_key not in config.readonly_api_key_hash
    assert config.readonly_api_key_hint == api_key[-4:]

    settings_data = client.get("/api/settings", **admin_headers).json()["data"]
    assert settings_data["readonly_api_key_configured"] is True
    assert settings_data["readonly_api_key_hint"] == api_key[-4:]
    assert "readonly_api_key_hash" not in settings_data
    assert "api_key" not in settings_data

    assert client.get("/api/v1", **admin_headers).status_code == 401

    api_headers = {"HTTP_AUTHORIZATION": f"Bearer {api_key}"}
    api_index = client.get("/api/v1", **api_headers)
    assert api_index.status_code == 200
    index_data = api_index.json()["data"]
    assert index_data["openapi"] == "/api/v1/openapi.json"
    assert index_data["authentication"]["scheme"] == "bearer"
    expected_endpoint_paths = {
        "/api/v1/accounts",
        "/api/v1/dashboard",
        "/api/v1/recommendations",
        "/api/v1/recommendations/{participant_id}/apply",
        "/api/v1/account-status",
        "/api/v1/participants",
        "/api/v1/observations",
        "/api/v1/observations/{observation_id}/fast-correction",
        "/api/v1/particle-trajectory",
        "/api/v1/statistics",
        "/api/v1/statistics/participants/{participant_id}/api-usage",
        "/api/v1/notifications",
    }
    assert {
        endpoint["path"] for endpoint in index_data["endpoints"]
    } == expected_endpoint_paths
    apply_index = next(
        endpoint
        for endpoint in index_data["endpoints"]
        if endpoint["path"] == "/api/v1/recommendations/{participant_id}/apply"
    )
    assert apply_index["method"] == "POST"
    assert index_data["authentication"]["key_prefix"] == "sub2pool_"
    assert index_data["authentication"]["permissions"] == "all"

    openapi_response = client.get("/api/v1/openapi.json", **api_headers)
    assert openapi_response.status_code == 200
    openapi = openapi_response.json()
    assert openapi["openapi"] == "3.1.0"
    assert openapi["info"]["version"] == "1.5.1"
    assert openapi["servers"] == [{"url": "/api"}]
    assert set(openapi["paths"]) == {
        "/v1",
        "/v1/openapi.json",
        *(path.removeprefix("/api") for path in expected_endpoint_paths),
    }
    assert {
        f"/api{path}"
        for path in openapi["paths"]
        if path not in {"/v1", "/v1/openapi.json"}
    } == expected_endpoint_paths
    assert openapi["security"] == [{"ApiKey": []}]
    assert (
        openapi["components"]["securitySchemes"]["ApiKey"]["scheme"] == "bearer"
    )
    assert set(openapi["components"]["securitySchemes"]) == {"ApiKey"}
    assert openapi["paths"]["/v1/recommendations/{participant_id}/apply"][
        "post"
    ]["security"] == [{"ApiKey": []}]
    schemas = openapi["components"]["schemas"]
    assert schemas["CapacityPoint"]["properties"]["basis"]["oneOf"][0][
        "$ref"
    ].endswith("/CapacityClosingBasis")
    assert schemas["ApiKeyUsage"]["properties"]["api_key_id"]["type"] == [
        "integer",
        "null",
    ]
    assert schemas["Participant"]["properties"]["account_breakdowns"]["items"][
        "$ref"
    ].endswith("/AccountBreakdown")
    assert schemas["Participant"]["properties"]["pool_allocations"]["items"][
        "$ref"
    ].endswith("/ParticipantPoolAllocation")
    assert schemas["AggregateRecommendation"]["properties"]["allocation_model"][
        "const"
    ] == "partitioned_pool_sum"
    assert schemas["ParticipantSnapshot"]["properties"]["allocation_model"][
        "enum"
    ] == ["time_varying", "constant_average"]
    assert {
        "pool_id",
        "pool_name",
        "pool_contract_revision",
        "contract_share_percent",
    }.issubset(schemas["AggregateRecommendationSource"]["properties"])
    assert {
        "operation_id",
        "participant_id",
        "sub2api_user_id",
        "applied_balance_usd",
        "account_count",
    } == set(schemas["AppliedRecommendation"]["properties"])
    assert schemas["ObservationList"]["properties"]["items"]["items"][
        "$ref"
    ].endswith("/Observation")
    assert schemas["AccountStatus"]["properties"]["accounts"]["items"][
        "$ref"
    ].endswith("/AccountStatusAccount")
    assert (
        schemas["AccountUsageStats"]["properties"]["fast_correction_usd"]
        == {"type": ["number", "null"]}
    )
    assert (
        schemas["AccountUsageStats"]["properties"][
            "account_cost_with_fast_correction_usd"
        ]
        == {"type": ["number", "null"]}
    )
    assert schemas["NotificationList"]["properties"]["items"]["items"][
        "$ref"
    ].endswith("/Notification")

    referenced_schemas = set()

    def collect_schema_references(value):
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith(
                "#/components/schemas/"
            ):
                referenced_schemas.add(reference.rsplit("/", 1)[-1])
            for child in value.values():
                collect_schema_references(child)
        elif isinstance(value, list):
            for child in value:
                collect_schema_references(child)

    collect_schema_references(openapi)
    assert referenced_schemas <= set(schemas)

    participants = client.get("/api/v1/participants", **api_headers)
    assert participants.status_code == 200
    assert participants.json()["data"][0]["id"] == participant.id
    participant_data = participants.json()["data"][0]
    assert participant_data["account_breakdowns"][0]["account_id"] == account.id

    accounts = client.get("/api/v1/accounts", **api_headers)
    assert accounts.status_code == 200
    assert accounts.json()["data"][0]["external_account_id"] == 7

    dashboard = client.get(
        f"/api/v1/dashboard?account_id={account.id}",
        **api_headers,
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["selected_account_id"] == account.id

    account_status = client.get("/api/v1/account-status", **api_headers)
    assert account_status.status_code == 200
    assert account_status.json()["data"]["configured"] is False
    assert account_status.json()["data"]["accounts"][0]["id"] == account.id

    observations = client.get(
        f"/api/v1/observations?account_id={account.id}&page_size=1",
        **api_headers,
    )
    assert observations.status_code == 200
    assert observations.json()["data"]["items"][0]["id"] == observation.id
    assert observations.json()["data"]["pagination"]["total"] == 1

    fast_detail = client.get(
        f"/api/v1/observations/{observation.id}/fast-correction",
        **api_headers,
    )
    assert fast_detail.status_code == 200
    assert fast_detail.json()["data"]["observation_id"] == observation.id
    assert fast_detail.json()["data"]["calculated"] is False

    trajectory = client.get(
        f"/api/v1/particle-trajectory?account_id={account.id}",
        **api_headers,
    )
    assert trajectory.status_code == 200
    assert trajectory.json()["data"]["account"]["id"] == account.id

    notifications = client.get(
        "/api/v1/notifications?status=sent&page_size=1",
        **api_headers,
    )
    assert notifications.status_code == 200
    assert notifications.json()["data"]["items"][0]["id"] == notification.id
    assert notifications.json()["data"]["items"][0]["body"] == notification.body

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

    read_paths = [
        "/api/v1/accounts",
        f"/api/v1/dashboard?account_id={account.id}",
        "/api/v1/recommendations",
        "/api/v1/account-status",
        "/api/v1/participants",
        f"/api/v1/observations?account_id={account.id}",
        f"/api/v1/observations/{observation.id}/fast-correction",
        f"/api/v1/particle-trajectory?account_id={account.id}",
        f"/api/v1/statistics?account_id={account.id}",
        (
            f"/api/v1/statistics/participants/{participant.id}/api-usage"
            f"?account_id={account.id}"
        ),
        "/api/v1/notifications",
    ]
    for path in read_paths:
        assert client.post(path, **api_headers).status_code == 405
    assert client.post("/api/v1/openapi.json", **api_headers).status_code == 405
    assert (
        client.post(
            f"/api/v1/recommendations/{participant.id}/apply",
            **api_headers,
        ).status_code
        == 409
    )
    assert client.get("/api/settings", **api_headers).status_code == 401
    assert client.get("/api/login-events", **api_headers).status_code == 401

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



@pytest.mark.django_db
def test_system_user_api_key_follows_live_page_and_data_permissions():
    user_model = get_user_model()
    user_model.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    viewer = user_model.objects.create_user(
        username="viewer",
        password="viewer-password",
    )
    SystemUserPageAccess.objects.bulk_create(
        [
            SystemUserPageAccess(user=viewer, page_code=page_code)
            for page_code in (
                PagePermission.ACCOUNT_STATUS,
                PagePermission.PARTICIPANTS,
                PagePermission.OBSERVATIONS,
                PagePermission.PARTICLE_FILTER,
            )
        ]
    )
    allowed_account = MonitoredAccount.objects.create(
        external_account_id=7,
        name="授权账号",
    )
    hidden_account = MonitoredAccount.objects.create(
        external_account_id=8,
        name="隐藏账号",
    )
    viewer.visible_monitored_accounts.add(allowed_account)
    visible_participant = Participant.objects.create(
        name="授权参与者",
        sub2api_user_id=21,
    )
    hidden_participant = Participant.objects.create(
        name="隐藏参与者",
        sub2api_user_id=22,
    )
    viewer.quota_participants.add(visible_participant)
    for account in (allowed_account, hidden_account):
        AccountParticipant.objects.create(
            account=account,
            participant=visible_participant,
        )
        PoolParticipant.objects.create(
            pool=account.pool,
            participant=visible_participant,
            share_percent=Decimal("40"),
        )
    now = timezone.now()
    observations = {}
    for offset, account in enumerate((allowed_account, hidden_account)):
        observations[account.id] = Observation.objects.create(
            account_id=account.external_account_id,
            observed_at=now + timedelta(minutes=offset),
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

    client = Client()
    viewer_headers, _ = jwt_login(
        client,
        username="viewer",
        password="viewer-password",
    )
    state = client.get("/api/settings/my-api-key", **viewer_headers)
    assert state.status_code == 200
    assert state.json()["data"] == {
        "configured": False,
        "hint": "",
        "created_at": None,
    }

    generated = client.post(
        "/api/settings/my-api-key",
        **viewer_headers,
    ).json()["data"]
    api_key = generated["api_key"]
    record = SystemUserAPIKey.objects.get(user=viewer)
    assert record.key_hash == hash_api_key(api_key)
    assert record.hint == api_key[-4:]

    api_headers = {"HTTP_AUTHORIZATION": f"Bearer {api_key}"}
    index = client.get("/api/v1", **api_headers)
    assert index.status_code == 200
    index_data = index.json()["data"]
    assert index_data["authentication"]["permissions"] == "page_scoped"
    expected_paths = {
        "/api/v1/accounts",
        "/api/v1/account-status",
        "/api/v1/participants",
        "/api/v1/particle-trajectory",
        "/api/v1/observations",
        "/api/v1/observations/{observation_id}/fast-correction",
    }
    assert {
        endpoint["path"] for endpoint in index_data["endpoints"]
    } == expected_paths

    openapi = client.get("/api/v1/openapi.json", **api_headers).json()
    assert openapi["info"]["version"] == "1.5.1"
    assert "无需单独分配“系统设置”权限" in openapi["info"]["description"]
    assert "无需单独分配“系统设置”权限" in openapi["components"][
        "securitySchemes"
    ]["ApiKey"]["description"]
    assert set(openapi["paths"]) == {
        "/v1",
        "/v1/openapi.json",
        *(path.removeprefix("/api") for path in expected_paths),
    }
    assert "/v1/recommendations/{participant_id}/apply" not in openapi["paths"]

    accounts = client.get("/api/v1/accounts", **api_headers)
    assert accounts.status_code == 200
    assert [item["id"] for item in accounts.json()["data"]] == [
        allowed_account.id
    ]

    participants = client.get("/api/v1/participants", **api_headers)
    assert participants.status_code == 200
    participant_rows = participants.json()["data"]
    assert [item["id"] for item in participant_rows] == [
        visible_participant.id
    ]
    assert [
        item["account_id"]
        for item in participant_rows[0]["account_breakdowns"]
    ] == [allowed_account.id]
    assert hidden_participant.id not in {
        item["id"] for item in participant_rows
    }

    allowed_observations = client.get(
        f"/api/v1/observations?account_id={allowed_account.id}",
        **api_headers,
    )
    assert allowed_observations.status_code == 200
    assert [
        item["id"] for item in allowed_observations.json()["data"]["items"]
    ] == [observations[allowed_account.id].id]
    assert (
        client.get(
            f"/api/v1/observations?account_id={hidden_account.id}",
            **api_headers,
        ).status_code
        == 400
    )
    assert (
        client.get(
            (
                "/api/v1/observations/"
                f"{observations[hidden_account.id].id}/fast-correction"
            ),
            **api_headers,
        ).status_code
        == 404
    )
    viewer.visible_monitored_accounts.clear()
    no_account_observations = client.get(
        "/api/v1/observations",
        **api_headers,
    )
    assert no_account_observations.status_code == 200
    assert no_account_observations.json()["data"]["items"] == []
    assert no_account_observations.json()["data"]["pagination"]["total"] == 0
    assert client.get("/api/v1/dashboard", **api_headers).status_code == 403
    assert (
        client.post(
            f"/api/v1/recommendations/{visible_participant.id}/apply",
            **api_headers,
        ).status_code
        == 403
    )

    SystemUserPageAccess.objects.filter(
        user=viewer,
        page_code=PagePermission.PARTICLE_FILTER,
    ).delete()
    refreshed_index = client.get("/api/v1", **api_headers).json()["data"]
    assert "/api/v1/particle-trajectory" not in {
        endpoint["path"] for endpoint in refreshed_index["endpoints"]
    }
    assert (
        client.get("/api/v1/particle-trajectory", **api_headers).status_code
        == 403
    )

    rotated_key = client.post(
        "/api/settings/my-api-key",
        **viewer_headers,
    ).json()["data"]["api_key"]
    assert rotated_key != api_key
    assert client.get("/api/v1", **api_headers).status_code == 401
    rotated_headers = {"HTTP_AUTHORIZATION": f"Bearer {rotated_key}"}
    assert client.get("/api/v1", **rotated_headers).status_code == 200
    viewer.is_active = False
    viewer.save(update_fields=["is_active"])
    assert client.get("/api/v1", **rotated_headers).status_code == 401
    viewer.is_active = True
    viewer.save(update_fields=["is_active"])
    assert client.get("/api/v1", **rotated_headers).status_code == 200

    revoked = client.delete("/api/settings/my-api-key", **viewer_headers)
    assert revoked.status_code == 200
    assert revoked.json()["data"] == {"revoked": True}
    assert not SystemUserAPIKey.objects.filter(user=viewer).exists()
    assert client.get("/api/v1", **rotated_headers).status_code == 401

@pytest.mark.django_db
def test_recommendation_api_matches_homepage_and_includes_source_details():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = create_participant(
        name="需要调整",
        sub2api_user_id=51,
        share_percent=50,
    )
    create_recommendation_snapshot(participant)
    client = Client()
    admin_headers, _ = jwt_login(client)
    api_key = client.post(
        "/api/settings/readonly-api-key",
        **admin_headers,
    ).json()["data"]["api_key"]
    api_headers = {"HTTP_AUTHORIZATION": f"Bearer {api_key}"}

    homepage = client.get("/api/dashboard", **admin_headers)
    recommendations = client.get("/api/v1/recommendations", **api_headers)

    assert homepage.status_code == 200
    assert recommendations.status_code == 200
    data = recommendations.json()["data"]
    assert data == homepage.json()["data"]["participants"]
    assert [item["id"] for item in data] == [participant.id]
    recommendation = data[0]["snapshot"]
    assert recommendation["recommendation_complete"] is True
    assert recommendation["needs_manual_update"] is True
    assert recommendation["recommended_balance_usd"] is not None
    assert recommendation["recommended_balance_min_usd"] is not None
    assert recommendation["recommended_balance_max_usd"] is not None
    assert recommendation["reason"]
    assert len(recommendation["sources"]) == 1
    source = recommendation["sources"][0]
    assert source["contract_share_percent"] == 50.0
    assert source["snapshot"]["participant_id"] == participant.id
    assert source["contribution_usd"] is not None
