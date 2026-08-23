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
    AccountParticipant,
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    MonitoredAccount,
    NotificationEvent,
    Observation,
    ObservationFastCorrection,
    Participant,
    ParticipantSnapshot,
    PoolParticipant,
    QuotaPool,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from monitor.notifications import send_notification
from monitor.sampling.notifications import send_observation_notifications
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
def test_resend_provider_sends_with_encrypted_key(monkeypatch):
    config = AppSettings.load()
    config.email_provider = "resend"
    config.notification_email = "owner@example.com"
    config.resend_from_email = "拼车额度 <notice@example.com>"
    config.resend_api_key_encrypted = encrypt_secret("re_secret")
    config.save()
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(200, json={"id": "email_123"})

    monkeypatch.setattr("monitor.notifications.httpx.post", fake_post)
    event = send_notification(
        config=config,
        event_type="test",
        dedupe_key="resend-test",
        subject="测试",
        body="正文",
        ignore_cooldown=True,
    )

    assert event is not None
    assert event.status == "sent"
    assert NotificationEvent.objects.get(pk=event.pk).sent_at is not None
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_secret"
    assert (
        captured["headers"]["Idempotency-Key"]
        == f"pinche-notification-{event.pk}"
    )
    assert captured["json"] == {
        "from": "拼车额度 <notice@example.com>",
        "to": ["owner@example.com"],
        "subject": "测试",
        "text": "正文",
    }

@pytest.mark.django_db
def test_resend_uses_a_new_idempotency_key_for_each_delivery(monkeypatch):
    config = AppSettings.load()
    config.email_provider = "resend"
    config.notification_email = "owner@example.com"
    config.resend_from_email = "拼车额度 <notice@example.com>"
    config.resend_api_key_encrypted = encrypt_secret("re_secret")
    config.save()
    requests_by_key = {}

    def fake_post(_url, **kwargs):
        key = kwargs["headers"]["Idempotency-Key"]
        payload = kwargs["json"]
        if key in requests_by_key and requests_by_key[key] != payload:
            return httpx.Response(
                409,
                json={
                    "message": (
                        "This idempotency key has been used with a modified body"
                    )
                },
            )
        requests_by_key[key] = payload
        return httpx.Response(
            200,
            json={"id": f"email_{len(requests_by_key)}"},
        )

    monkeypatch.setattr("monitor.notifications.httpx.post", fake_post)
    first = send_notification(
        config=config,
        event_type="test",
        dedupe_key="same-business-notification",
        subject="测试",
        body="第一次正文",
        ignore_cooldown=True,
    )
    second = send_notification(
        config=config,
        event_type="test",
        dedupe_key="same-business-notification",
        subject="测试",
        body="修改后的正文",
        ignore_cooldown=True,
    )

    assert first is not None and first.status == "sent"
    assert second is not None and second.status == "sent"
    assert set(requests_by_key) == {
        f"pinche-notification-{first.pk}",
        f"pinche-notification-{second.pk}",
    }

@pytest.mark.django_db
def test_resend_retries_one_transient_transport_failure(monkeypatch):
    config = AppSettings.load()
    config.email_provider = "resend"
    config.notification_email = "owner@example.com"
    config.resend_from_email = "拼车额度 <notice@example.com>"
    config.resend_api_key_encrypted = encrypt_secret("re_secret")
    config.save()
    requests = []

    def fake_post(_url, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise httpx.ConnectError("temporary TLS disconnect")
        return httpx.Response(200, json={"id": "email_after_retry"})

    monkeypatch.setattr("monitor.notifications.httpx.post", fake_post)
    event = send_notification(
        config=config,
        event_type="test",
        dedupe_key="resend-transport-retry",
        subject="测试",
        body="正文",
        ignore_cooldown=True,
    )

    assert event is not None and event.status == "sent"
    assert len(requests) == 2
    assert requests[0]["headers"]["Idempotency-Key"] == requests[1]["headers"]["Idempotency-Key"]
    assert requests[0]["json"] == requests[1]["json"]


@pytest.mark.django_db
def test_exhausted_balance_emails_multi_account_aggregate_notice(
    monkeypatch,
):
    config = AppSettings.load()
    config.notify_on_limit_exhausted = True
    config.limit_warning_usd = Decimal("1")
    config.save()
    pool = QuotaPool.objects.create(name="主备用混池")
    primary_account = MonitoredAccount.objects.create(
        external_account_id=7,
        name="主账号",
        pool=pool,
    )
    secondary_account = MonitoredAccount.objects.create(
        external_account_id=8,
        name="备用账号",
        pool=pool,
    )
    participant = Participant.objects.create(
        name="待补额车友",
        email="rider@example.com",
        sub2api_user_id=51,
        latest_balance_usd=Decimal("0"),
    )
    PoolParticipant.objects.create(
        pool=pool,
        participant=participant,
        share_percent=Decimal("50"),
    )
    for account in (primary_account, secondary_account):
        AccountParticipant.objects.create(
            account=account,
            participant=participant,
        )
    now = timezone.now()

    def create_snapshot(account, recommended):
        observation = Observation.objects.create(
            account_id=account.external_account_id,
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
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            source_sub2api_user_id=participant.sub2api_user_id,
            share_percent=Decimal("50"),
            quota_pool_id=account.pool_id,
            quota_pool_name=account.pool.name,
            pool_contract_revision=account.pool.contract_revision,
            raw_selected_cost=Decimal("200"),
            selected_cost=Decimal("200"),
            current_balance_usd=Decimal("0"),
            charged_cycle_percent=Decimal("40"),
            remaining_share_percent=Decimal("10"),
            recommended_balance_usd=recommended,
            recommended_balance_min_usd=recommended,
            recommended_balance_max_usd=recommended,
            needs_manual_update=True,
        )
        return observation

    observation = create_snapshot(primary_account, Decimal("190"))
    create_snapshot(secondary_account, Decimal("30"))
    captured = []

    def fake_send_notification(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "monitor.sampling.notifications.send_notification",
        fake_send_notification,
    )

    send_observation_notifications(config, observation, None)

    assert len(captured) == 1
    assert captured[0]["event_type"] == "limit_exhausted"
    assert captured[0]["participant"] == participant
    assert "需要补充全局余额" in captured[0]["subject"]
    assert "账号 主账号 的新观测" in captured[0]["body"]
    assert "跨账号净额化后的混池建议余额：$380.0" in captured[0]["body"]
    assert "参与混池账号数：2" in captured[0]["body"]


@pytest.mark.django_db
def test_usage_logs_are_paginated_and_filtered_to_exact_interval():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    started_at = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    ended_at = started_at + timedelta(hours=1)
    requested_pages: list[int] = []

    def raw_log(
        log_id: int,
        created_at,
        *,
        account_id: int = 7,
        user_id: int = 51,
        service_tier: str = "priority",
        api_key_id: int = 91,
    ):
        return {
            "id": log_id,
            "user_id": user_id,
            "account_id": account_id,
            "created_at": created_at.isoformat(),
            "service_tier": service_tier,
            "total_cost": "4.00",
            "actual_cost": "3.00",
            "api_key_id": api_key_id,
            "api_key": {"id": api_key_id, "name": "主密钥"},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/admin/usage"
        assert request.url.params["account_id"] == "7"
        assert request.url.params["page_size"] == "1000"
        assert request.url.params["sort_order"] == "asc"
        assert request.url.params["sort_by"] == "created_at"
        assert request.url.params["exact_total"] == "true"
        assert request.url.params["user_id"] == "51"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        items = (
            [
                raw_log(1, started_at - timedelta(seconds=1)),
                raw_log(2, started_at),
                *[
                    raw_log(log_id, started_at - timedelta(seconds=2))
                    for log_id in range(5, 1003)
                ],
            ]
            if page == 1
            else [
                raw_log(3, ended_at),
                raw_log(4, ended_at - timedelta(seconds=1)),
            ]
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": items,
                    "total": 1002,
                    "page": page,
                    "page_size": 1000,
                    "pages": 2,
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        rows = client.usage_logs(
            account_id=7,
            started_at=started_at,
            ended_at=ended_at,
            timezone_name="Asia/Shanghai",
            user_id=51,
        )

    assert requested_pages == [1, 2]
    assert [row.id for row in rows] == [2, 4]
    assert [row.user_id for row in rows] == [51, 51]
    assert rows[0].total_cost == Decimal("4.00")
    assert rows[0].actual_cost == Decimal("3.00")
    assert rows[0].api_key_id == 91
    assert rows[0].api_key_name == "主密钥"
