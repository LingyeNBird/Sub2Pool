"""所有 DRF View 共用的响应和权限基类。"""

from __future__ import annotations

from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def ok(data=None, status_code: int = 200) -> Response:
    return Response({"ok": True, "data": data}, status=status_code)


def error(
    message: str,
    status_code: int = 400,
    details=None,
) -> Response:
    payload = {"ok": False, "message": message}
    if details:
        payload["details"] = details
    return Response(payload, status=status_code)


class AdminAPIView(APIView):
    """显式要求已通过 JWT 认证的管理员。"""

    permission_classes = [IsAdminUser]

class AuthenticatedAPIView(APIView):
    """允许已通过 JWT 认证的管理员和普通用户。"""

    permission_classes = [IsAuthenticated]


class PublicAPIView(APIView):
    """公开接口不尝试解析 Bearer Token，避免无效旧 Token 阻塞登录或刷新。"""

    authentication_classes = []
    permission_classes = [AllowAny]
