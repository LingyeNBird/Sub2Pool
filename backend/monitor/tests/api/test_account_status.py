from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.integrations.sub2api import Sub2APIError, WeeklyWindow
from monitor.models import AppSettings, MonitoredAccount, Observation, PagePermission, SystemUserPageAccess
from monitor.secrets import encrypt_secret
from monitor.tests.helpers import create_monitored_account, jwt_login
from monitor.views.account_status import _fallback_usage


@pytest.mark.parametrize(
    ("used_percent", "reset_at"),
    [
        (Decimal("NaN"), 1787472000),
        (Decimal("10"), 10**100),
    ],
)
def test_account_status_rejects_invalid_fallback_window(
    used_percent,
    reset_at,
):
    window = WeeklyWindow(
        used_percent=used_percent,
        window_seconds=604800,
        reset_after_seconds=86400,
        reset_at=reset_at,
        slot="passive_snapshot",
    )
    with pytest.raises(Sub2APIError):
        _fallback_usage(window)


@pytest.mark.django_db
def test_account_status_returns_each_account_and_isolates_upstream_failures(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    first = create_monitored_account(7, name="A 主账号")
    second = create_monitored_account(8, name="B 备用账号", enabled=False)
    now = timezone.now()
    for account_id, age_days, actual, standard in (
        (7, 5, "3.25", "4.50"),
        (7, 12, "1.75", "2.50"),
        (7, 31, "99", "99"),
        (8, 3, "9", "12"),
    ):
        observed_at = now - timedelta(days=age_days)
        Observation.objects.create(
            account_id=account_id,
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=observed_at + timedelta(days=7),
            attribution_started_at=observed_at - timedelta(days=1),
            upstream_used_percent=Decimal("20"),
            raw_selected_total_cost=Decimal("100"),
            selected_total_cost=Decimal("100"),
            total_standard_cost=Decimal("100"),
            total_actual_cost=Decimal("100"),
            fast_correction_started_at=observed_at,
            fast_correction_request_count=1,
            fast_correction_actual_cost=Decimal(actual),
            fast_correction_standard_cost=Decimal(standard),
            effective_usd_per_percent=Decimal("20"),
        )


    class FakeClient:
        calls: list[tuple[str, int, object]] = []

        def __init__(self, received_config):
            assert received_config.pk == config.pk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def account_runtime_status(self, account_id):
            self.calls.append(("runtime", account_id, None))
            return {
                "name": f"上游 {account_id}",
                "account_type": "oauth",
                "status": "active" if account_id == 7 else "error",
                "schedulable": account_id == 7,
                "current_concurrency": 2 if account_id == 7 else 0,
                "concurrency_limit": 10,
                "last_used_at": "2026-08-19T08:00:00+00:00",
                "rate_limited_at": None,
                "rate_limit_reset_at": None,
                "overload_until": None,
                "temp_unschedulable_until": None,
                "temp_unschedulable_reason": None,
                "error_message": None if account_id == 7 else "授权失效",
            }

        def account_usage_status(self, account_id, *, source):
            self.calls.append(("usage", account_id, source))
            if account_id == 8:
                raise Sub2APIError("尚无被动额度快照")
            return {
                "source": "passive",
                "updated_at": "2026-08-19T08:00:00+00:00",
                "five_hour": None,
                "seven_day": {
                    "used_percent": 41.25,
                    "reset_at": "2026-08-23T08:00:00+00:00",
                    "remaining_seconds": 345600,
                    "request_count": 120,
                    "token_count": 456789,
                    "account_cost_usd": 18.75,
                    "standard_cost_usd": 15.0,
                    "user_cost_usd": 20.5,
                },
                "needs_verify": False,
                "is_banned": False,
                "needs_reauth": False,
                "error_code": None,
                "error": None,
            }

        def query_weekly_window(self, account_id, mode):
            self.calls.append(("window", account_id, mode))
            return WeeklyWindow(
                used_percent=Decimal("62.5"),
                window_seconds=604800,
                reset_after_seconds=172800,
                reset_at=1787472000,
                slot="passive_snapshot",
                sampled_at="2026-08-19T08:00:00+00:00",
            )

        def account_usage_stats(self, account_id, *, days):
            self.calls.append(("stats", account_id, days))
            return {
                "days": days,
                "actual_days_used": 12,
                "account_cost_usd": 81.25,
                "standard_cost_usd": 65.0,
                "user_cost_usd": 90.0,
                "request_count": 730 + account_id,
                "token_count": 3456789,
                "avg_daily_cost_usd": 6.77,
                "avg_daily_request_count": 60.8,
                "avg_daily_token_count": 288065.75,
                "avg_duration_ms": 1240.5,
                "today": None,
            }

    monkeypatch.setattr(
        "monitor.views.account_status.Sub2APIClient",
        FakeClient,
    )
    client = Client()
    headers, _response = jwt_login(client)
    response = client.get("/api/account-status", **headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["configured"] is True
    assert data["stats_days"] == 30
    assert data["connection_error"] is None
    assert [item["id"] for item in data["accounts"]] == [first.id, second.id]

    first_row, second_row = data["accounts"]
    assert second_row["enabled"] is False
    assert second_row["runtime"]["error_message"] == "授权失效"
    assert second_row["usage"]["source"] == "passive_snapshot"
    assert second_row["usage"]["seven_day"]["used_percent"] == 62.5
    assert second_row["stats"]["request_count"] == 738
    assert second_row["warnings"] == []

    assert first_row["usage"]["seven_day"]["used_percent"] == 41.25
    assert first_row["usage"]["seven_day"]["account_cost_usd"] == 18.75
    assert first_row["stats"]["token_count"] == 3456789
    assert first_row["stats"]["fast_correction_usd"] == 5.0
    assert first_row["stats"]["account_cost_with_fast_correction_usd"] == 86.25
    assert second_row["stats"]["fast_correction_usd"] == 9.0
    assert second_row["stats"]["account_cost_with_fast_correction_usd"] == 90.25

    assert first_row["warnings"] == []
    assert FakeClient.calls == [
        ("runtime", 7, None),
        ("usage", 7, "passive"),
        ("stats", 7, 30),
        ("runtime", 8, None),
        ("usage", 8, "passive"),
        ("window", 8, "passive"),
        ("stats", 8, 30),
    ]


@pytest.mark.django_db
def test_account_status_requires_page_permission():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="viewer",
        password="viewer-password",
    )
    anonymous = Client()
    assert anonymous.get("/api/account-status").status_code == 401

    client = Client()
    headers, _response = jwt_login(
        client,
        username="viewer",
        password="viewer-password",
    )
    assert client.get("/api/account-status", **headers).status_code == 403

    SystemUserPageAccess.objects.create(
        user=user,
        page_code=PagePermission.ACCOUNT_STATUS,
    )
    assert client.get("/api/account-status", **headers).status_code == 200
