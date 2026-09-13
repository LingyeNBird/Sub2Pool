"""Temporary upstream disables: lifecycle, journal, permissions, and auto restore."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.integrations.sub2api import Sub2APIError
from monitor.models import (
    AccountTemporaryDisable,
    AppSettings,
    PagePermission,
    SystemUserPageAccess,
)
from monitor.secrets import encrypt_secret
from monitor.temporary_disable import restore_due_disables
from monitor.tests.helpers import create_monitored_account, jwt_login


class FakeUpstream:
    """Mutable stand-in for the upstream account state the writes must preserve."""

    def __init__(
        self,
        *,
        schedulable: bool = True,
        credentials: dict | None = None,
        models: list[str] | None = None,
    ):
        self.schedulable = schedulable
        self.credentials = credentials or {}
        self.models = models or ["gpt-5.5", "gpt-5.4"]
        self.calls: list[tuple] = []


def fake_client(upstream: FakeUpstream):
    class FakeClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def account_runtime_status(self, account_id):
            upstream.calls.append(("runtime", account_id))
            return {
                "name": "上游账号",
                "account_type": "oauth",
                "status": "active",
                "schedulable": upstream.schedulable,
                "current_concurrency": 0,
                "concurrency_limit": 10,
                "last_used_at": None,
                "rate_limited_at": None,
                "rate_limit_reset_at": None,
                "overload_until": None,
                "temp_unschedulable_until": None,
                "temp_unschedulable_reason": None,
                "error_message": None,
            }

        def account_configuration(self, account_id):
            upstream.calls.append(("configuration", account_id))
            return {
                "name": "上游账号",
                "schedulable": upstream.schedulable,
                "credentials": deepcopy(upstream.credentials),
            }

        def account_models(self, account_id):
            upstream.calls.append(("models", account_id))
            return list(upstream.models)

        def set_account_schedulable(self, account_id, schedulable):
            upstream.calls.append(("schedulable", account_id, schedulable))
            upstream.schedulable = schedulable

        def set_account_model_whitelist(self, account_id, mapping):
            upstream.calls.append(("whitelist", account_id, deepcopy(mapping)))
            retained = {
                key: value
                for key, value in upstream.credentials.items()
                if key != "model_mapping"
            }
            if mapping:
                retained["model_mapping"] = deepcopy(mapping)
            upstream.credentials = retained

        def account_usage_status(self, _account_id, *, source="passive"):
            upstream.calls.append(("usage", source))
            return {
                "source": source,
                "updated_at": timezone.now().isoformat(),
                "five_hour": None,
                "seven_day": {
                    "used_percent": 12.5,
                    "reset_at": (timezone.now() + timedelta(days=3)).isoformat(),
                    "remaining_seconds": 259200,
                    "request_count": 10,
                    "token_count": 1000,
                    "account_cost_usd": 1.5,
                    "standard_cost_usd": 1.2,
                    "user_cost_usd": 1.8,
                },
                "needs_verify": None,
                "is_banned": None,
                "needs_reauth": None,
                "error_code": None,
                "error": None,
            }

        def account_usage_stats(self, _account_id, *, days=30):
            return {
                "days": days,
                "actual_days_used": 1,
                "account_cost_usd": 1.5,
                "standard_cost_usd": None,
                "user_cost_usd": None,
                "request_count": 10,
                "token_count": 1000,
                "avg_daily_cost_usd": 1.5,
                "avg_daily_request_count": 10.0,
                "avg_daily_token_count": 1000.0,
                "avg_duration_ms": None,
                "today": None,
            }

    return FakeClient


def configure(monkeypatch, upstream: FakeUpstream):
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    monkeypatch.setattr(
        "monitor.temporary_disable.Sub2APIClient",
        fake_client(upstream),
    )
    return config


def admin_headers() -> tuple[Client, dict[str, str]]:
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _response = jwt_login(client)
    return client, headers


@pytest.mark.django_db
def test_disabling_one_model_writes_minus_that_model_and_restores_original(monkeypatch):
    upstream = FakeUpstream(
        credentials={"base_url": "https://up.example/v1", "api_key_state": "kept"},
        models=["gpt-5.5", "gpt-5.4", "gpt-5.3"],
    )
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()

    created = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "model", "model": "gpt-5.5", "minutes": 240},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    payload = created.json()["data"]
    assert payload["scope"] == "model"
    assert payload["model"] == "gpt-5.5"
    assert payload["created_by"] == "owner"
    assert payload["last_error"] == ""
    assert payload["restore_source"] == ""
    row = AccountTemporaryDisable.objects.get(pk=payload["id"])
    assert timedelta(hours=3, minutes=59) < row.restore_at - row.started_at
    assert row.restore_at - row.started_at < timedelta(hours=4, minutes=1)

    # 白名单去掉被禁模型，其余上游配置原样保留。
    assert upstream.credentials["model_mapping"] == {
        "gpt-5.4": "gpt-5.4",
        "gpt-5.3": "gpt-5.3",
    }
    assert upstream.credentials["base_url"] == "https://up.example/v1"
    assert upstream.credentials["api_key_state"] == "kept"

    listed = client.get("/api/account-status", **headers)
    assert listed.status_code == 200
    row_payload = listed.json()["data"]["accounts"][0]["temporary_disables"]
    assert [item["model"] for item in row_payload] == ["gpt-5.5"]

    restored = client.delete(
        f"/api/accounts/temporary-disables/{row.id}",
        **headers,
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["restore_source"] == "manual"
    # 原本没有白名单，恢复后必须回到“不限模型”，而不是留下空白名单。
    assert "model_mapping" not in upstream.credentials
    assert upstream.credentials["base_url"] == "https://up.example/v1"
    assert AccountTemporaryDisable.objects.get(pk=row.id).restored_at is not None


@pytest.mark.django_db
def test_disabling_one_model_keeps_existing_mapping_targets(monkeypatch):
    upstream = FakeUpstream(
        credentials={
            "model_mapping": {"public-a": "gpt-5.5", "public-b": "gpt-5.4"},
        },
    )
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()

    response = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "model", "model": "public-a", "minutes": 60},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201
    # 已有白名单时只删键，其他别名映射不得被改写成同名映射。
    assert upstream.credentials["model_mapping"] == {"public-b": "gpt-5.4"}

    client.delete(
        f"/api/accounts/temporary-disables/{response.json()['data']['id']}",
        **headers,
    )
    assert upstream.credentials["model_mapping"] == {
        "public-a": "gpt-5.5",
        "public-b": "gpt-5.4",
    }


@pytest.mark.django_db
def test_disabling_account_pauses_scheduling_and_restores_previous_state(monkeypatch):
    upstream = FakeUpstream(schedulable=True)
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()

    created = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 60},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201
    assert upstream.schedulable is False

    extended = client.patch(
        f"/api/accounts/temporary-disables/{created.json()['data']['id']}",
        data={"minutes": 600},
        content_type="application/json",
        **headers,
    )
    assert extended.status_code == 200
    assert extended.json()["data"]["restore_at"] == AccountTemporaryDisable.objects.get(
        pk=created.json()["data"]["id"]
    ).restore_at.isoformat()
    row = AccountTemporaryDisable.objects.get(pk=created.json()["data"]["id"])
    assert timedelta(hours=9, minutes=59) < row.restore_at - timezone.now()
    assert row.restore_at - timezone.now() < timedelta(hours=10, minutes=1)

    # 到点后由后台轮询恢复，无需管理员再操作。
    row.restore_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["restore_at"])
    assert restore_due_disables() == {"restored": 1, "failed": 0}
    assert upstream.schedulable is True
    row.refresh_from_db()
    assert row.restore_source == "auto"
    assert row.last_error == ""

    # 原本就不可调度时，恢复必须写回原状态而不是强行打开。
    upstream.schedulable = False
    again = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 1},
        content_type="application/json",
        **headers,
    )
    assert again.status_code == 201
    client.delete(
        f"/api/accounts/temporary-disables/{again.json()['data']['id']}",
        **headers,
    )
    assert upstream.schedulable is False


@pytest.mark.django_db
def test_restore_retries_are_backed_off_and_reported(monkeypatch):
    upstream = FakeUpstream(schedulable=True)
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()
    created = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 30},
        content_type="application/json",
        **headers,
    )
    row = AccountTemporaryDisable.objects.get(pk=created.json()["data"]["id"])
    row.restore_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["restore_at"])

    class BrokenClient:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_account_schedulable(self, *_args):
            raise Sub2APIError("上游超时")

    monkeypatch.setattr("monitor.temporary_disable.Sub2APIClient", BrokenClient)
    assert restore_due_disables() == {"restored": 0, "failed": 1}
    row.refresh_from_db()
    assert row.restored_at is None
    assert row.retry_at is not None
    assert "自动恢复失败" in row.last_error
    # 失败后进入退避窗口，避免每几秒重复打上游。
    assert restore_due_disables() == {"restored": 0, "failed": 0}

    monkeypatch.setattr(
        "monitor.temporary_disable.Sub2APIClient", fake_client(upstream)
    )
    row.refresh_from_db()
    row.retry_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["retry_at"])
    assert restore_due_disables() == {"restored": 1, "failed": 0}
    row.refresh_from_db()
    assert row.restored_at is not None
    assert row.last_error == ""


@pytest.mark.django_db
def test_conflicting_or_invalid_disable_requests_are_rejected(monkeypatch):
    upstream = FakeUpstream()
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()

    def create(**payload):
        return client.post(
            f"/api/accounts/{account.id}/temporary-disables",
            data=payload,
            content_type="application/json",
            **headers,
        )

    assert create(scope="model", model="missing-model", minutes=60).status_code == 400
    assert create(scope="model", model="", minutes=60).status_code == 400
    assert create(scope="account", minutes=0).status_code == 400
    assert create(scope="account", minutes="60").status_code == 400
    assert create(scope="account", minutes=60 * 24 * 31).status_code == 400
    assert create(scope="unknown", minutes=60).status_code == 400
    assert AccountTemporaryDisable.objects.count() == 0

    assert create(scope="account", minutes=60).status_code == 201
    duplicate_scope = create(scope="account", minutes=60)
    assert duplicate_scope.status_code == 400
    assert "同类临时禁用" in duplicate_scope.json()["message"]

    assert create(scope="model", model="gpt-5.5", minutes=60).status_code == 201
    # 同账号已有一条模型级禁用时就拒绝第二条，避免恢复目标互相覆盖。
    second_model = create(scope="model", model="gpt-5.4", minutes=60)
    assert second_model.status_code == 400
    assert AccountTemporaryDisable.objects.count() == 2

    page = client.get("/api/account-status", **headers).json()["data"]["accounts"][0]
    assert sorted(item["scope"] for item in page["temporary_disables"]) == [
        "account",
        "model",
    ]


@pytest.mark.django_db
def test_disables_are_visible_but_not_manageable_for_page_users(monkeypatch):
    upstream = FakeUpstream()
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    admin, admin_header = admin_headers()
    created = admin.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 90},
        content_type="application/json",
        **admin_header,
    )
    disable_id = created.json()["data"]["id"]

    user_model = get_user_model()
    viewer = user_model.objects.create_user(
        username="viewer",
        password="viewer-password",
    )
    SystemUserPageAccess.objects.create(
        user=viewer,
        page_code=PagePermission.ACCOUNT_STATUS,
    )
    account.authorized_users.add(viewer)
    viewer_client = Client()
    viewer_header, _response = jwt_login(
        viewer_client,
        username="viewer",
        password="viewer-password",
    )

    listed = viewer_client.get("/api/account-status", **viewer_header)
    assert listed.status_code == 200
    rows = listed.json()["data"]["accounts"][0]["temporary_disables"]
    assert [item["id"] for item in rows] == [disable_id]
    # 系统用户能看到禁用情况，但看不到操作者。
    assert rows[0]["created_by"] == ""

    assert (
        viewer_client.post(
            f"/api/accounts/{account.id}/temporary-disables",
            data={"scope": "account", "minutes": 60},
            content_type="application/json",
            **viewer_header,
        ).status_code
        == 403
    )
    assert (
        viewer_client.patch(
            f"/api/accounts/temporary-disables/{disable_id}",
            data={"minutes": 60},
            content_type="application/json",
            **viewer_header,
        ).status_code
        == 403
    )
    assert (
        viewer_client.delete(
            f"/api/accounts/temporary-disables/{disable_id}",
            **viewer_header,
        ).status_code
        == 403
    )
    assert (
        viewer_client.get(
            f"/api/accounts/{account.id}/models",
            **viewer_header,
        ).status_code
        == 403
    )
    assert AccountTemporaryDisable.objects.get(pk=disable_id).restored_at is None
    assert upstream.schedulable is False

    api_key = viewer_client.post(
        "/api/settings/my-api-key",
        **viewer_header,
    ).json()["data"]["api_key"]
    read_only = viewer_client.get(
        "/api/v1/account-status",
        HTTP_AUTHORIZATION=f"Bearer {api_key}",
    )
    assert read_only.status_code == 200
    assert read_only.json()["data"]["accounts"][0]["temporary_disables"][0][
        "created_by"
    ] == ""


@pytest.mark.django_db
def test_model_list_comes_from_upstream_for_unrestricted_account(monkeypatch):
    upstream = FakeUpstream(models=["gpt-5.5", "gpt-5.4"])
    configure(monkeypatch, upstream)
    account = create_monitored_account(7)
    client, headers = admin_headers()
    assert client.get("/api/accounts/999/models", **headers).status_code == 400
    response = client.get(f"/api/accounts/{account.id}/models", **headers)
    assert response.status_code == 200
    assert response.json()["data"]["models"] == ["gpt-5.5", "gpt-5.4"]

    upstream.credentials = {"model_mapping": {"alias": "gpt-5.5"}}
    restricted = client.get(f"/api/accounts/{account.id}/models", **headers)
    assert restricted.json()["data"]["models"] == ["alias"]


@pytest.mark.django_db
def test_disable_requires_configured_sub2api_connection(monkeypatch):
    upstream = FakeUpstream()
    monkeypatch.setattr(
        "monitor.temporary_disable.Sub2APIClient",
        fake_client(upstream),
    )
    account = create_monitored_account(7)
    client, headers = admin_headers()
    response = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 60},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 400
    assert "Admin Token" in response.json()["message"]
    assert AccountTemporaryDisable.objects.count() == 0


@pytest.mark.django_db
def test_failed_upstream_write_keeps_an_unconfirmed_journal_row(monkeypatch):
    upstream = FakeUpstream()

    class FailingClient(fake_client(upstream)):
        def set_account_schedulable(self, *_args):
            raise Sub2APIError("上游超时")

    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    monkeypatch.setattr("monitor.temporary_disable.Sub2APIClient", FailingClient)
    account = create_monitored_account(7)
    client, headers = admin_headers()
    response = client.post(
        f"/api/accounts/{account.id}/temporary-disables",
        data={"scope": "account", "minutes": 60},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 502
    row = AccountTemporaryDisable.objects.get()
    assert row.restored_at is None
    assert row.last_error
    assert upstream.schedulable is True
