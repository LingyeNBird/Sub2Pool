"""Upstream account writes: schedulable switch and model-whitelist replacement."""

from __future__ import annotations

import json

import httpx
import pytest

from monitor.integrations.sub2api import Sub2APIClient, Sub2APIError
from monitor.models import AppSettings
from monitor.secrets import encrypt_secret


def configured_client() -> tuple[AppSettings, Sub2APIClient]:
    config = AppSettings.load()
    config.sub2api_base_url = "https://sub2api.example/"
    config.sub2api_admin_token_encrypted = encrypt_secret("admin-secret")
    config.save()
    return config, Sub2APIClient(config)


@pytest.mark.django_db
def test_model_whitelist_write_replaces_only_the_whitelist_key():
    config, client = configured_client()
    written: list[dict] = []
    account = {
        "id": 42,
        "name": "GPT Pro 主账号",
        "platform": "openai",
        "type": "oauth",
        "credentials": {
            "base_url": "https://up.example/v1",
            "chatgpt_account_id": "acct-1",
            "has_access_token": True,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"code": 0, "message": "success", "data": account}
            )
        payload = json.loads(request.content)
        written.append(payload)
        credentials = dict(account["credentials"])
        credentials.pop("model_mapping", None)
        credentials.update(payload["credentials"])
        account["credentials"] = credentials
        return httpx.Response(
            200, json={"code": 0, "message": "success", "data": account}
        )

    with client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        client.set_account_model_whitelist(
            42,
            {"gpt-5.4": "gpt-5.4", "public-a": "gpt-5.5"},
        )

    assert written == [
        {
            "name": "GPT Pro 主账号",
            "credentials": {
                "base_url": "https://up.example/v1",
                "chatgpt_account_id": "acct-1",
                "has_access_token": True,
                "model_mapping": {"gpt-5.4": "gpt-5.4", "public-a": "gpt-5.5"},
            },
        }
    ]
    # GET 返回的脱敏凭据原样回传（敏感子键由 Sub2API 自己保留），
    # 但 base_url 等非敏感配置绝不能因为写白名单而丢失。
    assert account["credentials"]["base_url"] == "https://up.example/v1"


@pytest.mark.django_db
def test_model_whitelist_none_removes_the_whitelist():
    config, client = configured_client()
    written: list[dict] = []
    account = {
        "id": 42,
        "name": "GPT Pro 主账号",
        "platform": "openai",
        "credentials": {
            "base_url": "https://up.example/v1",
            "model_mapping": {"gpt-5.4": "gpt-5.4"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"code": 0, "message": "success", "data": account}
            )
        payload = json.loads(request.content)
        written.append(payload)
        account["credentials"] = dict(payload["credentials"])
        return httpx.Response(
            200, json={"code": 0, "message": "success", "data": account}
        )

    with client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        client.set_account_model_whitelist(42, None)

    assert written[0]["credentials"] == {"base_url": "https://up.example/v1"}
    assert "model_mapping" not in account["credentials"]


@pytest.mark.django_db
def test_model_whitelist_write_rejects_a_different_readback():
    config, client = configured_client()
    account = {
        "id": 42,
        "name": "GPT Pro 主账号",
        "platform": "openai",
        "credentials": {"base_url": "https://up.example/v1"},
    }
    stale = {
        "id": 42,
        "name": "GPT Pro 主账号",
        "platform": "openai",
        "credentials": {
            "base_url": "https://up.example/v1",
            "model_mapping": {"gpt-5.5": "gpt-5.5"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        data = account if request.method == "GET" else stale
        return httpx.Response(
            200, json={"code": 0, "message": "success", "data": data}
        )

    with client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(Sub2APIError, match="白名单读回不一致"):
            client.set_account_model_whitelist(42, {"gpt-5.4": "gpt-5.4"})


@pytest.mark.django_db
def test_schedulable_write_posts_the_setting_and_verifies_readback():
    config, client = configured_client()
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/admin/accounts/42/schedulable"
        posted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {"id": 42, "schedulable": False},
            },
        )

    with client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        client.set_account_schedulable(42, False)
        with pytest.raises(Sub2APIError, match="调度状态读回不一致"):
            client.set_account_schedulable(42, True)

    assert posted == [{"schedulable": False}, {"schedulable": True}]


@pytest.mark.django_db
def test_account_models_normalizes_the_upstream_catalog():
    config, client = configured_client()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": [
                    {"id": "gpt-5.5", "object": "model"},
                    {"id": "gpt-5.4", "object": "model"},
                    {"id": "gpt-5.5", "object": "model"},
                    "gpt-4.1",
                ],
            },
        )

    with client:
        client.client.close()
        client.client = httpx.Client(transport=httpx.MockTransport(handler))
        models = client.account_models(42)

    assert models == ["gpt-4.1", "gpt-5.4", "gpt-5.5"]
    assert requested == ["/api/v1/admin/accounts/42/models"]
