"""把 DRF 自带异常统一成前端现有的 JSON 响应格式。"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .history_state import LeaseBusyError, LeaseLostError


def _message(data: Any, fallback: str) -> str:
    """从 DRF 的 detail 或字段错误中提取一条适合直接展示的消息。"""
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return str(detail)
        for value in data.values():
            if isinstance(value, list) and value:
                return str(value[0])
            if value:
                return str(value)
    if isinstance(data, list) and data:
        return str(data[0])
    if data:
        return str(data)
    return fallback


def api_exception_handler(exc, context) -> Response | None:
    """Map fenced-write conflicts while preserving the shared JSON contract."""
    if isinstance(exc, (LeaseBusyError, LeaseLostError)):
        response = Response(
            {"detail": str(exc)},
            status=status.HTTP_409_CONFLICT,
        )
    else:
        response = exception_handler(exc, context)
    if response is None:
        return None

    details = response.data
    response.data = {
        "ok": False,
        "message": _message(details, "请求处理失败"),
        "details": details,
    }
    return response
