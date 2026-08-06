"""IP 封禁判定。

服务器来源 IP 与直连地址能在请求进入 Django 时可靠读取，因此由中间件在任何
路由处理前返回空响应。WebRTC IP 只能由浏览器运行 JavaScript 后上报，无法作为
首个 HTTP 请求的服务端身份依据，只在登录预检和登录提交阶段校验。
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse

from .login_audit import _webrtc_addresses, request_addresses
from .models import BlockedIPAddress


def empty_block_response() -> HttpResponse:
    """不给被封禁来源返回页面、JSON 或可用于探测系统的信息。"""

    return HttpResponse(status=204)


def blocked_webrtc_addresses(payload: dict[str, Any]) -> list[str]:
    """返回本次浏览器上报中已被封禁的 WebRTC 地址。"""

    _, addresses = _webrtc_addresses(payload)
    if not addresses:
        return []
    return list(
        BlockedIPAddress.objects.filter(
            source_type="webrtc",
            address__in=addresses,
        ).values_list("address", flat=True)
    )


class IPBlockMiddleware:
    """在路由解析前拦截服务端可见的两类来源地址。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        request_ip, remote_ip = request_addresses(request)
        if request_ip and BlockedIPAddress.objects.filter(
            source_type="request",
            address=request_ip,
        ).exists():
            return empty_block_response()
        if remote_ip and BlockedIPAddress.objects.filter(
            source_type="remote",
            address=remote_ip,
        ).exists():
            return empty_block_response()
        return self.get_response(request)
