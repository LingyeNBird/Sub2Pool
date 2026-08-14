from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from monitor.integrations.sub2api import Sub2APIClient, Sub2APIError
from monitor.models import AppSettings
from monitor.secrets import encrypt_secret


def _client(handler):
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    client = Sub2APIClient(config)
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"x-api-key": "admin-secret"},
    )
    return client


@pytest.mark.django_db
def test_usage_logs_uses_strict_pagination_and_returns_sampling_rows():
    ended_at = timezone.now().replace(microsecond=0)
    started_at = ended_at - timedelta(hours=2)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [
                        {
                            "id": 10,
                            "user_id": 11,
                            "account_id": 7,
                            "created_at": (
                                started_at + timedelta(minutes=5)
                            ).isoformat(),
                            "service_tier": "priority",
                            "total_cost": "4.5",
                            "actual_cost": "3.5",
                            "api_key_id": 91,
                            "api_key": {"name": "desktop"},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 1000,
                    "pages": 1,
                },
            },
        )

    with _client(handler) as client:
        rows = client.usage_logs(
            account_id=7,
            started_at=started_at,
            ended_at=ended_at,
            timezone_name="Asia/Shanghai",
        )

    assert len(requests) == 1
    assert requests[0].url.params["exact_total"] == "true"
    assert requests[0].url.params["sort_by"] == "created_at"
    assert requests[0].url.params["sort_order"] == "asc"
    assert len(rows) == 1
    assert rows[0].api_key_id == 91
    assert rows[0].api_key_name == "desktop"


@pytest.mark.django_db
def test_usage_logs_rejects_pagination_totals_that_change_mid_scan():
    ended_at = timezone.now().replace(microsecond=0)
    started_at = ended_at - timedelta(hours=2)

    def row(log_id: int) -> dict:
        return {
            "id": log_id,
            "user_id": 11,
            "account_id": 7,
            "created_at": (
                started_at + timedelta(microseconds=log_id)
            ).isoformat(),
            "service_tier": "",
            "total_cost": "1",
            "actual_cost": "1",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [row(log_id) for log_id in range(1, 1001)]
                    if page == 1
                    else [],
                    "total": 1001 if page == 1 else 1002,
                    "page": page,
                    "page_size": 1000,
                    "pages": 2,
                },
            },
        )

    with _client(handler) as client:
        with pytest.raises(Sub2APIError, match="分页期间数据发生变化"):
            client.usage_logs(
                account_id=7,
                started_at=started_at,
                ended_at=ended_at,
                timezone_name="UTC",
            )


@pytest.mark.django_db
def test_usage_logs_rejects_duplicate_rows_across_pages():
    ended_at = timezone.now().replace(microsecond=0)
    started_at = ended_at - timedelta(hours=2)

    def row(log_id: int) -> dict:
        return {
            "id": log_id,
            "user_id": 11,
            "account_id": 7,
            "created_at": (
                started_at + timedelta(microseconds=log_id)
            ).isoformat(),
            "service_tier": "",
            "total_cost": "1",
            "actual_cost": "1",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": (
                        [row(log_id) for log_id in range(1, 1001)]
                        if page == 1
                        else [row(1000)]
                    ),
                    "total": 1001,
                    "page": page,
                    "page_size": 1000,
                    "pages": 2,
                },
            },
        )

    with _client(handler) as client:
        with pytest.raises(Sub2APIError, match="重复行"):
            client.usage_logs(
                account_id=7,
                started_at=started_at,
                ended_at=ended_at,
                timezone_name="UTC",
            )


@pytest.mark.django_db
def test_usage_logs_rejects_reported_total_larger_than_actual_rows():
    ended_at = timezone.now().replace(microsecond=0)
    started_at = ended_at - timedelta(hours=2)
    raw = {
        "id": 10,
        "user_id": 11,
        "account_id": 7,
        "created_at": (started_at + timedelta(minutes=5)).isoformat(),
        "service_tier": "",
        "total_cost": "1",
        "actual_cost": "1",
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [raw],
                    "total": 2,
                    "page": 1,
                    "page_size": 1000,
                    "pages": 1,
                },
            },
        )

    with _client(handler) as client:
        with pytest.raises(Sub2APIError, match="分页.*行数|总数"):
            client.usage_logs(
                account_id=7,
                started_at=started_at,
                ended_at=ended_at,
                timezone_name="UTC",
            )


@pytest.mark.django_db
def test_usage_logs_rejects_truncated_last_page():
    ended_at = timezone.now().replace(microsecond=0)
    started_at = ended_at - timedelta(hours=2)
    raw = {
        "id": 10,
        "user_id": 11,
        "account_id": 7,
        "created_at": (started_at + timedelta(minutes=5)).isoformat(),
        "service_tier": "",
        "total_cost": "1",
        "actual_cost": "1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [raw] if page == 1 else [],
                    "total": 1001,
                    "page": page,
                    "page_size": 1000,
                    "pages": 2,
                },
            },
        )

    with _client(handler) as client:
        with pytest.raises(Sub2APIError, match="分页.*行数|总数"):
            client.usage_logs(
                account_id=7,
                started_at=started_at,
                ended_at=ended_at,
                timezone_name="UTC",
            )
