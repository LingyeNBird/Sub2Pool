"""登录来源审计：服务端地址为主，WebRTC 地址仅作为浏览器自报线索。"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

from django.conf import settings
from django.http import HttpRequest

from .models import LoginEvent


def _normalized_ip(value: Any) -> str | None:
    """只接受 Python 能解析的 IPv4/IPv6，拒绝 mDNS 主机名和任意文本。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return str(ip_address(text))
    except ValueError:
        return None


def request_addresses(request: HttpRequest) -> tuple[str | None, str | None]:
    """返回（可信客户端地址、直连地址）。

    默认不信任可伪造的 X-Forwarded-For。仅当部署者通过 TRUSTED_PROXY_COUNT
    声明反向代理层数后，才从右侧按可信层数取客户端地址。
    """
    remote_ip = _normalized_ip(request.META.get("REMOTE_ADDR"))
    request_ip = remote_ip
    trusted_count = settings.TRUSTED_PROXY_COUNT
    if trusted_count > 0:
        forwarded = [
            item
            for item in (
                _normalized_ip(part)
                for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            )
            if item
        ]
        if len(forwarded) >= trusted_count:
            request_ip = forwarded[-trusted_count]
        elif trusted_count == 1:
            request_ip = (
                _normalized_ip(request.META.get("HTTP_X_REAL_IP")) or request_ip
            )
    return request_ip, remote_ip


def _webrtc_addresses(payload: dict[str, Any]) -> tuple[bool | None, list[str]]:
    client = payload.get("client_network")
    if not isinstance(client, dict):
        return None, []
    supported_value = client.get("webrtc_supported")
    supported = supported_value if isinstance(supported_value, bool) else None
    raw_addresses = client.get("webrtc_ips")
    if not isinstance(raw_addresses, list):
        return supported, []
    addresses: list[str] = []
    for raw in raw_addresses[:16]:
        address = _normalized_ip(raw)
        if address and address not in addresses:
            addresses.append(address)
    return supported, addresses[:8]


def record_login_attempt(
    request: HttpRequest,
    payload: dict[str, Any],
    *,
    username: str,
    success: bool,
    failure_reason: str = "",
) -> LoginEvent:
    request_ip, remote_ip = request_addresses(request)
    webrtc_supported, webrtc_ips = _webrtc_addresses(payload)
    return LoginEvent.objects.create(
        username=username[:150],
        success=success,
        request_ip=request_ip,
        remote_ip=remote_ip,
        webrtc_supported=webrtc_supported,
        webrtc_ips=webrtc_ips,
        user_agent=str(request.META.get("HTTP_USER_AGENT", ""))[:1000],
        failure_reason=failure_reason[:120],
    )
