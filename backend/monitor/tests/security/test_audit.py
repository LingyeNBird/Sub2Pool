import json
import sqlite3
from io import BytesIO, StringIO

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
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
def test_request_and_remote_ip_blocks_return_empty_response(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    BlockedIPAddress.objects.create(
        address="198.51.100.21",
        source_type="request",
    )
    BlockedIPAddress.objects.create(
        address="10.0.0.8",
        source_type="remote",
    )
    client = Client()

    request_blocked = client.get(
        "/",
        REMOTE_ADDR="10.0.0.7",
        HTTP_X_FORWARDED_FOR="198.51.100.21",
    )
    remote_blocked = client.get(
        "/api/health",
        REMOTE_ADDR="10.0.0.8",
        HTTP_X_FORWARDED_FOR="198.51.100.22",
    )

    assert request_blocked.status_code == 204
    assert request_blocked.content == b""
    assert remote_blocked.status_code == 204
    assert remote_blocked.content == b""

@pytest.mark.django_db
def test_admin_manages_blocks_and_cannot_block_current_server_address():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    self_block = client.post(
        "/api/ip-blocks",
        data=json.dumps(
            {
                "address": "127.0.0.1",
                "source_type": "request",
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert self_block.status_code == 400

    created = client.post(
        "/api/ip-blocks",
        data=json.dumps(
            {
                "address": "203.0.113.17",
                "source_type": "request",
                "notes": "测试封禁",
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    block = created.json()["data"]
    assert block["address"] == "203.0.113.17"
    assert block["source_label"] == "服务器来源 IP"

    listed = client.get("/api/ip-blocks", **headers).json()["data"]
    assert [item["id"] for item in listed] == [block["id"]]
    assert (
        client.delete(f"/api/ip-blocks/{block['id']}", **headers).status_code
        == 200
    )
    assert not BlockedIPAddress.objects.exists()

@pytest.mark.django_db
def test_webrtc_block_rejects_preflight_and_login_with_empty_response():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    BlockedIPAddress.objects.create(
        address="203.0.113.29",
        source_type="webrtc",
    )
    payload = {
        "username": "owner",
        "password": "very-strong-password",
        "client_network": {
            "webrtc_supported": True,
            "webrtc_ips": ["203.0.113.29"],
        },
    }
    client = Client()

    preflight = client.post(
        "/api/auth/network-check",
        data=json.dumps({"client_network": payload["client_network"]}),
        content_type="application/json",
    )
    login = client.post(
        "/api/auth/login",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert preflight.status_code == 204
    assert preflight.content == b""
    assert login.status_code == 204
    assert login.content == b""
    event = LoginEvent.objects.get()
    assert event.success is False
    assert event.failure_reason == "WebRTC IP 已封禁"

@pytest.mark.django_db
def test_login_audit_records_server_and_webrtc_addresses(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    common = {
        "content_type": "application/json",
        "REMOTE_ADDR": "10.0.0.2",
        "HTTP_X_FORWARDED_FOR": "198.51.100.23",
        "HTTP_USER_AGENT": "Audit Browser/1.0",
    }
    failed = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": "owner",
                "password": "wrong-password",
                "client_network": {
                    "webrtc_supported": True,
                    "webrtc_ips": [
                        "192.168.1.8",
                        "host.local",
                        "999.1.1.1",
                    ],
                },
            }
        ),
        **common,
    )
    assert failed.status_code == 401

    success = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": "owner",
                "password": "very-strong-password",
                "client_network": {
                    "webrtc_supported": True,
                    "webrtc_ips": ["192.168.1.8", "203.0.113.9"],
                },
            }
        ),
        **common,
    )
    headers = {
        "HTTP_AUTHORIZATION": (
            f"Bearer {success.json()['data']['access']}"
        )
    }

    rows = list(LoginEvent.objects.order_by("created_at"))
    assert len(rows) == 2
    assert rows[0].success is False
    assert rows[0].failure_reason == "用户名、密码或权限错误"
    assert rows[1].success is True
    assert rows[1].request_ip == "198.51.100.23"
    assert rows[1].remote_ip == "10.0.0.2"
    assert rows[1].webrtc_ips == ["192.168.1.8", "203.0.113.9"]
    assert rows[1].user_agent == "Audit Browser/1.0"

    audit = client.get(
        "/api/login-events",
        {"page": 1, "page_size": 1},
        **headers,
    ).json()["data"]
    assert audit["success_count"] == 1
    assert audit["failure_count"] == 1
    assert audit["unique_request_ips"] == 1
    assert audit["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 2,
        "total_pages": 2,
    }
    assert [item["id"] for item in audit["items"]] == [rows[1].id]
    second_page = client.get(
        "/api/login-events",
        {"page": 2, "page_size": 1},
        **headers,
    ).json()["data"]
    assert [item["id"] for item in second_page["items"]] == [rows[0].id]
