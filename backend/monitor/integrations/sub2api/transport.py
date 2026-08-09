"""Authenticated HTTP transport and common response handling."""
from typing import Any
from urllib.parse import urljoin

import httpx

from ...models import AppSettings
from ...secrets import decrypt_secret
from .dto import Sub2APIError


class Sub2APITransport:
    def __init__(
        self,
        config: AppSettings,
        *,
        base_url: str | None = None,
        admin_token: str | None = None,
        request_timeout_seconds: int | None = None,
        verify_tls: bool | None = None,
    ):
        # 设置页允许用尚未保存的地址和 Token 发起测试；空 Token 则安全地回退到已保存密文。
        token = admin_token or decrypt_secret(config.sub2api_admin_token_encrypted)
        if not token:
            raise Sub2APIError("尚未配置 Sub2API Admin Token")
        self.base_url = (base_url or config.sub2api_base_url).rstrip("/") + "/"
        self.client = httpx.Client(
            headers={"x-api-key": token, "Accept": "application/json"},
            timeout=request_timeout_seconds or config.request_timeout_seconds,
            verify=config.verify_tls if verify_tls is None else verify_tls,
            follow_redirects=False,
        )

    def __enter__(self) -> "Sub2APIClient":
        return self

    def __exit__(self, *_args) -> None:
        self.client.close()

    @staticmethod
    def _response_data(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                message = response.json().get("message", "")
            except ValueError:
                message = ""
            suffix = f"：{message}" if message else ""
            raise Sub2APIError(
                f"Sub2API 返回 HTTP {response.status_code}{suffix}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise Sub2APIError("Sub2API 返回的不是 JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            message = (
                payload.get("message", "未知错误")
                if isinstance(payload, dict)
                else "响应结构错误"
            )
            raise Sub2APIError(f"Sub2API 请求失败：{message}")
        return payload.get("data")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # urljoin 会处理用户在设置页中是否填写末尾斜杠，但 path 必须保持相对形式。
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise Sub2APIError(
                f"无法连接 Sub2API：{exc.__class__.__name__}"
            ) from exc
        return self._response_data(response)
