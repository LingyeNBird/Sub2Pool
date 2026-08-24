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

from monitor.api_usage import refresh_due_api_usage_snapshots
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
    PagePermission,
    ParticipantAPIUsageSnapshot,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
    SystemUserPageAccess,
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
from monitor.reporting.costs import FastCorrectionBreakdownPresenter
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_recommendation_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_disabled_fast_mode_still_reports_saved_historical_correction():
    config = AppSettings.load()
    config.cost_basis = "actual"
    config.fast_correction_enabled = False
    config.save()
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=4),
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("100"),
        selected_total_cost=Decimal("125"),
        total_standard_cost=Decimal("125"),
        total_actual_cost=Decimal("125"),
        fast_correction_standard_cost=Decimal("25"),
        fast_correction_actual_cost=Decimal("25"),
        effective_usd_per_percent=Decimal("6.25"),
    )

    breakdown = FastCorrectionBreakdownPresenter(config, 7).for_observation(
        observation
    )

    assert breakdown == {
        "sub2api_cost_usd": 100.0,
        "fast_correction_usd": 25.0,
        "total_cost_usd": 125.0,
    }


@pytest.mark.django_db
def test_statistics_groups_capacity_and_participant_usage():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=22,
    share_percent=50,)
    now = timezone.now()
    base = (now - timedelta(days=60)).replace(
        day=10,
        hour=8,
        minute=0,
        second=0,
        microsecond=0,
    )
    reset_at = now + timedelta(days=3)
    attribution_started_at = base - timedelta(days=7)

    def observation(at, rate):
        return Observation.objects.create(
            account_id=7,
            observed_at=at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=attribution_started_at,
            upstream_used_percent=10,
            interval_used_percent=10,
            raw_selected_total_cost=100,
            selected_total_cost=100,
            total_standard_cost=100,
            total_actual_cost=100,
            effective_usd_per_percent=Decimal(rate),
            raw_window={"rate_method": RATE_METHOD},
        )

    observation(base, "10")
    observation(base + timedelta(hours=2), "12")
    observation(base + timedelta(days=1), "14")
    observation(now, "16")

    hour = now.replace(minute=5, second=0, microsecond=0)
    ParticipantUsageSample.objects.create(
        participant=participant,
        account_id=7,
        attribution_started_at=attribution_started_at,
        observed_at=hour,
        balance_usd=Decimal("800"),
        selected_cost=10,
        raw_selected_cost=10,
    )
    ParticipantUsageSample.objects.create(
        participant=participant,
        account_id=7,
        attribution_started_at=attribution_started_at,
        observed_at=hour + timedelta(minutes=30),
        balance_usd=Decimal("760"),
        selected_cost=12,
        raw_selected_cost=12,
    )

    daily = client.get(
        "/api/statistics?capacity_period=day&capacity_days=365"
        "&usage_days=7&usage_precision=hour",
        **headers,
    ).json()["data"]
    assert daily["capacity_series"][-1]["weekly_total_usd"] == 1000.0
    assert daily["capacity_summary"]["cycle"]["estimate_usd"] == 1000.0
    assert daily["capacity_summary"]["today"]["sufficient"] is False
    assert daily["capacity_series"][-1]["daily_total_usd"] is None
    assert daily["capacity_series"][-1]["daily_basis"] is None
    assert len(daily["participant_series"][0]["points"]) == 1
    point = daily["participant_series"][0]["points"][0]
    assert point["account_cycle_usage_usd"] == 12.0
    assert point["balance_usd"] == 760.0

    monthly = client.get(
        "/api/statistics?capacity_period=month&capacity_days=365",
        **headers,
    ).json()["data"]
    base_month = base.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")
    month = next(
        item for item in monthly["capacity_series"] if item["period"] == base_month
    )
    assert month["weekly_total_usd"] == 1000.0
    assert month["minimum_usd"] == 1000.0
    assert month["maximum_usd"] == 1000.0
    assert month["sample_count"] == 2
    assert month["daily_total_usd"] is None
    assert month["daily_basis"] is None

@pytest.mark.django_db
def test_statistics_separates_cycle_and_daily_capacity_estimates():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    create_monitored_account(7)
    config.daily_estimate_min_percent_span = Decimal("5")
    config.save()

    now = timezone.now()
    local_day_start = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    reset_at = now + timedelta(days=5)
    attribution_started_at = now - timedelta(days=2)

    def observation(at, used_percent, cost):
        Observation.objects.create(
            account_id=7,
            observed_at=at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=attribution_started_at,
            upstream_used_percent=used_percent,
            interval_used_percent=used_percent,
            raw_selected_total_cost=cost,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            sample_usd_per_percent=Decimal(cost) / Decimal(used_percent),
            effective_usd_per_percent=Decimal("20"),
            valid_sample=True,
            raw_window={"rate_method": RATE_METHOD},
        )

    first_at = local_day_start + timedelta(minutes=5)
    last_at = local_day_start + timedelta(hours=20)
    observation(first_at, Decimal("10"), Decimal("200"))
    observation(last_at, Decimal("15"), Decimal("300"))

    result = client.get("/api/statistics", **headers).json()["data"]
    assert result["capacity_summary"]["cycle"]["estimate_usd"] == 2000.0
    assert result["capacity_summary"]["cycle"]["cost_usd"] == 300.0
    assert result["capacity_summary"]["cycle"]["start_cost_usd"] == 0.0
    assert result["capacity_summary"]["cycle"]["start_percent"] == 0.0
    assert result["capacity_summary"]["cycle"]["end_cost_usd"] == 300.0
    assert result["capacity_summary"]["cycle"]["end_percent"] == 15.0
    assert result["capacity_summary"]["cycle"]["raw_estimate_usd"] == 2000.0
    assert result["capacity_summary"]["cycle"]["rate_calculated"] is True
    closing_basis = result["capacity_series"][-1]["basis"]
    assert closing_basis["starts_at"] == attribution_started_at.isoformat()
    assert closing_basis["observed_at"] == last_at.astimezone(
        ZoneInfo("UTC")
    ).isoformat()
    assert closing_basis["end_cost_usd"] == 300.0
    assert closing_basis["end_percent"] == 15.0
    assert closing_basis["raw_estimate_usd"] == 2000.0
    assert closing_basis["estimate_usd"] == 2000.0
    daily_history = result["capacity_series"][-1]
    assert daily_history["daily_total_usd"] == 2000.0
    assert daily_history["daily_basis"] == {
        "observed_from": first_at.astimezone(ZoneInfo("UTC")).isoformat(),
        "observed_to": last_at.astimezone(ZoneInfo("UTC")).isoformat(),
        "start_cost_usd": 200.0,
        "start_cost_breakdown": {
            "sub2api_cost_usd": 200.0,
            "fast_correction_usd": 0.0,
            "total_cost_usd": 200.0,
        },
        "start_percent": 10.0,
        "end_cost_usd": 300.0,
        "end_cost_breakdown": {
            "sub2api_cost_usd": 300.0,
            "fast_correction_usd": 0.0,
            "total_cost_usd": 300.0,
        },
        "end_percent": 15.0,
        "cost_delta_usd": 100.0,
        "percent_delta": 5.0,
        "estimate_usd": 2000.0,
        "minimum_usd": 1666.67,
        "maximum_usd": 2500.0,
        "sample_count": 2,
        "min_percent_span": 5.0,
    }
    assert result["capacity_summary"]["today"] == {
        "estimate_usd": 2000.0,
        "minimum_usd": 1666.67,
        "maximum_usd": 2500.0,
        "start_cost_usd": 200.0,
        "start_cost_breakdown": {
            "sub2api_cost_usd": 200.0,
            "fast_correction_usd": 0.0,
            "total_cost_usd": 200.0,
        },
        "start_percent": 10.0,
        "end_cost_usd": 300.0,
        "end_cost_breakdown": {
            "sub2api_cost_usd": 300.0,
            "fast_correction_usd": 0.0,
            "total_cost_usd": 300.0,
        },
        "end_percent": 15.0,
        "cost_delta_usd": 100.0,
        "percent_delta": 5.0,
        "sample_count": 2,
        "observed_from": first_at.astimezone(ZoneInfo("UTC")).isoformat(),
        "observed_to": last_at.astimezone(ZoneInfo("UTC")).isoformat(),
        "min_percent_span": 5.0,
        "sufficient": True,
        "reason": "按今日已覆盖观测区间的成本增量与周限增量折算",
    }

    monthly = client.get(
        "/api/statistics?capacity_period=month",
        **headers,
    ).json()["data"]
    month_history = monthly["capacity_series"][-1]
    assert month_history["daily_total_usd"] == 2000.0
    assert month_history["daily_basis"] is None

@pytest.mark.django_db
def test_statistics_endpoint_formula_is_independent_of_quota_model():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    create_monitored_account(7)
    config.weekly_quota_model = "constant_average"
    config.save()

    now = timezone.now()
    attribution_started_at = now - timedelta(days=2)
    Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=5),
        attribution_started_at=attribution_started_at,
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("600"),
        selected_total_cost=Decimal("600"),
        total_standard_cost=Decimal("600"),
        total_actual_cost=Decimal("600"),
        sample_usd_per_percent=Decimal("30"),
        effective_usd_per_percent=Decimal("25"),
        valid_sample=True,
        raw_window={"rate_method": RATE_METHOD},
    )

    constant = client.get("/api/statistics", **headers).json()["data"]

    assert constant["capacity_summary"]["cycle"]["calculation_model"] == (
        "endpoint_ratio"
    )
    assert constant["capacity_summary"]["cycle"]["raw_estimate_usd"] == 3000.0
    assert constant["capacity_summary"]["cycle"]["estimate_usd"] == 3000.0
    assert (
        constant["capacity_summary"]["cycle"]["effective_usd_per_percent"]
        == 30.0
    )
    assert constant["capacity_series"][-1]["weekly_total_usd"] == 3000.0
    assert (
        constant["capacity_series"][-1]["basis"]["calculation_model"]
        == "endpoint_ratio"
    )
    assert constant["capacity_series"][-1]["basis"]["estimate_usd"] == 3000.0

    config.weekly_quota_model = "time_varying"
    config.save(update_fields=["weekly_quota_model"])
    time_varying = client.get("/api/statistics", **headers).json()["data"]

    assert time_varying["capacity_summary"]["cycle"]["calculation_model"] == (
        "endpoint_ratio"
    )
    assert time_varying["capacity_summary"]["cycle"]["estimate_usd"] == 3000.0
    assert time_varying["capacity_series"][-1]["weekly_total_usd"] == 3000.0



@pytest.mark.django_db
def test_api_key_usage_breakdown_uses_current_cycle_and_user_permissions(
    monkeypatch,
):
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    viewer = User.objects.create_user(
        username="viewer",
        password="Viewer-Access-2026!secure",
        email="viewer@example.com",
    )
    config = AppSettings.load()
    account = create_monitored_account(7)
    account.authorized_users.add(viewer)
    config.cost_basis = "actual"
    config.fast_correction_enabled = True
    config.save()
    participant = create_participant(
        name="车友",
        sub2api_user_id=22,
        share_percent=50,
    )
    participant.authorized_users.add(viewer)
    hidden = create_participant(
        name="未授权车友",
        sub2api_user_id=23,
        share_percent=50,
    )
    SystemUserPageAccess.objects.create(
        user=viewer,
        page_code=PagePermission.STATISTICS,
    )
    now = timezone.now()
    starts_at = now - timedelta(days=2)
    Observation.objects.create(
        account_id=7,
        observed_at=now - timedelta(minutes=5),
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=5),
        attribution_started_at=starts_at,
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("500"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )

    calls = {"keys": 0, "logs": 0}

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_user_api_keys(self, user_id):
            calls["keys"] += 1
            assert user_id == 22
            return [
                {"id": 1, "name": "主密钥", "status": "active"},
                {"id": 3, "name": "尚未使用", "status": "active"},
            ]

        def usage_logs(self, **kwargs):
            calls["logs"] += 1
            assert kwargs["account_id"] == 7
            assert kwargs["user_id"] == 22
            assert kwargs["started_at"] == starts_at
            assert kwargs["ended_at"] >= now
            return [
                Sub2APIUsageLog(
                    id=1,
                    user_id=22,
                    account_id=7,
                    created_at=now - timedelta(hours=2),
                    service_tier="priority",
                    total_cost=Decimal("75"),
                    actual_cost=Decimal("60"),
                    api_key_id=1,
                    api_key_name="主密钥",
                    model="gpt-5.6-codex",
                ),
                Sub2APIUsageLog(
                    id=2,
                    user_id=22,
                    account_id=7,
                    created_at=now - timedelta(hours=1),
                    service_tier="",
                    total_cost=Decimal("50"),
                    actual_cost=Decimal("40"),
                    api_key_id=2,
                    api_key_name="已删除密钥",
                ),
            ]

    monkeypatch.setattr("monitor.api_usage.Sub2APIClient", FakeClient)
    client = Client()
    headers, _ = jwt_login(
        client,
        username="viewer",
        password="Viewer-Access-2026!secure",
    )
    response = client.get(
        f"/api/statistics/participants/{participant.id}/api-usage",
        **headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["fast_correction_enabled"] is True
    assert data["starts_at"] == starts_at.isoformat()
    assert data["cost_basis"] == "actual"
    assert data["participant_total_usd"] == 115.0
    assert data["weekly_total_estimate_usd"] == 2000.0
    assert data["participant_weekly_percent"] == 5.75
    assert [
        (
            item["name"],
            item["usage_usd"],
            item["participant_usage_percent"],
            item["weekly_quota_percent"],
        )
        for item in data["api_keys"]
    ] == [
        ("主密钥", 75.0, 65.2174, 3.75),
        ("尚未使用", 0.0, 0.0, 0.0),
        ("已删除密钥", 40.0, 34.7826, 2.0),
    ]
    cached = client.get(
        f"/api/statistics/participants/{participant.id}/api-usage",
        **headers,
    ).json()["data"]
    assert cached == data
    assert calls == {"keys": 1, "logs": 1}
    assert ParticipantAPIUsageSnapshot.objects.count() == 1
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
    config.save(update_fields=["fast_correction_rules"])
    model_specific = client.get(
        f"/api/statistics/participants/{participant.id}/api-usage",
        **headers,
    ).json()["data"]
    assert model_specific["participant_total_usd"] == 100.0
    assert calls == {"keys": 2, "logs": 2}
    assert ParticipantAPIUsageSnapshot.objects.count() == 2

    config.fast_correction_enabled = False
    config.save(update_fields=["fast_correction_enabled"])
    uncorrected = client.get(
        f"/api/statistics/participants/{participant.id}/api-usage",
        **headers,
    ).json()["data"]
    assert uncorrected["fast_correction_enabled"] is False
    assert uncorrected["participant_total_usd"] == 100.0
    assert calls == {"keys": 3, "logs": 3}
    assert ParticipantAPIUsageSnapshot.objects.count() == 3
    assert (
        client.get(
            f"/api/statistics/participants/{hidden.id}/api-usage",
            **headers,
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_background_api_usage_refreshes_each_participant_at_most_hourly(
    monkeypatch,
):
    config = AppSettings.load()
    create_monitored_account(7)
    config.monitoring_enabled = True
    config.save()
    participant = create_participant(name="车友",
    sub2api_user_id=22,
    share_percent=50,)
    now = timezone.now()
    Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=5),
        attribution_started_at=now - timedelta(days=2),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    calls = {"keys": 0, "logs": 0}

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_user_api_keys(self, user_id):
            calls["keys"] += 1
            assert user_id == participant.sub2api_user_id
            return [{"id": 1, "name": "主密钥", "status": "active"}]

        def usage_logs(self, **kwargs):
            calls["logs"] += 1
            return []

    monkeypatch.setattr("monitor.api_usage.Sub2APIClient", FakeClient)

    first = refresh_due_api_usage_snapshots(config)
    second = refresh_due_api_usage_snapshots(config)

    assert first == {"refreshed": 1, "skipped": 0}
    assert second == {"refreshed": 0, "skipped": 1}
    assert calls == {"keys": 1, "logs": 1}
    assert ParticipantAPIUsageSnapshot.objects.filter(
        participant=participant
    ).count() == 1