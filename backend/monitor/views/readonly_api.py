"""Machine-readable discovery and OpenAPI documents for the external API."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..api_auth import APIKeyAuthentication
from ..openapi.document import openapi_document
from .base import ok


API_ENDPOINTS = [
    {
        "method": "GET",
        "path": "/api/v1/accounts",
        "description": "读取监控账号及其本地采集状态。",
    },
    {
        "method": "GET",
        "path": "/api/v1/dashboard",
        "description": "读取额度总览、当前周期和待处理建议。",
    },
    {
        "method": "GET",
        "path": "/api/v1/recommendations",
        "description": "读取首页待应用建议及其账号来源、合同和测算明细。",
    },
    {
        "method": "POST",
        "path": "/api/v1/recommendations/{participant_id}/apply",
        "description": "使用管理员生成的 API Key 幂等应用一名参与者的当前聚合余额建议。",
    },
    {
        "method": "GET",
        "path": "/api/v1/account-status",
        "description": "从 Sub2API 只读查询全部监控账号的运行和用量状态。",
    },
    {
        "method": "GET",
        "path": "/api/v1/participants",
        "description": "读取参与者页面的表格数据。",
    },
    {
        "method": "GET",
        "path": "/api/v1/observations",
        "description": "按监控账号筛选并分页读取观测记录。",
    },
    {
        "method": "GET",
        "path": "/api/v1/observations/{observation_id}/fast-correction",
        "description": "读取一个观测区间已持久化的 FAST 修正明细。",
    },
    {
        "method": "GET",
        "path": "/api/v1/particle-trajectory",
        "description": "按监控账号和历史周期只读重放粒子轨迹。",
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics",
        "description": "按监控账号读取额度统计、容量历史和参与者用量序列。",
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics/participants/{participant_id}/api-usage",
        "description": "按监控账号读取一个参与者在当前周期内的 API Key 用量构成。",
    },
    {
        "method": "GET",
        "path": "/api/v1/notifications",
        "description": "筛选并分页读取通知发送记录。",
    },
]


class ReadOnlyAPIView(APIView):
    """Common policy for API-key-authenticated, read-only endpoints."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]


class ReadOnlyAPIRootView(ReadOnlyAPIView):
    """Return a compact endpoint index for clients without web access."""

    def get(self, _request):
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
                    "permissions": "all",
                },
                "endpoints": API_ENDPOINTS,
            }
        )


class ReadOnlyOpenAPIView(ReadOnlyAPIView):
    """Return the raw OpenAPI document for import into API tooling."""

    def get(self, _request):
        return Response(openapi_document())


