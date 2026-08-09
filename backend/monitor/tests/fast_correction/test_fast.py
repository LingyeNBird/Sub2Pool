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
from monitor.sub2api import (
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
def test_sampling_applies_fast_correction_for_all_sub2api_users(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.cost_basis = "actual"
    config.fast_correction_enabled = True
    config.save()
    participant = Participant.objects.create(
        name="已配置参与者",
        sub2api_user_id=51,
        share_percent=100,
    )
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
                ),
                Sub2APIUsageLog(
                    2,
                    52,
                    7,
                    ended_at - timedelta(minutes=2),
                    "priority",
                    Decimal("20"),
                    Decimal("20"),
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
    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    observation = Observation.objects.get()
    assert observation.fast_correction_standard_cost == Decimal("25")
    assert observation.fast_correction_actual_cost == Decimal("25")
    assert observation.selected_total_cost == Decimal("225")
    corrections = list(
        ObservationFastCorrection.objects.order_by("sub2api_user_id")
    )
    assert [row.sub2api_user_id for row in corrections] == [51, 52]
    assert [row.actual_correction_cost for row in corrections] == [
        Decimal("20"),
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
    assert detail["correction_usd"] == 25.0
    assert detail["corrected_fast_cost_usd"] == 125.0
    assert detail["sub2api_fast_multiplier"] == 2.0
    assert detail["upstream_fast_multiplier"] == 2.5
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
            "correction_usd": 20.0,
            "corrected_fast_cost_usd": 100.0,
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
    assert snapshot.selected_cost == Decimal("170")

@pytest.mark.django_db
def test_disabled_fast_correction_skips_log_reads_and_preserves_null_interval(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.fast_correction_enabled = False
    config.save()
    Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=100,
    )
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
    run_monitor(force_upstream=True, source="manual")

    observation = Observation.objects.get()
    assert observation.fast_correction_standard_cost is None
    assert observation.fast_correction_actual_cost is None
    assert observation.selected_total_cost == Decimal("100")

@pytest.mark.django_db
def test_fast_correction_rebuild_api_fills_missing_cycle_and_replays(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.fast_correction_enabled = True
    config.cost_basis = "actual"
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=100,
    )
    cycle_start = timezone.now().replace(microsecond=0) - timedelta(days=2)
    reset_at = cycle_start + timedelta(days=7)
    first_at = cycle_start + timedelta(hours=1)
    second_at = cycle_start + timedelta(hours=2)

    def observation_at(observed_at, used_percent, cost):
        observation = Observation.objects.create(
            account_id=7,
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=cycle_start,
            upstream_used_percent=used_percent,
            interval_used_percent=used_percent,
            raw_selected_total_cost=cost,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            effective_usd_per_percent=Decimal("10"),
        )
        ParticipantSnapshot.objects.create(
            observation=observation,
            participant=participant,
            raw_selected_cost=cost,
            selected_cost=cost,
            remaining_share_percent=Decimal("100") - used_percent,
        )
        return observation

    first = observation_at(first_at, Decimal("10"), Decimal("100"))
    second = observation_at(second_at, Decimal("20"), Decimal("200"))

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def usage_logs(self, **kwargs):
            assert kwargs["started_at"] == cycle_start
            assert kwargs["ended_at"] == second_at
            return [
                Sub2APIUsageLog(
                    1,
                    51,
                    7,
                    first_at + timedelta(minutes=1),
                    "priority",
                    Decimal("100"),
                    Decimal("100"),
                )
            ]

    monkeypatch.setattr(
        "monitor.fast_correction.Sub2APIClient",
        FakeClient,
    )
    client = Client()
    headers, _ = jwt_login(client)
    settings_before = client.get("/api/settings", **headers).json()["data"]
    assert settings_before["fast_correction_rebuild_recommended"] is True
    assert settings_before["fast_correction_missing_intervals"] == 2

    response = client.post(
        "/api/settings/fast-correction/rebuild",
        data=json.dumps({"scope": "cycle"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["rebuilt_observations"] == 2
    assert result["fast_request_count"] == 1
    assert result["correction_usd"] == 25.0
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.fast_correction_actual_cost == Decimal("0")
    assert second.fast_correction_actual_cost == Decimal("25")
    assert second.selected_total_cost == Decimal("225")
    assert ParticipantSnapshot.objects.get(
        observation=second,
        participant=participant,
    ).selected_cost == Decimal("225")
    settings_after = client.get("/api/settings", **headers).json()["data"]
    assert settings_after["fast_correction_rebuild_recommended"] is False
    assert settings_after["fast_correction_missing_intervals"] == 0
    observations = client.get("/api/observations", **headers).json()["data"]
    assert observations["fast_correction_enabled"] is True
    assert observations["items"][0]["fast_correction_usd"] == 25.0
    # 混合历史中未计算的区间必须按 0 累加，不能阻断后续已计算修正。
    first.fast_correction_standard_cost = None
    first.fast_correction_actual_cost = None
    first.save(
        update_fields=[
            "fast_correction_standard_cost",
            "fast_correction_actual_cost",
        ]
    )
    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["fast_correction_enabled"] is True
    assert dashboard["cycle"]["start_cost_breakdown"] == {
        "sub2api_cost_usd": 0.0,
        "fast_correction_usd": 0.0,
        "total_cost_usd": 0.0,
    }
    assert dashboard["cycle"]["selected_total_cost_breakdown"] == {
        "sub2api_cost_usd": 200.0,
        "fast_correction_usd": 25.0,
        "total_cost_usd": 225.0,
    }
    assert dashboard["cycle"]["rate_samples"][0]["cost_breakdown"] == {
        "sub2api_cost_usd": 200.0,
        "fast_correction_usd": 25.0,
        "total_cost_usd": 225.0,
    }

    statistics = client.get("/api/statistics", **headers).json()["data"]
    assert statistics["fast_correction_enabled"] is True
    assert statistics["capacity_summary"]["cycle"]["end_cost_breakdown"] == {
        "sub2api_cost_usd": 200.0,
        "fast_correction_usd": 25.0,
        "total_cost_usd": 225.0,
    }
    # 首个区间尚未计算出 FAST 请求时按 0 展示，后续累计修正仍保持可追溯。
    assert statistics["capacity_summary"]["cycle"]["rate_samples"][-1][
        "cost_breakdown"
    ] == {
        "sub2api_cost_usd": 100.0,
        "fast_correction_usd": 0.0,
        "total_cost_usd": 100.0,
    }

    config.fast_correction_enabled = False
    config.save(update_fields=["fast_correction_enabled"])
    dashboard_without_breakdown = client.get(
        "/api/dashboard",
        **headers,
    ).json()["data"]
    statistics_without_breakdown = client.get(
        "/api/statistics",
        **headers,
    ).json()["data"]
    assert dashboard_without_breakdown["fast_correction_enabled"] is False
    assert statistics_without_breakdown["fast_correction_enabled"] is False
