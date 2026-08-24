"""Machine-readable discovery and OpenAPI documents for the external API."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..access import has_page_permission
from ..api_auth import APIKeyAuthentication
from ..openapi.document import openapi_document
from ..models import PagePermission
from .base import ok


API_ENDPOINTS = [
    {
        "method": "GET",
        "path": "/api/v1/accounts",
        "description": "读取当前 Key 可见的监控账号及其本地采集状态。",
        "page_permission": PagePermission.ACCOUNT_STATUS,
    },
    {
        "method": "GET",
        "path": "/api/v1/dashboard",
        "description": "读取当前 Key 可见的额度总览、周期和待处理建议。",
        "page_permission": PagePermission.DASHBOARD,
    },
    {
        "method": "GET",
        "path": "/api/v1/recommendations",
        "description": "读取当前 Key 可见的待应用建议及其完整来源明细。",
        "page_permission": PagePermission.DASHBOARD,
    },
    {
        "method": "POST",
        "path": "/api/v1/recommendations/{participant_id}/apply",
        "description": "使用管理员生成的 API Key 幂等应用一名参与者的当前聚合余额建议。",
        "admin_only": True,
    },
    {
        "method": "GET",
        "path": "/api/v1/account-status",
        "description": "只读查询当前 Key 可见账号的运行和用量状态。",
        "page_permission": PagePermission.ACCOUNT_STATUS,
    },
    {
        "method": "GET",
        "path": "/api/v1/participants",
        "description": "读取当前 Key 可见的参与者页面数据。",
        "page_permission": PagePermission.PARTICIPANTS,
    },
    {
        "method": "GET",
        "path": "/api/v1/observations",
        "description": "按当前 Key 可见的监控账号筛选并分页读取观测记录。",
        "page_permission": PagePermission.OBSERVATIONS,
    },
    {
        "method": "GET",
        "path": "/api/v1/observations/{observation_id}/fast-correction",
        "description": "读取授权账号中一个观测区间的 FAST 修正明细。",
        "page_permission": PagePermission.OBSERVATIONS,
    },
    {
        "method": "GET",
        "path": "/api/v1/particle-trajectory",
        "description": "按授权监控账号和历史周期只读重放粒子轨迹。",
        "page_permission": PagePermission.PARTICLE_FILTER,
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics",
        "description": "按授权监控账号读取额度统计、容量历史和参与者用量。",
        "page_permission": PagePermission.STATISTICS,
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics/participants/{participant_id}/api-usage",
        "description": "按授权账号读取可见参与者的当前周期 API Key 用量。",
        "page_permission": PagePermission.STATISTICS,
    },
    {
        "method": "GET",
        "path": "/api/v1/notifications",
        "description": "筛选并分页读取当前 Key 可见的通知发送记录。",
        "page_permission": PagePermission.NOTIFICATIONS,
    },
]


def api_endpoints_for(user) -> list[dict]:
    endpoints = []
    for endpoint in API_ENDPOINTS:
        if endpoint.get("admin_only") and not user.is_staff:
            continue
        page_permission = endpoint.get("page_permission")
        if page_permission and not has_page_permission(user, page_permission):
            continue
        endpoints.append(
            {
                key: value
                for key, value in endpoint.items()
                if key not in {"admin_only", "page_permission"}
            }
        )
    return endpoints


class ReadOnlyAPIView(APIView):
    """Common policy for API-key-authenticated, read-only endpoints."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]


class ReadOnlyAPIRootView(ReadOnlyAPIView):
    """Return a compact endpoint index for clients without web access."""

    def get(self, request):
        endpoints = api_endpoints_for(request.user)
        return ok(
            {
                "name": "Sub2Pool API",
                "version": "v1",
                "openapi": "/api/v1/openapi.json",
                "authentication": {
                    "type": "http",
                    "scheme": "bearer",
                    "header": "Authorization",
                    "key_prefix": "sub2pool_",
                    "permissions": (
                        "all" if request.user.is_staff else "page_scoped"
                    ),
                },
                "endpoints": endpoints,
            }
        )


class ReadOnlyOpenAPIView(ReadOnlyAPIView):
    """Return the raw OpenAPI document for import into API tooling."""

    def get(self, request):
        endpoint_paths = {
            endpoint["path"] for endpoint in api_endpoints_for(request.user)
        }
        return Response(
            openapi_document(
                endpoint_paths=endpoint_paths,
                full_access=request.user.is_staff,
            )
        )


