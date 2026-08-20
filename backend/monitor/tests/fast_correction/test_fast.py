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
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_recommendation_snapshot,
    jwt_login,
)

@pytest.mark.django_db
def test_sampling_applies_ordered_model_fast_correction_rules(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.cost_basis = "actual"
    config.fast_correction_enabled = True
    config.fast_correction_rules = [
        {
            "model_pattern": "gpt-5.6*",
            "source_multiplier": "2.5",
            "target_multiplier": "2.5",
        },
        {
            "model_pattern": "*",
            "source_multiplier": "2",
            "target_multiplier": "2.5",
        },
    ]
    config.save()
    participant = create_participant(name="已配置参与者",
    sub2api_user_id=51,
    share_percent=100,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("10"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, *, user_id=None, **_kwargs):
            value = Decimal("200") if user_id is None else Decimal("150")
            return UsageStats(value, value)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

        def usage_logs(
            self,
            *,
            account_id,
            started_at,
            ended_at,
            timezone_name,
        ):
            assert account_id == 7
            assert started_at < ended_at
            assert timezone_name == "Asia/Shanghai"
            return [
                Sub2APIUsageLog(
                    1,
                    51,
                    7,
                    ended_at - timedelta(minutes=3),
                    "priority",
                    Decimal("80"),
                    Decimal("80"),
                    model="gpt-5.6-codex",
                ),
                Sub2APIUsageLog(
                    2,
                    52,
                    7,
                    ended_at - timedelta(minutes=2),
                    "priority",
                    Decimal("20"),
                    Decimal("20"),
                    model="gpt-5.4",
                ),
                Sub2APIUsageLog(
                    3,
                    51,
                    7,
                    ended_at - timedelta(minutes=1),
                    "default",
                    Decimal("100"),
                    Decimal("100"),
                ),
            ]

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    observation = Observation.objects.get()
    assert observation.fast_correction_standard_cost == Decimal("5")
    assert observation.fast_correction_actual_cost == Decimal("5")
    assert observation.selected_total_cost == Decimal("205")
    corrections = list(
        ObservationFastCorrection.objects.order_by("sub2api_user_id")
    )
    assert [row.sub2api_user_id for row in corrections] == [51, 52]
    assert [row.actual_correction_cost for row in corrections] == [
        Decimal("0"),
        Decimal("5"),
    ]
    assert observation.fast_correction_request_count == 3
    assert [row.request_count for row in corrections] == [2, 1]

    client = Client()
    headers, _ = jwt_login(client)
    detail_response = client.get(
        f"/api/observations/{observation.id}/fast-correction",
        **headers,
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["request_count"] == 3
    assert detail["fast_request_count"] == 2
    assert detail["non_fast_request_count"] == 1
    assert detail["fast_billed_cost_usd"] == 100.0
    assert detail["correction_usd"] == 5.0
    assert detail["corrected_fast_cost_usd"] == 105.0
    assert detail["users"] == [
        {
            "sub2api_user_id": 51,
            "username": "",
            "email": "",
            "display_name": "已配置参与者",
            "request_count": 2,
            "fast_request_count": 1,
            "non_fast_request_count": 1,
            "fast_billed_cost_usd": 80.0,
            "correction_usd": 0.0,
            "corrected_fast_cost_usd": 80.0,
        },
        {
            "sub2api_user_id": 52,
            "username": "",
            "email": "",
            "display_name": "用户 52",
            "request_count": 1,
            "fast_request_count": 1,
            "non_fast_request_count": 0,
            "fast_billed_cost_usd": 20.0,
            "correction_usd": 5.0,
            "corrected_fast_cost_usd": 25.0,
        },
    ]
    snapshot = ParticipantSnapshot.objects.get(participant=participant)
    assert snapshot.selected_cost == Decimal("150")

@pytest.mark.django_db
def test_disabled_fast_correction_skips_log_reads_and_preserves_null_interval(
    monkeypatch,
):
    config = AppSettings.load()
    create_monitored_account(7)
    config.fast_correction_enabled = False
    config.save()
    create_participant(name="车友",
    sub2api_user_id=51,
    share_percent=100,)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("10"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("100"), Decimal("100"))

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

        def usage_logs(self, **_kwargs):
            raise AssertionError("关闭 FAST 修正后不应读取请求日志")

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(account_id=create_monitored_account(7).id, force_upstream=True, source="manual")

    observation = Observation.objects.get()
    assert observation.fast_correction_standard_cost is None
    assert observation.fast_correction_actual_cost is None
    assert observation.selected_total_cost == Decimal("100")

@pytest.mark.django_db
def test_unsafe_fast_rebuild_endpoint_is_removed_and_missing_facts_are_preserved():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.fast_correction_enabled = True
    config.save()
    cycle_start = timezone.now().replace(microsecond=0) - timedelta(days=2)
    reset_at = cycle_start + timedelta(days=7)
    observations = [
        Observation.objects.create(
            account_id=7,
            observed_at=cycle_start + timedelta(hours=offset),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=cycle_start,
            upstream_used_percent=Decimal(offset * 10),
            raw_selected_total_cost=Decimal(offset * 100),
            selected_total_cost=Decimal(offset * 100),
            total_standard_cost=Decimal(offset * 100),
            total_actual_cost=Decimal(offset * 100),
            effective_usd_per_percent=Decimal("10"),
        )
        for offset in (1, 2)
    ]
    client = Client()
    headers, _ = jwt_login(client)
    settings = client.get("/api/settings", **headers).json()["data"]
    assert settings["fast_correction_rebuild_recommended"] is True
    assert settings["fast_correction_missing_intervals"] == 2

    response = client.post(
        "/api/settings/fast-correction/rebuild",
        data={"scope": "all"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 404
    for observation in observations:
        observation.refresh_from_db()
        assert observation.fast_correction_standard_cost is None
        assert observation.fast_correction_actual_cost is None
        assert observation.fast_correction_request_count is None


@pytest.mark.django_db
def test_admin_can_calculate_one_missing_fast_interval_without_bulk_rebuild(
    monkeypatch,
):
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    User.objects.create_user(
        username="viewer",
        password="Viewer-Access-2026!secure",
        email="viewer@example.com",
    )
    config = AppSettings.load()
    config.fast_correction_enabled = True
    config.save()
    cycle_start = timezone.now().replace(microsecond=0) - timedelta(days=2)
    reset_at = cycle_start + timedelta(days=7)
    previous = Observation.objects.create(
        account_id=7,
        observed_at=cycle_start + timedelta(hours=1),
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=cycle_start,
        upstream_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("100"),
        selected_total_cost=Decimal("100"),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
        effective_usd_per_percent=Decimal("10"),
    )
    target = Observation.objects.create(
        account_id=7,
        observed_at=cycle_start + timedelta(hours=2),
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=cycle_start,
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("200"),
        selected_total_cost=Decimal("200"),
        total_standard_cost=Decimal("200"),
        total_actual_cost=Decimal("200"),
        effective_usd_per_percent=Decimal("10"),
    )
    calls = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def usage_logs(
            self,
            *,
            account_id,
            started_at,
            ended_at,
            timezone_name,
        ):
            calls.append((account_id, started_at, ended_at, timezone_name))
            return [
                Sub2APIUsageLog(
                    id=1,
                    user_id=51,
                    account_id=7,
                    created_at=ended_at - timedelta(minutes=1),
                    service_tier="priority",
                    total_cost=Decimal("100"),
                    actual_cost=Decimal("80"),
                    model="gpt-5.4",
                )
            ]

    monkeypatch.setattr(
        "monitor.fast_correction.repair.Sub2APIClient",
        FakeClient,
    )
    client = Client()
    headers, _ = jwt_login(client)
    response = client.post(
        f"/api/observations/{target.id}/fast-correction/calculate",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "observation_id": target.id,
        "fast_correction_usd": 20.0,
        "fast_correction_calculated": True,
    }
    assert calls == [
        (7, previous.observed_at, target.observed_at, "Asia/Shanghai")
    ]
    previous.refresh_from_db()
    target.refresh_from_db()
    assert previous.fast_correction_actual_cost is None
    assert target.fast_correction_started_at == previous.observed_at
    assert target.fast_correction_standard_cost == Decimal("25")
    assert target.fast_correction_actual_cost == Decimal("20")
    assert target.fast_correction_request_count == 1
    assert target.selected_total_cost == Decimal("220")
    correction = ObservationFastCorrection.objects.get(observation=target)
    assert correction.sub2api_user_id == 51
    assert correction.actual_correction_cost == Decimal("20")
    settings = client.get("/api/settings", **headers).json()["data"]
    assert settings["fast_correction_missing_intervals"] == 0

    repeated = client.post(
        f"/api/observations/{target.id}/fast-correction/calculate",
        **headers,
    )
    assert repeated.status_code == 200
    assert len(calls) == 1

    regular_client = Client()
    regular_headers, _ = jwt_login(
        regular_client,
        username="viewer",
        password="Viewer-Access-2026!secure",
    )
    assert (
        regular_client.post(
            f"/api/observations/{previous.id}/fast-correction/calculate",
            **regular_headers,
        ).status_code
        == 403
    )

    class FailingClient(FakeClient):
        def usage_logs(self, **_kwargs):
            raise Sub2APIError("请求日志暂时不可用")

    monkeypatch.setattr(
        "monitor.fast_correction.repair.Sub2APIClient",
        FailingClient,
    )
    failed = client.post(
        f"/api/observations/{previous.id}/fast-correction/calculate",
        **headers,
    )
    assert failed.status_code == 502
    previous.refresh_from_db()
    assert previous.fast_correction_standard_cost is None
    assert previous.fast_correction_actual_cost is None
