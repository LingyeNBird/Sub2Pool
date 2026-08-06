import json
import sqlite3
from io import BytesIO

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo
import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
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
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    QuotaCycle,
)
from monitor.notifications import send_notification
from monitor.secrets import encrypt_secret
from monitor.sub2api import (
    Sub2APIClient,
    Sub2APIError,
    UsageStats,
    UserBalance,
    WeeklyWindow,
)
from monitor import database_transfer

def jwt_login(
    client: Client,
    username: str = "owner",
    password: str = "very-strong-password",
    **extra,
) -> tuple[dict[str, str], object]:
    response = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": username,
                "password": password,
                **extra,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    access = response.json()["data"]["access"]
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}, response

def create_recommendation_snapshot(
    participant: Participant,
    recommended: Decimal = Decimal("123.45"),
) -> ParticipantSnapshot:
    now = timezone.now()
    cycle = QuotaCycle.objects.create(
        account_id=7,
        window_seconds=604800,
        starts_at=now - timedelta(days=3),
        resets_at=now + timedelta(days=4),
    )
    observation = Observation.objects.create(
        cycle=cycle,
        observed_at=now,
        upstream_used_percent=Decimal("20"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    return ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        selected_cost=Decimal("200"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=recommended,
        balance_difference_usd=recommended - Decimal("80"),
        needs_manual_update=True,
    )


@pytest.mark.django_db
def test_default_query_mode_is_passive():
    assert AppSettings.load().quota_query_mode == "passive"


@pytest.mark.django_db
def test_passive_quota_reads_account_snapshot_without_official_quota_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_paths: list[str] = []
    reset_at = timezone.now() + timedelta(days=3)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "platform": "openai",
                    "extra": {
                        "codex_7d_used_percent": 37.5,
                        "codex_7d_reset_at": reset_at.isoformat(),
                        "codex_usage_updated_at": timezone.now().isoformat(),
                    },
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"x-api-key": "admin-secret"})
        window = client.query_weekly_window(42, "passive")

    assert window.used_percent == Decimal("37.5")
    assert requested_paths == ["/api/v1/admin/accounts/42"]
    assert not any("/openai/" in path for path in requested_paths)


@pytest.mark.django_db
def test_openai_account_discovery_uses_filtered_paginated_admin_api():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/accounts"
        assert request.url.params["platform"] == "openai"
        assert request.url.params["lite"] == "true"
        assert request.headers["x-api-key"] == "admin-secret"
        page = int(request.url.params["page"])
        requested_pages.append(page)
        item = (
            {
                "id": 41,
                "name": "GPT Pro 主账号",
                "platform": "openai",
                "type": "oauth",
                "status": "active",
                "schedulable": True,
            }
            if page == 1
            else {
                "id": 42,
                "name": "备用账号",
                "platform": "openai",
                "type": "oauth",
                "status": "disabled",
                "schedulable": False,
            }
        )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [item],
                    "total": 2,
                    "page": page,
                    "page_size": 1,
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
        accounts = client.list_openai_accounts()

    assert requested_pages == [1, 2]
    assert accounts == [
        {
            "id": 41,
            "name": "GPT Pro 主账号",
            "type": "oauth",
            "status": "active",
            "schedulable": True,
        },
        {
            "id": 42,
            "name": "备用账号",
            "type": "oauth",
            "status": "disabled",
            "schedulable": False,
        },
    ]


@pytest.mark.django_db
def test_sub2api_user_discovery_includes_admin_accounts():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/users"
        assert request.url.params["include_subscriptions"] == "false"
        assert "role" not in request.url.params
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "items": [
                        {
                            "id": 51,
                            "email": "rider@example.com",
                            "username": "rider",
                            "status": "active",
                            "role": "admin",
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 100,
                    "pages": 1,
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        users = client.list_users()

    assert users == [
        {
            "id": 51,
            "email": "rider@example.com",
            "username": "rider",
            "status": "active",
            "role": "admin",
        }
    ]


@pytest.mark.django_db
def test_user_balance_reads_user_detail_without_platform_quota_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "id": 51,
                    "balance": "1597.096606",
                    "frozen_balance": "2.5",
                },
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        balance = client.user_balance(51)

    assert balance == UserBalance(
        balance=Decimal("1597.096606"),
        frozen_balance=Decimal("2.5"),
    )
    assert requested_paths == ["/api/v1/admin/users/51"]


@pytest.mark.django_db
def test_recommendation_balance_write_uses_dedicated_sub2api_endpoint():
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    requested: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requested["method"] = request.method
        requested["path"] = request.url.path
        requested["api_key"] = request.headers["x-api-key"]
        requested["idempotency_key"] = request.headers["idempotency-key"]
        requested["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"id": 51, "balance": 123.45},
            },
        )

    with Sub2APIClient(config) as client:
        client.client.close()
        client.client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"x-api-key": "admin-secret"},
        )
        confirmed = client.set_user_balance_from_recommendation(
            51,
            Decimal("123.45"),
        )

    assert confirmed == Decimal("123.45")
    assert requested["method"] == "POST"
    assert requested["path"] == "/api/v1/admin/users/51/balance"
    assert requested["api_key"] == "admin-secret"
    assert requested["idempotency_key"]
    assert requested["body"] == {
        "balance": 123.45,
        "operation": "set",
        "notes": "Sub2Pool 一键应用额度建议",
    }


@pytest.mark.django_db
def test_apply_recommendation_updates_balance_and_hides_current_snapshot(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_base_url = "https://admin.example:8443/internal/path"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
        latest_balance_usd=Decimal("80"),
    )
    snapshot = create_recommendation_snapshot(participant)
    captured: dict = {}

    class FakeClient:
        def __init__(self, received_config):
            assert received_config.pk == config.pk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, user_id, balance):
            captured.update(user_id=user_id, balance=balance)
            return balance

    monkeypatch.setattr("monitor.views.dashboard.Sub2APIClient", FakeClient)
    client = Client()
    headers, _ = jwt_login(client)

    applied = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert applied.status_code == 200
    assert applied.json()["data"]["applied_balance_usd"] == 123.45
    assert captured == {"user_id": 51, "balance": Decimal("123.45")}
    snapshot.refresh_from_db()
    participant.refresh_from_db()
    assert snapshot.recommendation_applied is True
    assert snapshot.needs_manual_update is False
    assert snapshot.current_balance_usd == Decimal("123.45")
    assert snapshot.balance_difference_usd == Decimal("0")
    assert participant.latest_balance_usd == Decimal("123.45")

    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["sub2api_admin_url"] == "https://admin.example:8443"
    assert dashboard["participants"] == []


@pytest.mark.django_db
def test_apply_recommendation_failure_keeps_snapshot_actionable(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
    )
    snapshot = create_recommendation_snapshot(participant)

    class FailingClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def set_user_balance_from_recommendation(self, _user_id, _balance):
            raise Sub2APIError("上游拒绝更新")

    monkeypatch.setattr(
        "monitor.views.dashboard.Sub2APIClient",
        FailingClient,
    )
    client = Client()
    headers, _ = jwt_login(client)

    failed = client.post(
        f"/api/dashboard/participants/{participant.id}/apply-recommendation",
        **headers,
    )

    assert failed.status_code == 502
    assert failed.json()["message"] == "上游拒绝更新"
    snapshot.refresh_from_db()
    assert snapshot.recommendation_applied is False
    assert snapshot.needs_manual_update is True


@pytest.mark.django_db
def test_initial_observation_conserves_percent_and_builds_manual_recommendations(monkeypatch):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.quota_query_mode = "passive"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = Participant.objects.create(name="车主", sub2api_user_id=1, share_percent=50, is_owner=True)
    rider = Participant.objects.create(name="车友", sub2api_user_id=2, share_percent=50)
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, account_id, mode):
            assert account_id == 7
            assert mode == "passive"
            return WeeklyWindow(Decimal("40"), 604800, 345600, int(reset_at.timestamp()), "passive_snapshot")

        def usage_stats(self, *, user_id=None, **_kwargs):
            costs = {None: Decimal("400"), 1: Decimal("300"), 2: Decimal("100")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("300") if user_id == 1 else Decimal("100")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["status"] == "calibrated"
    snapshots = {item.participant_id: item for item in ParticipantSnapshot.objects.all()}
    assert snapshots[owner.id].charged_cycle_percent == Decimal("30")
    assert snapshots[rider.id].charged_cycle_percent == Decimal("10")
    assert sum((item.charged_cycle_percent for item in snapshots.values()), Decimal("0")) == Decimal("40")
    assert snapshots[owner.id].recommended_balance_usd == Decimal("190.00")
    assert snapshots[rider.id].recommended_balance_usd == Decimal("380.00")
    assert ParticipantUsageSample.objects.count() == 2


@pytest.mark.django_db
def test_integer_percent_plateau_uses_cumulative_cost_for_capacity(monkeypatch):
    """16% 平台期内的消费不能在跳到 17% 时被漏掉并产生 $687 的错误总额。"""
    config = AppSettings.load()
    config.openai_account_id = 7
    config.cost_basis = "actual"
    config.initial_usd_per_percent = Decimal("16")
    config.safety_factor = Decimal("0.95")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    reset_at = timezone.now() + timedelta(days=4)
    used_values = [Decimal("16"), Decimal("16"), Decimal("17")]
    cost_values = [
        Decimal("419.409971"),
        Decimal("431.558149"),
        Decimal("438.431382"),
    ]

    class FakeClient:
        run_count = 0

        def __init__(self, _config):
            self.step = type(self).run_count
            type(self).run_count += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                used_values[self.step],
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            cost = cost_values[self.step]
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1561.568618"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    for _ in range(3):
        run_monitor(force_upstream=True, source="manual")

    observations = list(Observation.objects.order_by("observed_at"))
    assert observations[0].sample_usd_per_percent == Decimal("26.213123")
    assert observations[1].sample_usd_per_percent is None
    assert observations[2].delta_cost == Decimal("6.873233")
    assert observations[2].sample_usd_per_percent == Decimal("25.790081")
    assert observations[2].effective_usd_per_percent == Decimal("25.790081")
    assert observations[2].raw_window["rate_method"] == "cumulative_cycle_v1"

    snapshot = ParticipantSnapshot.objects.get(
        observation=observations[2],
        participant=owner,
    )
    assert snapshot.charged_cycle_percent == Decimal("17")
    assert snapshot.remaining_share_percent == Decimal("33")
    assert snapshot.recommended_balance_usd == Decimal("808.52")


@pytest.mark.django_db
def test_passive_reset_timestamp_drift_keeps_the_same_cycle(monkeypatch):
    """被动快照重置时间漂移几十秒时不能误建一个新的官方周期。"""
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    reset_at = timezone.now() + timedelta(days=4)

    class FakeClient:
        run_count = 0

        def __init__(self, _config):
            self.step = type(self).run_count
            type(self).run_count += 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("10") + self.step,
                604800,
                345600,
                int((reset_at + timedelta(seconds=70 * self.step)).timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            cost = Decimal("100") + Decimal("10") * self.step
            return UsageStats(cost, cost)

        def user_balance(self, _user_id):
            return UserBalance(Decimal("1000"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(force_upstream=True, source="manual")
    run_monitor(force_upstream=True, source="manual")

    assert QuotaCycle.objects.count() == 1
    assert Observation.objects.count() == 2


@pytest.mark.django_db
def test_midcycle_initialization_assigns_existing_ten_percent_to_owner(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.initial_usd_per_percent = Decimal("16")
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=2,
        share_percent=50,
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
            costs = {None: Decimal("100"), 1: Decimal("100"), 2: Decimal("0")}
            return UsageStats(costs[user_id], costs[user_id])

        def user_balance(self, user_id):
            balance = Decimal("700") if user_id == 1 else Decimal("800")
            return UserBalance(balance, Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    run_monitor(force_upstream=True, source="manual")

    snapshots = {
        item.participant_id: item for item in ParticipantSnapshot.objects.all()
    }
    assert snapshots[owner.id].charged_cycle_percent == Decimal("10")
    assert snapshots[owner.id].remaining_share_percent == Decimal("40")
    assert snapshots[rider.id].charged_cycle_percent == Decimal("0")
    assert snapshots[rider.id].remaining_share_percent == Decimal("50")


@pytest.mark.django_db
def test_manual_upstream_refresh_starts_new_cycle_without_negative_ledger(
    monkeypatch,
):
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    now = timezone.now()
    reset_at = now + timedelta(days=4)
    old_cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=reset_at - timedelta(days=7),
        resets_at=reset_at,
        active=True,
    )
    previous = Observation.objects.create(
        cycle=old_cycle,
        source="manual",
        observed_at=now - timedelta(hours=1),
        upstream_used_percent=Decimal("10"),
        selected_total_cost=Decimal("100"),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
        effective_usd_per_percent=Decimal("10"),
    )
    ParticipantSnapshot.objects.create(
        observation=previous,
        participant=owner,
        selected_cost=Decimal("100"),
        charged_delta_percent=Decimal("10"),
        charged_cycle_percent=Decimal("10"),
        remaining_share_percent=Decimal("40"),
        current_balance_usd=Decimal("500"),
        recommended_balance_usd=Decimal("500"),
    )

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def query_weekly_window(self, _account_id, _mode):
            return WeeklyWindow(
                Decimal("0"),
                604800,
                345600,
                int(reset_at.timestamp()),
                "passive_snapshot",
            )

        def usage_stats(self, **_kwargs):
            return UsageStats(Decimal("0"), Decimal("0"))

        def user_balance(self, _user_id):
            return UserBalance(Decimal("500"), Decimal("0"))

    monkeypatch.setattr("monitor.engine.Sub2APIClient", FakeClient)
    result = run_monitor(force_upstream=True, source="manual")

    assert result["reason"] == "检测到官方手动刷新"
    old_cycle.refresh_from_db()
    assert old_cycle.active is False
    current = QuotaCycle.objects.get(active=True)
    assert current.id != old_cycle.id
    snapshot = ParticipantSnapshot.objects.get(observation__cycle=current)
    assert snapshot.charged_delta_percent == Decimal("0")
    assert snapshot.charged_cycle_percent == Decimal("0")
    assert snapshot.remaining_share_percent == Decimal("50")
    assert snapshot.delta_cost is None


@pytest.mark.django_db
def test_api_requires_admin_jwt_and_accepts_admin_login():
    user = get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    unauthorized = client.get("/api/dashboard")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["ok"] is False

    headers, logged_in = jwt_login(client)
    assert "access" in logged_in.json()["data"]
    assert "refresh" not in logged_in.json()["data"]
    assert logged_in.cookies["pinche_refresh"]["httponly"]
    assert "sessionid" not in logged_in.cookies
    assert logged_in.json()["data"]["timezone"] == "Asia/Shanghai"
    assert client.get("/api/auth/me", **headers).json()["data"]["timezone"] == (
        "Asia/Shanghai"
    )
    config = AppSettings.load()
    config.last_upstream_check_at = timezone.now() - timedelta(hours=13)
    config.stale_warning_hours = 12
    config.save(
        update_fields=["last_upstream_check_at", "stale_warning_hours", "updated_at"]
    )

    dashboard = client.get("/api/dashboard", **headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["quota_query_mode"] == "passive"
    assert dashboard.json()["data"]["snapshot_stale"] is True
    assert user.is_staff

@pytest.mark.django_db
def test_regular_user_only_reads_bound_participant_statistics():
    User = get_user_model()
    User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    first = Participant.objects.create(
        name="甲",
        sub2api_user_id=101,
        sub2api_username="rider-a",
        share_percent=50,
    )
    second = Participant.objects.create(
        name="乙",
        sub2api_user_id=102,
        sub2api_username="rider-b",
        share_percent=50,
    )
    client = Client()
    admin_headers, _ = jwt_login(client)

    created = client.post(
        "/api/system-users",
        data=json.dumps(
            {
                "username": "rider-viewer",
                "email": "viewer@example.com",
                "password": "Rider-Access-2026!secure",
                "is_active": True,
                "participant_ids": [first.id, second.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert created.status_code == 201
    user_id = created.json()["data"]["id"]
    regular = User.objects.get(pk=user_id)
    assert regular.is_staff is False
    assert list(
        regular.quota_participants.order_by("id").values_list("id", flat=True)
    ) == [first.id, second.id]

    config = AppSettings.load()
    config.openai_account_id = 7
    config.save()
    now = timezone.now()
    cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=now - timedelta(days=2),
        resets_at=now + timedelta(days=5),
    )
    for participant, cost in ((first, 120), (second, 240)):
        ParticipantUsageSample.objects.create(
            participant=participant,
            cycle=cycle,
            observed_at=now,
            balance_usd=Decimal("500"),
            selected_cost=cost,
        )

    regular_client = Client()
    regular_headers, logged_in = jwt_login(
        regular_client,
        username="rider-viewer",
        password="Rider-Access-2026!secure",
    )
    assert logged_in.json()["data"]["is_staff"] is False
    assert regular_client.get("/api/auth/me", **regular_headers).json()["data"][
        "is_staff"
    ] is False

    statistics = regular_client.get("/api/statistics", **regular_headers)
    assert statistics.status_code == 200
    assert [
        item["participant_id"]
        for item in statistics.json()["data"]["participant_series"]
    ] == [first.id, second.id]
    for admin_path in (
        "/api/dashboard",
        "/api/participants",
        "/api/login-events",
        "/api/settings",
        "/api/system-users",
    ):
        assert regular_client.get(admin_path, **regular_headers).status_code == 403

    updated = client.patch(
        f"/api/system-users/{user_id}",
        data=json.dumps(
            {
                "username": "rider-viewer",
                "email": "viewer@example.com",
                "is_active": True,
                "participant_ids": [second.id],
            }
        ),
        content_type="application/json",
        **admin_headers,
    )
    assert updated.status_code == 200
    filtered = regular_client.get("/api/statistics", **regular_headers)
    assert [
        item["participant_id"]
        for item in filtered.json()["data"]["participant_series"]
    ] == [second.id]

    deleted = client.delete(
        f"/api/system-users/{user_id}",
        **admin_headers,
    )
    assert deleted.status_code == 200
    assert not User.objects.filter(pk=user_id).exists()

@pytest.mark.django_db
def test_system_user_validation_returns_field_errors():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    participant = Participant.objects.create(
        name="甲",
        sub2api_user_id=101,
        share_percent=100,
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.post(
        "/api/system-users",
        data=json.dumps(
            {
                "username": "viewer",
                "email": "viewer@example.com",
                "password": "123",
                "is_active": True,
                "participant_ids": [participant.id],
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["message"] == "系统用户校验失败"
    assert payload["details"]["password"]
    assert not get_user_model().objects.filter(username="viewer").exists()

@pytest.mark.django_db
def test_refresh_rotation_blacklists_old_cookie_and_logout_clears_current_cookie():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    _, logged_in = jwt_login(client)
    old_refresh = logged_in.cookies["pinche_refresh"].value

    refreshed = client.post(
        "/api/auth/refresh",
        data="{}",
        content_type="application/json",
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["data"]["access"]
    new_refresh = refreshed.cookies["pinche_refresh"].value
    assert new_refresh != old_refresh

    replay = Client()
    replay.cookies["pinche_refresh"] = old_refresh
    assert replay.post("/api/auth/refresh").status_code == 401

    logout = client.post(
        "/api/auth/logout",
        HTTP_AUTHORIZATION=f"Bearer {new_access}",
    )
    assert logout.status_code == 200
    assert logout.cookies["pinche_refresh"]["max-age"] == 0
    assert client.post("/api/auth/refresh").status_code == 401


@pytest.mark.django_db
def test_password_change_reissues_tokens_and_revokes_old_access():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    old_headers, _ = jwt_login(client)

    changed = client.post(
        "/api/auth/password",
        data=json.dumps(
            {
                "old_password": "very-strong-password",
                "new_password": "another-very-strong-password",
            }
        ),
        content_type="application/json",
        **old_headers,
    )
    assert changed.status_code == 200
    new_access = changed.json()["data"]["access"]
    assert client.get("/api/auth/me", **old_headers).status_code == 401
    assert (
        client.get(
            "/api/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {new_access}",
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_settings_round_trip_accepts_internal_docker_url_and_decimal_values():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    headers, _ = jwt_login(client)
    payload = client.get("/api/settings", **headers).json()["data"]
    payload["local_poll_minutes"] = 11
    response = client.patch(
        "/api/settings",
        data=json.dumps(payload),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    config = AppSettings.load()
    assert config.sub2api_base_url == "http://host.docker.internal:8080"
    assert config.safety_factor == Decimal("0.95")
    assert config.local_poll_minutes == 11


@pytest.mark.django_db
def test_partial_settings_patch_does_not_touch_other_cards():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example"
    config.smtp_host = "smtp.original.example"
    config.save()
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"local_poll_minutes": 17}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    config.refresh_from_db()
    assert config.local_poll_minutes == 17
    assert config.sub2api_base_url == "https://sub2api.example"
    assert config.smtp_host == "smtp.original.example"


@pytest.mark.django_db
def test_settings_rejects_invalid_iana_timezone():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.patch(
        "/api/settings",
        data=json.dumps({"timezone": "Shanghai"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert response.json()["details"]["timezone"]
    assert AppSettings.load().timezone == "Asia/Shanghai"

@pytest.mark.django_db
def test_global_monitor_schedule_records_next_wake_time():
    config = AppSettings.load()
    config.local_poll_minutes = 13
    config.save()
    now = timezone.now()

    sleep_seconds = schedule_next_run(config, now=now)

    config.refresh_from_db()
    assert sleep_seconds == 13 * 60
    assert config.next_local_check_at == now + timedelta(minutes=13)

@pytest.mark.django_db
def test_global_monitor_schedule_does_not_add_run_duration_to_interval():
    config = AppSettings.load()
    config.local_poll_minutes = 10
    config.save()
    cycle_started_at = timezone.now()

    sleep_seconds = schedule_next_run(
        config,
        now=cycle_started_at + timedelta(minutes=3),
        cycle_started_at=cycle_started_at,
    )

    config.refresh_from_db()
    assert sleep_seconds == 7 * 60
    assert config.next_local_check_at == cycle_started_at + timedelta(minutes=10)


@pytest.mark.django_db
def test_global_monitor_schedule_skips_elapsed_slots_after_slow_run():
    config = AppSettings.load()
    config.local_poll_minutes = 10
    config.save()
    cycle_started_at = timezone.now()

    sleep_seconds = schedule_next_run(
        config,
        now=cycle_started_at + timedelta(minutes=25),
        cycle_started_at=cycle_started_at,
    )

    config.refresh_from_db()
    assert sleep_seconds == 5 * 60
    assert config.next_local_check_at == cycle_started_at + timedelta(minutes=30)


@pytest.mark.django_db
def test_monitor_status_exposes_global_countdown_and_hides_it_when_disabled():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    config = AppSettings.load()
    now = timezone.now()
    config.monitoring_enabled = True
    config.local_poll_minutes = 10
    config.next_local_check_at = now + timedelta(minutes=7)
    config.run_lease_until = now + timedelta(minutes=1)
    config.save()

    enabled = client.get("/api/monitor/run", **headers)

    assert enabled.status_code == 200
    data = enabled.json()["data"]
    assert data["monitoring_enabled"] is True
    assert data["interval_seconds"] == 600
    assert data["next_local_check_at"] == config.next_local_check_at.isoformat()
    assert data["server_time"]
    assert data["run_in_progress"] is True

    config.monitoring_enabled = False
    config.save(update_fields=["monitoring_enabled"])
    disabled = client.get("/api/monitor/run", **headers).json()["data"]
    assert disabled["monitoring_enabled"] is False
    assert disabled["next_local_check_at"] is None


@pytest.mark.django_db
def test_account_discovery_uses_unsaved_address_and_token(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    captured: dict = {}

    class FakeClient:
        def __init__(self, config, **overrides):
            captured["saved_url"] = config.sub2api_base_url
            captured.update(overrides)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_openai_accounts(self):
            return [
                {
                    "id": 88,
                    "name": "测试账号",
                    "type": "oauth",
                    "status": "active",
                    "schedulable": True,
                }
            ]

    monkeypatch.setattr("monitor.views.settings.Sub2APIClient", FakeClient)
    response = client.post(
        "/api/settings/openai-accounts",
        data=json.dumps(
            {
                "sub2api_base_url": "http://unsaved-sub2api:8088",
                "sub2api_admin_token": "unsaved-admin-token",
                "request_timeout_seconds": 37,
                "verify_tls": False,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == 88
    assert captured == {
        "saved_url": "http://host.docker.internal:8080",
        "base_url": "http://unsaved-sub2api:8088",
        "admin_token": "unsaved-admin-token",
        "request_timeout_seconds": 37,
        "verify_tls": False,
    }
    config = AppSettings.load()
    assert config.sub2api_base_url == "http://host.docker.internal:8080"
    assert config.sub2api_admin_token_encrypted == ""


@pytest.mark.django_db
def test_connection_test_uses_unsaved_form_without_persisting(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    captured: dict = {}

    class FakeClient:
        def __init__(self, _config, **overrides):
            captured.update(overrides)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def test_connection(self, account_id, mode):
            captured["account_id"] = account_id
            captured["mode"] = mode
            return {"users_api": "ok", "quota_api": "ok"}

    monkeypatch.setattr("monitor.views.settings.Sub2APIClient", FakeClient)
    response = client.post(
        "/api/settings/test-sub2api",
        data=json.dumps(
            {
                "sub2api_base_url": "https://new-sub2api.example",
                "sub2api_admin_token": "new-admin-token",
                "openai_account_id": 91,
                "quota_query_mode": "direct",
                "request_timeout_seconds": 25,
                "verify_tls": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert captured == {
        "base_url": "https://new-sub2api.example",
        "admin_token": "new-admin-token",
        "request_timeout_seconds": 25,
        "verify_tls": True,
        "account_id": 91,
        "mode": "direct",
    }
    config = AppSettings.load()
    assert config.openai_account_id is None
    assert config.quota_query_mode == "passive"


@pytest.mark.django_db
def test_participant_user_list_endpoint_uses_saved_admin_connection(monkeypatch):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    participant = Participant.objects.create(
        name="测试车友",
        sub2api_user_id=51,
        share_percent=Decimal("50"),
    )
    blank_name_participant = Participant.objects.create(
        name="不应作为账号身份",
        sub2api_user_id=52,
        sub2api_username="错误的旧缓存",
        share_percent=Decimal("50"),
    )

    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def list_users(self):
            return [
                {
                    "id": 51,
                    "email": "rider@example.com",
                    "username": "rider",
                    "status": "active",
                    "role": "user",
                },
                {
                    "id": 52,
                    "email": "blank-name@example.com",
                    "username": "",
                    "status": "active",
                    "role": "user",
                },
            ]

    monkeypatch.setattr("monitor.views.participants.Sub2APIClient", FakeClient)
    response = client.get("/api/participants/sub2api-users", **headers)

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == 51
    participant.refresh_from_db()
    assert participant.sub2api_username == "rider"
    assert participant.sub2api_email == "rider@example.com"
    blank_name_participant.refresh_from_db()
    assert blank_name_participant.sub2api_username == ""
    assert blank_name_participant.sub2api_email == "blank-name@example.com"
    participants = client.get("/api/participants", **headers).json()["data"]
    blank_name_data = next(
        item for item in participants if item["id"] == blank_name_participant.id
    )
    assert blank_name_data["sub2api_identity"] == "blank-name@example.com"

@pytest.mark.django_db
def test_observation_records_paginate_and_filter_server_side():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    now = timezone.now()
    cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=now - timedelta(days=2),
        resets_at=now + timedelta(days=5),
    )

    def create_observation(
        *,
        minutes_ago: int,
        source: str,
        query_mode: str | None,
        valid: bool,
    ):
        raw_window = {}
        if query_mode:
            raw_window["query_mode"] = query_mode
        return Observation.objects.create(
            cycle=cycle,
            source=source,
            observed_at=now - timedelta(minutes=minutes_ago),
            upstream_used_percent=10,
            selected_total_cost=200,
            total_standard_cost=200,
            total_actual_cost=200,
            effective_usd_per_percent=20,
            valid_sample=valid,
            raw_window=raw_window,
        )

    newest = create_observation(
        minutes_ago=5,
        source="manual",
        query_mode="direct",
        valid=True,
    )
    legacy_direct = create_observation(
        minutes_ago=10,
        source="manual",
        query_mode=None,
        valid=False,
    )
    create_observation(
        minutes_ago=15,
        source="manual",
        query_mode="passive",
        valid=True,
    )
    create_observation(
        minutes_ago=20,
        source="scheduled",
        query_mode="direct",
        valid=True,
    )

    response = client.get(
        "/api/observations",
        {
            "page": 1,
            "page_size": 1,
            "from": (now - timedelta(minutes=12)).isoformat(),
            "to": now.isoformat(),
            "source": "manual",
            "query_mode": "direct",
        },
        **headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {
        "total": 2,
        "valid_count": 1,
        "passive_count": 0,
    }
    assert data["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 2,
        "total_pages": 2,
    }
    assert [item["id"] for item in data["items"]] == [newest.id]

    second_page = client.get(
        "/api/observations",
        {
            "page": 2,
            "page_size": 1,
            "from": (now - timedelta(minutes=12)).isoformat(),
            "to": now.isoformat(),
            "source": "manual",
            "query_mode": "direct",
        },
        **headers,
    ).json()["data"]
    assert [item["id"] for item in second_page["items"]] == [legacy_direct.id]


@pytest.mark.django_db
def test_notification_records_paginate_and_apply_all_filters():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    participant = Participant.objects.create(
        name="筛选车友",
        sub2api_user_id=88,
        share_percent=100,
    )
    now = timezone.now()
    target = NotificationEvent.objects.create(
        event_type="test",
        participant=participant,
        dedupe_key="target",
        recipient="rider@example.com",
        subject="Quota notice",
        body="target",
        status="sent",
    )
    NotificationEvent.objects.create(
        event_type="collection_error",
        participant=participant,
        dedupe_key="wrong-type",
        recipient="rider@example.com",
        subject="Quota notice",
        body="wrong type",
        status="failed",
    )
    old = NotificationEvent.objects.create(
        event_type="test",
        participant=participant,
        dedupe_key="old",
        recipient="rider@example.com",
        subject="Quota notice",
        body="old",
        status="sent",
    )
    NotificationEvent.objects.filter(pk=old.pk).update(
        created_at=now - timedelta(days=2)
    )

    response = client.get(
        "/api/notifications",
        {
            "page_size": 1,
            "from": (now - timedelta(hours=1)).isoformat(),
            "to": (now + timedelta(hours=1)).isoformat(),
            "event_type": "test",
            "participant": participant.id,
            "subject": "quota",
            "status": "sent",
        },
        **headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == {
        "total": 1,
        "sent_count": 1,
        "failed_count": 0,
    }
    assert data["pagination"]["total"] == 1
    assert [item["id"] for item in data["items"]] == [target.id]
    assert {"id": participant.id, "name": participant.name} in data[
        "filter_options"
    ]["participants"]




@pytest.mark.django_db
def test_database_transfer_endpoints_require_admin_and_clear_refresh_on_import(
    monkeypatch,
):
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)
    monkeypatch.setattr(
        "monitor.views.database.export_database_bytes",
        lambda: b"SQLite format 3\x00backup",
    )
    captured = {}

    def fake_import(uploaded, size):
        captured["name"] = uploaded.name
        captured["size"] = size
        return "pinche.before-import.sqlite3"

    monkeypatch.setattr("monitor.views.database.import_database", fake_import)

    unauthorized = Client().get("/api/database/export")
    assert unauthorized.status_code == 401
    exported = client.get("/api/database/export", **headers)
    assert exported.status_code == 200
    assert exported.content.startswith(b"SQLite format 3\x00")

    imported = client.post(
        "/api/database/import",
        data={
            "database": SimpleUploadedFile(
                "backup.sqlite3",
                b"SQLite format 3\x00backup",
                content_type="application/vnd.sqlite3",
            )
        },
        **headers,
    )
    assert imported.status_code == 200
    assert captured["name"] == "backup.sqlite3"
    assert captured["size"] == len(b"SQLite format 3\x00backup")
    assert imported.cookies["pinche_refresh"]["max-age"] == 0


def test_sqlite_import_replaces_database_and_keeps_recovery_copy(
    monkeypatch,
    tmp_path,
):
    current_path = tmp_path / "pinche.sqlite3"
    source_path = tmp_path / "uploaded.sqlite3"

    def create_database(path, marker):
        with sqlite3.connect(path) as database:
            database.executescript(
                """
                CREATE TABLE django_migrations (app TEXT, name TEXT);
                CREATE TABLE auth_user (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_appsettings (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_participant (id INTEGER PRIMARY KEY);
                CREATE TABLE monitor_quotacycle (id INTEGER PRIMARY KEY);
                CREATE TABLE marker (value TEXT);
                """
            )
            database.execute("INSERT INTO marker(value) VALUES (?)", (marker,))

    create_database(current_path, "before")
    create_database(source_path, "after")
    payload = source_path.read_bytes()
    monkeypatch.setattr(
        database_transfer,
        "_database_path",
        lambda: current_path,
    )
    monkeypatch.setattr(
        database_transfer,
        "_expected_leaf_migrations",
        lambda: set(),
    )

    recovery_name = database_transfer.import_database(
        BytesIO(payload),
        len(payload),
    )

    with sqlite3.connect(current_path) as database:
        assert database.execute("SELECT value FROM marker").fetchone()[0] == "after"
    with sqlite3.connect(tmp_path / recovery_name) as recovery:
        assert recovery.execute("SELECT value FROM marker").fetchone()[0] == "before"


@pytest.mark.django_db
def test_django_serves_vue_entry_for_root_and_history_routes():
    client = Client()

    for route in ("/", "/participants", "/settings"):
        response = client.get(route)
        assert response.status_code == 200
        assert b'id="app"' in response.content
        assert b"/static/frontend/assets/index-" in response.content

    assert client.get("/api/unknown").status_code == 404


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
    config.openai_account_id = 7
    config.save()
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=22,
        share_percent=50,
    )
    now = timezone.now()
    base = (now - timedelta(days=60)).replace(
        day=10,
        hour=8,
        minute=0,
        second=0,
        microsecond=0,
    )
    cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=base - timedelta(days=7),
        resets_at=now + timedelta(days=3),
    )

    def observation(at, rate):
        return Observation.objects.create(
            cycle=cycle,
            observed_at=at,
            upstream_used_percent=10,
            selected_total_cost=100,
            total_standard_cost=100,
            total_actual_cost=100,
            effective_usd_per_percent=Decimal(rate),
            raw_window={"rate_method": "cumulative_cycle_v1"},
        )

    observation(base, "10")
    observation(base + timedelta(hours=2), "12")
    observation(base + timedelta(days=1), "14")
    observation(now, "16")

    hour = now.replace(minute=5, second=0, microsecond=0)
    ParticipantUsageSample.objects.create(
        participant=participant,
        cycle=cycle,
        observed_at=hour,
        balance_usd=Decimal("800"),
        selected_cost=10,
    )
    ParticipantUsageSample.objects.create(
        participant=participant,
        cycle=cycle,
        observed_at=hour + timedelta(minutes=30),
        balance_usd=Decimal("760"),
        selected_cost=12,
    )

    daily = client.get(
        "/api/statistics?capacity_period=day&capacity_days=365"
        "&usage_days=7&usage_precision=hour",
        **headers,
    ).json()["data"]
    assert daily["capacity_series"][-1]["weekly_total_usd"] == 1600.0
    assert daily["capacity_summary"]["cycle"]["estimate_usd"] == 1600.0
    assert daily["capacity_summary"]["today"]["sufficient"] is False
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
    assert month["weekly_total_usd"] == 1300.0
    assert month["minimum_usd"] == 1000.0
    assert month["maximum_usd"] == 1400.0
    assert month["sample_count"] == 2


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
    config.openai_account_id = 7
    config.daily_estimate_min_percent_span = Decimal("5")
    config.save()

    now = timezone.now()
    local_day_start = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    cycle = QuotaCycle.objects.create(
        account_id=7,
        starts_at=now - timedelta(days=2),
        resets_at=now + timedelta(days=5),
    )

    def observation(at, used_percent, cost):
        Observation.objects.create(
            cycle=cycle,
            observed_at=at,
            upstream_used_percent=used_percent,
            selected_total_cost=cost,
            total_standard_cost=cost,
            total_actual_cost=cost,
            sample_usd_per_percent=Decimal(cost) / Decimal(used_percent),
            effective_usd_per_percent=Decimal("20"),
            valid_sample=True,
            raw_window={"rate_method": "cumulative_cycle_v1"},
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
    assert result["capacity_summary"]["cycle"]["rate_sample_count"] == 2
    assert result["capacity_summary"]["today"] == {
        "estimate_usd": 2000.0,
        "minimum_usd": 1666.67,
        "maximum_usd": 2500.0,
        "start_cost_usd": 200.0,
        "start_percent": 10.0,
        "end_cost_usd": 300.0,
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
    assert captured["headers"]["Idempotency-Key"].startswith("pinche-")
    assert captured["json"] == {
        "from": "拼车额度 <notice@example.com>",
        "to": ["owner@example.com"],
        "subject": "测试",
        "text": "正文",
    }
