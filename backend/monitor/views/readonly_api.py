"""Machine-readable discovery and OpenAPI documents for the external API."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..api_auth import ReadOnlyAPIKeyAuthentication
from .base import ok


READ_ONLY_API_ENDPOINTS = [
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

    authentication_classes = [ReadOnlyAPIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]


class ReadOnlyAPIRootView(ReadOnlyAPIView):
    """Return a compact endpoint index for clients without web access."""

    def get(self, _request):
        return ok(
            {
                "name": "Sub2Pool Read-only API",
                "version": "v1",
                "openapi": "/api/v1/openapi.json",
                "authentication": {
                    "type": "http",
                    "scheme": "bearer",
                    "header": "Authorization",
                },
                "endpoints": READ_ONLY_API_ENDPOINTS,
            }
        )


class ReadOnlyOpenAPIView(ReadOnlyAPIView):
    """Return the raw OpenAPI document for import into API tooling."""

    def get(self, _request):
        return Response(_openapi_document())


def _nullable(type_name: str, **fields) -> dict:
    return {"type": [type_name, "null"], **fields}


def _success_response(description: str, schema: dict) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {
                    "allOf": [
                        {"$ref": "#/components/schemas/SuccessResponse"},
                        {
                            "type": "object",
                            "required": ["data"],
                            "properties": {"data": schema},
                        },
                    ]
                }
            }
        },
    }


def _account_id_parameter(*, required: bool = False) -> dict:
    return {
        "name": "account_id",
        "in": "query",
        "required": required,
        "description": "Sub2Pool 监控账号 ID，不是上游账号 ID。",
        "schema": {"type": "integer", "minimum": 1},
    }


def _pagination_parameters() -> list[dict]:
    return [
        {
            "name": "page",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "default": 1},
        },
        {
            "name": "page_size",
            "in": "query",
            "schema": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
        },
    ]


def _openapi_document() -> dict:
    nullable_number = _nullable("number")
    nullable_string = _nullable("string")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sub2Pool Read-only API",
            "version": "1.2.0",
            "description": (
                "使用永久只读 API Key 获取账号、额度、参与者、观测、模型和通知数据。"
                "所有端点只允许 GET、HEAD 和 OPTIONS。"
            ),
        },
        "servers": [{"url": "/api"}],
        "security": [{"ReadOnlyApiKey": []}],
        "paths": {
            "/v1": {
                "get": {
                    "summary": "读取 API 索引",
                    "operationId": "getReadOnlyApiIndex",
                    "responses": {
                        "200": {
                            "description": "API 名称、认证方式、OpenAPI 地址和数据端点索引。",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiIndexResponse"
                                    }
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/openapi.json": {
                "get": {
                    "summary": "读取 OpenAPI 3.1 文档",
                    "operationId": "getReadOnlyOpenApi",
                    "responses": {
                        "200": {
                            "description": "可导入 Postman、Apifox、Insomnia 等工具的原始 OpenAPI 文档。",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/accounts": {
                "get": {
                    "summary": "读取监控账号",
                    "operationId": "listMonitoredAccounts",
                    "responses": {
                        "200": _success_response(
                            "监控账号及其本地采集状态。",
                            {
                                "type": "array",
                                "items": {
                                    "$ref": "#/components/schemas/MonitoredAccount"
                                },
                            },
                        ),
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/dashboard": {
                "get": {
                    "summary": "读取额度总览",
                    "operationId": "getDashboard",
                    "parameters": [_account_id_parameter()],
                    "responses": {
                        "200": _success_response(
                            "额度总览、当前周期和待处理建议。",
                            {"$ref": "#/components/schemas/Dashboard"},
                        ),
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/account-status": {
                "get": {
                    "summary": "读取上游账号状态",
                    "description": (
                        "对每个监控账号执行 Sub2API 只读查询；"
                        "单账号失败写入 warnings，不中断其他账号。"
                    ),
                    "operationId": "getAccountStatus",
                    "responses": {
                        "200": _success_response(
                            "全部监控账号的运行状态、额度窗口和 30 天统计。",
                            {"$ref": "#/components/schemas/AccountStatus"},
                        ),
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/participants": {
                "get": {
                    "summary": "读取参与者",
                    "operationId": "listParticipants",
                    "responses": {
                        "200": {
                            "description": "参与者页面表格所需的全部数据。",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {
                                                "$ref": "#/components/schemas/SuccessResponse"
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "data": {
                                                        "type": "array",
                                                        "items": {
                                                            "$ref": "#/components/schemas/Participant"
                                                        },
                                                    }
                                                },
                                            },
                                        ]
                                    }
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/observations": {
                "get": {
                    "summary": "读取观测记录",
                    "operationId": "listObservations",
                    "parameters": [
                        _account_id_parameter(),
                        *_pagination_parameters(),
                        {
                            "name": "from",
                            "in": "query",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "to",
                            "in": "query",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "source",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": [
                                    "scheduled",
                                    "manual",
                                    "exhausted",
                                    "reset",
                                ],
                            },
                        },
                        {
                            "name": "query_mode",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": ["passive", "direct"],
                            },
                        },
                    ],
                    "responses": {
                        "200": _success_response(
                            "筛选后的观测记录、分页和汇总。",
                            {"$ref": "#/components/schemas/ObservationList"},
                        ),
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/observations/{observation_id}/fast-correction": {
                "get": {
                    "summary": "读取 FAST 修正明细",
                    "operationId": "getObservationFastCorrection",
                    "parameters": [
                        {
                            "name": "observation_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1},
                        }
                    ],
                    "responses": {
                        "200": _success_response(
                            "一个观测区间已持久化的 FAST 修正事实。",
                            {"$ref": "#/components/schemas/FastCorrectionDetail"},
                        ),
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                        "404": {"$ref": "#/components/responses/NotFound"},
                    },
                }
            },
            "/v1/particle-trajectory": {
                "get": {
                    "summary": "读取粒子轨迹",
                    "operationId": "getParticleTrajectory",
                    "parameters": [
                        _account_id_parameter(),
                        {
                            "name": "period",
                            "in": "query",
                            "description": "观测周期 ID；省略时读取当前周期。",
                            "schema": {"type": "integer", "minimum": 1},
                        },
                    ],
                    "responses": {
                        "200": _success_response(
                            "确定性只读重放生成的粒子轨迹。",
                            {"$ref": "#/components/schemas/ParticleTrajectory"},
                        ),
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/statistics": {
                "get": {
                    "summary": "读取额度统计",
                    "operationId": "getStatistics",
                    "parameters": [
                        {
                            "name": "account_id",
                            "in": "query",
                            "required": True,
                            "description": "Sub2Pool 监控账号 ID，不是上游账号 ID。",
                            "schema": {"type": "integer", "minimum": 1},
                        },
                        {
                            "name": "capacity_period",
                            "in": "query",
                            "description": "容量历史按天或按月聚合。",
                            "schema": {
                                "type": "string",
                                "enum": ["day", "month"],
                                "default": "day",
                            },
                        },
                        {
                            "name": "capacity_days",
                            "in": "query",
                            "description": "容量历史回看天数，最大 730。",
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 730,
                            },
                        },
                        {
                            "name": "usage_days",
                            "in": "query",
                            "description": "参与者用量回看天数，最大 90。",
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 90,
                                "default": 7,
                            },
                        },
                        {
                            "name": "usage_precision",
                            "in": "query",
                            "description": "参与者用量序列的时间粒度。",
                            "schema": {
                                "type": "string",
                                "enum": ["raw", "hour", "day"],
                                "default": "hour",
                            },
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "额度统计页面的数据。",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {
                                                "$ref": "#/components/schemas/SuccessResponse"
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "data": {
                                                        "$ref": "#/components/schemas/Statistics"
                                                    }
                                                },
                                            },
                                        ]
                                    }
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/v1/statistics/participants/{participant_id}/api-usage": {
                "get": {
                    "summary": "读取参与者 API Key 用量构成",
                    "operationId": "getParticipantApiUsage",
                    "parameters": [
                        {
                            "name": "participant_id",
                            "in": "path",
                            "required": True,
                            "description": "Sub2Pool 参与者 ID，不是 Sub2API 用户 ID。",
                            "schema": {"type": "integer", "minimum": 1},
                        },
                        {
                            "name": "account_id",
                            "in": "query",
                            "required": True,
                            "description": "Sub2Pool 监控账号 ID，不是上游账号 ID。",
                            "schema": {"type": "integer", "minimum": 1},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "参与者当前周期的 API Key 用量汇总。",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "allOf": [
                                            {
                                                "$ref": "#/components/schemas/SuccessResponse"
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "data": {
                                                        "$ref": "#/components/schemas/ParticipantApiUsage"
                                                    }
                                                },
                                            },
                                        ]
                                    }
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                        "404": {"$ref": "#/components/responses/NotFound"},
                        "409": {"$ref": "#/components/responses/Conflict"},
                        "502": {"$ref": "#/components/responses/UpstreamError"},
                    },
                }
            },
            "/v1/notifications": {
                "get": {
                    "summary": "读取通知记录",
                    "operationId": "listNotifications",
                    "parameters": [
                        *_pagination_parameters(),
                        {
                            "name": "from",
                            "in": "query",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "to",
                            "in": "query",
                            "schema": {"type": "string", "format": "date-time"},
                        },
                        {
                            "name": "event_type",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": [
                                    "limit_exhausted",
                                    "recommendation_changed",
                                    "rate_changed",
                                    "collection_error",
                                    "test",
                                ],
                            },
                        },
                        {
                            "name": "participant",
                            "in": "query",
                            "description": "参与者 ID，或 system 表示系统通知。",
                            "schema": {
                                "oneOf": [
                                    {"type": "integer", "minimum": 1},
                                    {"type": "string", "const": "system"},
                                ]
                            },
                        },
                        {
                            "name": "subject",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "status",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "enum": ["sent", "skipped", "failed"],
                            },
                        },
                    ],
                    "responses": {
                        "200": _success_response(
                            "筛选后的通知发送记录、分页、汇总和筛选选项。",
                            {"$ref": "#/components/schemas/NotificationList"},
                        ),
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ReadOnlyApiKey": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Sub2Pool API Key",
                    "description": (
                        "系统设置中生成的永久只读 Key，格式为 sub2pool_...。"
                    ),
                }
            },
            "responses": {
                "Unauthorized": {
                    "description": "未提供 API Key，或 Key 无效、已轮换、已废弃。",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "BadRequest": {
                    "description": "查询参数无效。",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "NotFound": {
                    "description": "请求的资源不存在。",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "Conflict": {
                    "description": "尚未配置上游账号或尚无当前上游周期。",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "UpstreamError": {
                    "description": "读取 Sub2API 数据失败。",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
            },
            "schemas": {
                "SuccessResponse": {
                    "type": "object",
                    "required": ["ok", "data"],
                    "properties": {"ok": {"type": "boolean", "const": True}},
                },
                "ErrorResponse": {
                    "type": "object",
                    "required": ["ok", "message"],
                    "properties": {
                        "ok": {"type": "boolean", "const": False},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                },
                "ApiIndexResponse": {
                    "allOf": [
                        {"$ref": "#/components/schemas/SuccessResponse"},
                        {
                            "type": "object",
                            "properties": {
                                "data": {
                                    "type": "object",
                                    "required": [
                                        "name",
                                        "version",
                                        "openapi",
                                        "authentication",
                                        "endpoints",
                                    ],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "version": {"type": "string"},
                                        "openapi": {"type": "string"},
                                        "authentication": {"type": "object"},
                                        "endpoints": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/EndpointIndexItem"
                                            },
                                        },
                                    },
                                }
                            },
                        },
                    ]
                },
                "EndpointIndexItem": {
                    "type": "object",
                    "required": ["method", "path", "description"],
                    "properties": {
                        "method": {"type": "string", "const": "GET"},
                        "path": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
                "MonitoredAccount": {
                    "type": "object",
                    "required": [
                        "id",
                        "external_account_id",
                        "name",
                        "enabled",
                        "quota_query_mode",
                        "last_local_check_at",
                        "last_upstream_check_at",
                        "last_success_at",
                        "next_local_check_at",
                        "last_error",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "quota_query_mode": {
                            "type": "string",
                            "enum": ["passive", "direct"],
                        },
                        "last_local_check_at": nullable_string,
                        "last_upstream_check_at": nullable_string,
                        "last_success_at": nullable_string,
                        "next_local_check_at": nullable_string,
                        "last_error": {"type": "string"},
                    },
                },
                "Dashboard": {
                    "type": "object",
                    "required": [
                        "configured",
                        "monitoring_enabled",
                        "accounts",
                        "selected_account_id",
                        "last_local_check_at",
                        "last_upstream_check_at",
                        "snapshot_stale",
                        "last_success_at",
                        "last_error",
                        "quota_query_mode",
                        "sub2api_admin_url",
                        "fast_correction_enabled",
                        "weekly_quota_model",
                        "cycle",
                        "participants",
                        "needs_manual_update_count",
                    ],
                    "properties": {
                        "configured": {"type": "boolean"},
                        "monitoring_enabled": {"type": "boolean"},
                        "accounts": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/MonitoredAccount"
                            },
                        },
                        "selected_account_id": _nullable("integer"),
                        "last_local_check_at": nullable_string,
                        "last_upstream_check_at": nullable_string,
                        "snapshot_stale": {"type": "boolean"},
                        "last_success_at": nullable_string,
                        "last_error": {"type": "string"},
                        "quota_query_mode": {
                            "type": ["string", "null"],
                            "enum": ["passive", "direct", None],
                        },
                        "sub2api_admin_url": {"type": "string", "format": "uri"},
                        "fast_correction_enabled": {"type": "boolean"},
                        "weekly_quota_model": {
                            "type": "string",
                            "enum": ["time_varying", "constant_average"],
                        },
                        "cycle": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/DashboardCycle"},
                                {"type": "null"},
                            ]
                        },
                        "participants": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Participant"},
                        },
                        "needs_manual_update_count": {"type": "integer"},
                    },
                },
                "DashboardCycle": {
                    "type": "object",
                    "required": [
                        "id",
                        "observed_at",
                        "starts_at",
                        "resets_at",
                        "upstream_used_percent",
                        "interval_used_percent",
                        "effective_usd_per_percent",
                        "selected_total_cost",
                        "selected_total_cost_breakdown",
                        "start_cost_breakdown",
                        "unattributed_used_percent",
                        "sample_note",
                        "snapshot_sampled_at",
                        "rate_calculated",
                        "estimated_used_percent",
                        "capacity_lower_usd",
                        "capacity_upper_usd",
                        "model_diagnostics",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "observed_at": {"type": "string", "format": "date-time"},
                        "starts_at": nullable_string,
                        "resets_at": {"type": "string", "format": "date-time"},
                        "upstream_used_percent": {"type": "number"},
                        "interval_used_percent": {"type": "number"},
                        "effective_usd_per_percent": nullable_number,
                        "selected_total_cost": {"type": "number"},
                        "selected_total_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "start_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "unattributed_used_percent": {"type": "number"},
                        "sample_note": {"type": "string"},
                        "snapshot_sampled_at": nullable_string,
                        "rate_calculated": {"type": "boolean"},
                        "estimated_used_percent": {"type": "number"},
                        "capacity_lower_usd": nullable_number,
                        "capacity_upper_usd": nullable_number,
                        "model_diagnostics": {"type": "object"},
                    },
                },
                "CostBreakdown": {
                    "type": "object",
                    "required": [
                        "sub2api_cost_usd",
                        "fast_correction_usd",
                        "total_cost_usd",
                    ],
                    "properties": {
                        "sub2api_cost_usd": {"type": "number"},
                        "fast_correction_usd": {"type": "number"},
                        "total_cost_usd": {"type": "number"},
                    },
                },
                "AccountStatus": {
                    "type": "object",
                    "required": [
                        "configured",
                        "sampled_at",
                        "stats_days",
                        "connection_error",
                        "accounts",
                    ],
                    "properties": {
                        "configured": {"type": "boolean"},
                        "sampled_at": {"type": "string", "format": "date-time"},
                        "stats_days": {"type": "integer"},
                        "connection_error": nullable_string,
                        "accounts": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/AccountStatusAccount"
                            },
                        },
                    },
                },
                "AccountStatusAccount": {
                    "type": "object",
                    "required": [
                        "id",
                        "external_account_id",
                        "name",
                        "enabled",
                        "quota_query_mode",
                        "runtime",
                        "usage",
                        "stats",
                        "warnings",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "quota_query_mode": {
                            "type": "string",
                            "enum": ["passive", "direct"],
                        },
                        "runtime": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/AccountRuntimeStatus"
                                },
                                {"type": "null"},
                            ]
                        },
                        "usage": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/AccountUsageStatus"},
                                {"type": "null"},
                            ]
                        },
                        "stats": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/AccountUsageStats"},
                                {"type": "null"},
                            ]
                        },
                        "warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "AccountRuntimeStatus": {
                    "type": "object",
                    "properties": {
                        "name": nullable_string,
                        "account_type": nullable_string,
                        "status": nullable_string,
                        "schedulable": _nullable("boolean"),
                        "current_concurrency": _nullable("integer"),
                        "concurrency_limit": _nullable("integer"),
                        "last_used_at": nullable_string,
                        "rate_limited_at": nullable_string,
                        "rate_limit_reset_at": nullable_string,
                        "overload_until": nullable_string,
                        "temp_unschedulable_until": nullable_string,
                        "temp_unschedulable_reason": nullable_string,
                        "error_message": nullable_string,
                    },
                },
                "AccountUsageWindow": {
                    "type": "object",
                    "properties": {
                        "used_percent": nullable_number,
                        "reset_at": nullable_string,
                        "remaining_seconds": _nullable("integer"),
                        "request_count": _nullable("integer"),
                        "token_count": _nullable("integer"),
                        "account_cost_usd": nullable_number,
                        "standard_cost_usd": nullable_number,
                        "user_cost_usd": nullable_number,
                    },
                },
                "AccountUsageStatus": {
                    "type": "object",
                    "properties": {
                        "source": nullable_string,
                        "updated_at": nullable_string,
                        "five_hour": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/AccountUsageWindow"},
                                {"type": "null"},
                            ]
                        },
                        "seven_day": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/AccountUsageWindow"},
                                {"type": "null"},
                            ]
                        },
                        "needs_verify": _nullable("boolean"),
                        "is_banned": _nullable("boolean"),
                        "needs_reauth": _nullable("boolean"),
                        "error_code": nullable_string,
                        "error": nullable_string,
                    },
                },
                "AccountUsageStats": {
                    "type": "object",
                    "properties": {
                        "days": _nullable("integer"),
                        "actual_days_used": _nullable("integer"),
                        "account_cost_usd": nullable_number,
                        "standard_cost_usd": nullable_number,
                        "user_cost_usd": nullable_number,
                        "request_count": _nullable("integer"),
                        "token_count": _nullable("integer"),
                        "avg_daily_cost_usd": nullable_number,
                        "avg_daily_request_count": nullable_number,
                        "avg_daily_token_count": nullable_number,
                        "avg_duration_ms": nullable_number,
                        "today": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                    },
                },
                "Participant": {
                    "type": "object",
                    "required": [
                        "id",
                        "name",
                        "email",
                        "sub2api_user_id",
                        "sub2api_username",
                        "sub2api_email",
                        "sub2api_identity",
                        "share_percent",
                        "is_owner",
                        "enabled",
                        "notes",
                        "latest_balance_usd",
                        "last_checked_at",
                        "account_breakdowns",
                        "snapshot",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "sub2api_user_id": {"type": "integer"},
                        "sub2api_username": {"type": "string"},
                        "sub2api_email": {"type": "string"},
                        "sub2api_identity": {"type": "string"},
                        "share_percent": {"type": "number"},
                        "is_owner": {"type": "boolean"},
                        "enabled": {"type": "boolean"},
                        "notes": {"type": "string"},
                        "latest_balance_usd": nullable_number,
                        "last_checked_at": nullable_string,
                        "account_breakdowns": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/AccountBreakdown"
                            },
                        },
                        "snapshot": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/AggregateRecommendation"
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
                "ParticipantSnapshot": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "participant_name",
                        "selected_cost",
                        "delta_cost",
                        "charged_delta_percent",
                        "charged_cycle_percent",
                        "charged_percent_lower",
                        "charged_percent_upper",
                        "remaining_share_percent",
                        "current_balance_usd",
                        "recommended_balance_usd",
                        "recommended_balance_min_usd",
                        "recommended_balance_max_usd",
                        "deterministic_balance_min_usd",
                        "deterministic_balance_max_usd",
                        "balance_difference_usd",
                        "is_overused",
                        "overused_percent",
                        "overused_percent_min",
                        "overused_percent_max",
                        "needs_manual_update",
                        "recommendation_applied",
                        "reason",
                        "allocation_model",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
                        "selected_cost": {"type": "number"},
                        "delta_cost": nullable_number,
                        "charged_delta_percent": {"type": "number"},
                        "charged_cycle_percent": {"type": "number"},
                        "charged_percent_lower": nullable_number,
                        "charged_percent_upper": nullable_number,
                        "remaining_share_percent": {"type": "number"},
                        "current_balance_usd": nullable_number,
                        "recommended_balance_usd": nullable_number,
                        "recommended_balance_min_usd": nullable_number,
                        "recommended_balance_max_usd": nullable_number,
                        "deterministic_balance_min_usd": nullable_number,
                        "deterministic_balance_max_usd": nullable_number,
                        "balance_difference_usd": nullable_number,
                        "is_overused": {"type": "boolean"},
                        "overused_percent": {"type": "number"},
                        "overused_percent_min": {"type": "number"},
                        "overused_percent_max": {"type": "number"},
                        "needs_manual_update": {"type": "boolean"},
                        "recommendation_applied": {"type": "boolean"},
                        "reason": {"type": "string"},
                        "allocation_model": {
                            "type": "string",
                            "enum": ["time_varying", "constant_average"],
                        },
                    },
                },
                "MonitoredAccountSummary": {
                    "type": "object",
                    "required": ["id", "external_account_id", "name"],
                    "properties": {
                        "id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                "AccountBreakdown": {
                    "type": "object",
                    "required": [
                        "id",
                        "account_id",
                        "external_account_id",
                        "account_name",
                        "account_enabled",
                        "snapshot",
                        "latest_selected_cost",
                        "last_checked_at",
                    ],
                    "properties": {
                        "id": {
                            "oneOf": [
                                {"type": "integer"},
                                {"type": "null"},
                            ]
                        },
                        "account_id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "account_name": {"type": "string"},
                        "account_enabled": {"type": "boolean"},
                        "latest_selected_cost": nullable_number,
                        "last_checked_at": nullable_string,
                        "snapshot": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/ParticipantSnapshot"
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
                "AggregateRecommendationSource": {
                    "type": "object",
                    "required": [
                        "account_id",
                        "external_account_id",
                        "account_name",
                        "contract_share_percent",
                        "snapshot",
                        "net_position_usd",
                        "net_position_min_usd",
                        "net_position_max_usd",
                        "contribution_usd",
                        "contribution_min_usd",
                        "contribution_max_usd",
                    ],
                    "properties": {
                        "account_id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "account_name": {"type": "string"},
                        "contract_share_percent": {"type": "number"},
                        "net_position_usd": nullable_number,
                        "net_position_min_usd": nullable_number,
                        "net_position_max_usd": nullable_number,
                        "snapshot": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/ParticipantSnapshot"
                                },
                                {"type": "null"},
                            ]
                        },
                        "contribution_usd": nullable_number,
                        "contribution_min_usd": nullable_number,
                        "contribution_max_usd": nullable_number,
                    },
                },
                "AggregateRecommendation": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "participant_name",
                        "share_percent",
                        "selected_cost",
                        "charged_cycle_percent",
                        "current_balance_usd",
                        "recommended_balance_usd",
                        "recommended_balance_min_usd",
                        "recommended_balance_max_usd",
                        "balance_difference_usd",
                        "is_overused",
                        "needs_manual_update",
                        "recommendation_applied",
                        "recommendation_complete",
                        "account_count",
                        "reason",
                        "allocation_model",
                        "sources",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
                        "share_percent": {"type": "number"},
                        "selected_cost": {"type": "number"},
                        "charged_cycle_percent": {"type": "number"},
                        "current_balance_usd": nullable_number,
                        "recommended_balance_usd": nullable_number,
                        "recommended_balance_min_usd": nullable_number,
                        "recommended_balance_max_usd": nullable_number,
                        "balance_difference_usd": nullable_number,
                        "is_overused": {"type": "boolean"},
                        "needs_manual_update": {"type": "boolean"},
                        "recommendation_applied": {"type": "boolean"},
                        "recommendation_complete": {"type": "boolean"},
                        "account_count": {"type": "integer"},
                        "reason": {"type": "string"},
                        "allocation_model": {
                            "type": "string",
                            "const": "pooled_account_sum",
                        },
                        "sources": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/AggregateRecommendationSource"
                            },
                        },
                    },
                },
                "Pagination": {
                    "type": "object",
                    "required": ["page", "page_size", "total", "total_pages"],
                    "properties": {
                        "page": {"type": "integer"},
                        "page_size": {"type": "integer"},
                        "total": {"type": "integer"},
                        "total_pages": {"type": "integer"},
                    },
                },
                "ObservationList": {
                    "type": "object",
                    "required": [
                        "account",
                        "items",
                        "fast_correction_enabled",
                        "pagination",
                        "summary",
                    ],
                    "properties": {
                        "account": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/MonitoredAccountSummary"
                                },
                                {"type": "null"},
                            ]
                        },
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Observation"},
                        },
                        "fast_correction_enabled": {"type": "boolean"},
                        "pagination": {
                            "$ref": "#/components/schemas/Pagination"
                        },
                        "summary": {
                            "type": "object",
                            "required": [
                                "total",
                                "valid_count",
                                "passive_count",
                                "excluded_count",
                            ],
                            "properties": {
                                "total": {"type": "integer"},
                                "valid_count": {"type": "integer"},
                                "passive_count": {"type": "integer"},
                                "excluded_count": {"type": "integer"},
                            },
                        },
                    },
                },
                "Observation": {
                    "type": "object",
                    "required": [
                        "id",
                        "observed_at",
                        "source",
                        "account_id",
                        "attribution_started_at",
                        "upstream_resets_at",
                        "upstream_used_percent",
                        "interval_used_percent",
                        "raw_selected_total_cost",
                        "selected_total_cost",
                        "cost_window_started_at",
                        "cost_window_ended_at",
                        "interval_cost_started_at",
                        "interval_cost",
                        "interval_cost_source",
                        "normalized_total_cost",
                        "delta_percent",
                        "delta_cost",
                        "sample_usd_per_percent",
                        "effective_usd_per_percent",
                        "estimated_used_percent",
                        "capacity_lower_usd",
                        "capacity_upper_usd",
                        "model_diagnostics",
                        "fast_correction_usd",
                        "fast_correction_calculated",
                        "valid_sample",
                        "sample_note",
                        "rate_method",
                        "query_mode",
                        "snapshot_sampled_at",
                        "excluded",
                        "excluded_at",
                        "exclusion_reason",
                        "exclusion_source",
                        "is_manual_start",
                        "manual_start_reason",
                        "manual_start_set_at",
                        "manual_start_end_id",
                        "manual_start_end_observed_at",
                        "participants",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "observed_at": {"type": "string", "format": "date-time"},
                        "source": {
                            "type": "string",
                            "enum": [
                                "scheduled",
                                "manual",
                                "exhausted",
                                "reset",
                            ],
                        },
                        "account_id": {
                            "type": "integer",
                            "description": "Sub2API 上游账号 ID。",
                        },
                        "attribution_started_at": nullable_string,
                        "upstream_resets_at": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "upstream_used_percent": {"type": "number"},
                        "interval_used_percent": {"type": "number"},
                        "raw_selected_total_cost": {"type": "number"},
                        "selected_total_cost": {"type": "number"},
                        "cost_window_started_at": nullable_string,
                        "cost_window_ended_at": nullable_string,
                        "interval_cost_started_at": nullable_string,
                        "interval_cost": nullable_number,
                        "interval_cost_source": {"type": "string"},
                        "normalized_total_cost": {"type": "number"},
                        "delta_percent": nullable_number,
                        "delta_cost": nullable_number,
                        "sample_usd_per_percent": nullable_number,
                        "effective_usd_per_percent": {"type": "number"},
                        "estimated_used_percent": {"type": "number"},
                        "capacity_lower_usd": nullable_number,
                        "capacity_upper_usd": nullable_number,
                        "model_diagnostics": {"type": "object"},
                        "fast_correction_usd": nullable_number,
                        "fast_correction_calculated": {"type": "boolean"},
                        "valid_sample": {"type": "boolean"},
                        "sample_note": {"type": "string"},
                        "rate_method": {"type": "string"},
                        "query_mode": {
                            "type": "string",
                            "enum": ["passive", "direct"],
                        },
                        "snapshot_sampled_at": nullable_string,
                        "excluded": {"type": "boolean"},
                        "excluded_at": nullable_string,
                        "exclusion_reason": {"type": "string"},
                        "exclusion_source": {
                            "type": "string",
                            "enum": ["", "manual", "automatic"],
                        },
                        "is_manual_start": {"type": "boolean"},
                        "manual_start_reason": {"type": "string"},
                        "manual_start_set_at": nullable_string,
                        "manual_start_end_id": _nullable("integer"),
                        "manual_start_end_observed_at": nullable_string,
                        "participants": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/ParticipantSnapshot"
                            },
                        },
                    },
                },
                "FastCorrectionDetail": {
                    "type": "object",
                    "required": [
                        "observation_id",
                        "started_at",
                        "ended_at",
                        "calculated",
                        "cost_basis",
                        "cost_basis_label",
                        "request_count",
                        "fast_request_count",
                        "non_fast_request_count",
                        "fast_billed_cost_usd",
                        "correction_usd",
                        "corrected_fast_cost_usd",
                        "collection_error",
                        "users",
                    ],
                    "properties": {
                        "observation_id": {"type": "integer"},
                        "started_at": nullable_string,
                        "ended_at": {"type": "string", "format": "date-time"},
                        "calculated": {"type": "boolean"},
                        "cost_basis": {
                            "type": "string",
                            "enum": ["actual", "standard"],
                        },
                        "cost_basis_label": {"type": "string"},
                        "request_count": _nullable("integer"),
                        "fast_request_count": {"type": "integer"},
                        "non_fast_request_count": _nullable("integer"),
                        "fast_billed_cost_usd": {"type": "number"},
                        "correction_usd": {"type": "number"},
                        "corrected_fast_cost_usd": {"type": "number"},
                        "collection_error": {"type": "string"},
                        "users": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/FastCorrectionUser"
                            },
                        },
                    },
                },
                "FastCorrectionUser": {
                    "type": "object",
                    "required": [
                        "sub2api_user_id",
                        "username",
                        "email",
                        "display_name",
                        "request_count",
                        "fast_request_count",
                        "non_fast_request_count",
                        "fast_billed_cost_usd",
                        "correction_usd",
                        "corrected_fast_cost_usd",
                    ],
                    "properties": {
                        "sub2api_user_id": {"type": "integer"},
                        "username": {"type": "string"},
                        "email": {"type": "string"},
                        "display_name": {"type": "string"},
                        "request_count": _nullable("integer"),
                        "fast_request_count": {"type": "integer"},
                        "non_fast_request_count": _nullable("integer"),
                        "fast_billed_cost_usd": {"type": "number"},
                        "correction_usd": {"type": "number"},
                        "corrected_fast_cost_usd": {"type": "number"},
                    },
                },
                "ParticleTrajectory": {
                    "type": "object",
                    "required": ["available", "message"],
                    "properties": {
                        "account": {
                            "$ref": "#/components/schemas/MonitoredAccountSummary"
                        },
                        "available": {"type": "boolean"},
                        "message": {"type": "string"},
                        "algorithm": {"type": "string"},
                        "seed": {"type": "integer"},
                        "particle_count": {"type": "integer"},
                        "representative_particle_count": {"type": "integer"},
                        "credible_mass_percent": {"type": "number"},
                        "selected_period_id": {"type": "integer"},
                        "periods": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/TrajectoryPeriod"
                            },
                        },
                        "segment": {"type": "object"},
                        "latest": {"type": "object"},
                        "points": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/TrajectoryPoint"
                            },
                        },
                        "promotions": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/RangePromotion"
                            },
                        },
                    },
                },
                "TrajectoryPeriod": {
                    "type": "object",
                    "required": [
                        "id",
                        "sequence",
                        "started_at",
                        "first_observed_at",
                        "last_observed_at",
                        "resets_at",
                        "ended_at",
                        "observation_count",
                        "is_current",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "sequence": {"type": "integer"},
                        "started_at": {"type": "string", "format": "date-time"},
                        "first_observed_at": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "last_observed_at": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "resets_at": {"type": "string", "format": "date-time"},
                        "ended_at": {"type": "string", "format": "date-time"},
                        "observation_count": {"type": "integer"},
                        "is_current": {"type": "boolean"},
                    },
                },
                "TrajectoryPoint": {
                    "type": "object",
                    "required": [
                        "observation_id",
                        "observed_at",
                        "source",
                        "displayed_percent",
                        "estimated_percent",
                        "estimated_percent_lower",
                        "estimated_percent_upper",
                        "capacity_usd",
                        "capacity_lower_usd",
                        "capacity_upper_usd",
                        "range_min_usd",
                        "range_max_usd",
                        "range_stage",
                        "range_direction",
                        "ess_fraction",
                        "resampled",
                        "boundary_mass",
                        "particles_usd",
                    ],
                    "properties": {
                        "observation_id": {"type": "integer"},
                        "observed_at": {"type": "string", "format": "date-time"},
                        "source": {"type": "string"},
                        "displayed_percent": {"type": "number"},
                        "estimated_percent": {"type": "number"},
                        "estimated_percent_lower": {"type": "number"},
                        "estimated_percent_upper": {"type": "number"},
                        "capacity_usd": {"type": "number"},
                        "capacity_lower_usd": {"type": "number"},
                        "capacity_upper_usd": {"type": "number"},
                        "range_min_usd": {"type": "number"},
                        "range_max_usd": {"type": "number"},
                        "range_stage": {"type": "integer"},
                        "range_direction": {
                            "type": ["string", "null"],
                            "enum": ["upper", "lower", None],
                        },
                        "ess_fraction": {"type": "number"},
                        "resampled": {"type": "boolean"},
                        "boundary_mass": {
                            "type": "object",
                            "required": ["lower", "upper"],
                            "properties": {
                                "lower": {"type": "number"},
                                "upper": {"type": "number"},
                            },
                        },
                        "particles_usd": {
                            "type": "array",
                            "items": {"type": "number"},
                        },
                    },
                },
                "RangePromotion": {
                    "type": "object",
                    "required": [
                        "stage",
                        "direction",
                        "occurred_at",
                        "from_range_usd",
                        "to_range_usd",
                        "boundary_mass",
                        "display_residual_pp",
                    ],
                    "properties": {
                        "stage": {"type": "integer"},
                        "direction": {
                            "type": "string",
                            "enum": ["upper", "lower"],
                        },
                        "occurred_at": {"type": "string", "format": "date-time"},
                        "from_range_usd": {
                            "type": "array",
                            "prefixItems": [
                                {"type": "number"},
                                {"type": "number"},
                            ],
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "to_range_usd": {
                            "type": "array",
                            "prefixItems": [
                                {"type": "number"},
                                {"type": "number"},
                            ],
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "boundary_mass": {"type": "number"},
                        "display_residual_pp": {"type": "number"},
                    },
                },
                "NotificationList": {
                    "type": "object",
                    "required": [
                        "items",
                        "pagination",
                        "summary",
                        "filter_options",
                    ],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/Notification"
                            },
                        },
                        "pagination": {
                            "$ref": "#/components/schemas/Pagination"
                        },
                        "summary": {
                            "type": "object",
                            "required": [
                                "total",
                                "sent_count",
                                "failed_count",
                            ],
                            "properties": {
                                "total": {"type": "integer"},
                                "sent_count": {"type": "integer"},
                                "failed_count": {"type": "integer"},
                            },
                        },
                        "filter_options": {
                            "$ref": "#/components/schemas/NotificationFilterOptions"
                        },
                    },
                },
                "Notification": {
                    "type": "object",
                    "required": [
                        "id",
                        "event_type",
                        "event_type_label",
                        "severity",
                        "participant_name",
                        "recipient",
                        "subject",
                        "body",
                        "status",
                        "status_label",
                        "error",
                        "created_at",
                        "sent_at",
                    ],
                    "properties": {
                        "id": {"type": "integer"},
                        "event_type": {"type": "string"},
                        "event_type_label": {"type": "string"},
                        "severity": {"type": "string"},
                        "participant_name": nullable_string,
                        "recipient": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["sent", "skipped", "failed"],
                        },
                        "status_label": {"type": "string"},
                        "error": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "sent_at": nullable_string,
                    },
                },
                "NotificationFilterOptions": {
                    "type": "object",
                    "required": ["types", "participants", "statuses"],
                    "properties": {
                        "types": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SelectOption"},
                        },
                        "participants": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/ParticipantOption"
                            },
                        },
                        "statuses": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SelectOption"},
                        },
                    },
                },
                "SelectOption": {
                    "type": "object",
                    "required": ["value", "label"],
                    "properties": {
                        "value": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
                "ParticipantOption": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
                "Statistics": {
                    "type": "object",
                    "required": [
                        "account",
                        "capacity_period",
                        "capacity_series",
                        "fast_correction_enabled",
                        "capacity_summary",
                        "usage_days",
                        "usage_precision",
                        "sample_interval_minutes",
                        "participant_series",
                    ],
                    "properties": {
                        "account": {
                            "$ref": "#/components/schemas/MonitoredAccountSummary"
                        },
                        "capacity_period": {
                            "type": "string",
                            "enum": ["day", "month"],
                        },
                        "capacity_series": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CapacityPoint"},
                        },
                        "fast_correction_enabled": {"type": "boolean"},
                        "capacity_summary": {
                            "$ref": "#/components/schemas/CapacitySummary"
                        },
                        "usage_days": {"type": "integer"},
                        "usage_precision": {
                            "type": "string",
                            "enum": ["raw", "hour", "day"],
                        },
                        "sample_interval_minutes": {"type": "integer"},
                        "participant_series": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/ParticipantUsageSeries"
                            },
                        },
                    },
                },
                "CapacityPoint": {
                    "type": "object",
                    "required": [
                        "period",
                        "weekly_total_usd",
                        "minimum_usd",
                        "maximum_usd",
                        "sample_count",
                        "basis",
                        "daily_total_usd",
                        "daily_basis",
                    ],
                    "properties": {
                        "period": {"type": "string"},
                        "weekly_total_usd": {"type": "number"},
                        "minimum_usd": {"type": "number"},
                        "maximum_usd": {"type": "number"},
                        "sample_count": {"type": "integer"},
                        "basis": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CapacityClosingBasis"},
                                {"type": "null"},
                            ]
                        },
                        "daily_total_usd": nullable_number,
                        "daily_basis": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/CapacityDailyClosingBasis"
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
                "CapacitySummary": {
                    "type": "object",
                    "required": ["cycle", "today"],
                    "properties": {
                        "cycle": {
                            "oneOf": [
                                {
                                    "$ref": "#/components/schemas/CycleCapacityEstimate"
                                },
                                {"type": "null"},
                            ]
                        },
                        "today": {
                            "$ref": "#/components/schemas/DailyCapacityEstimate"
                        },
                    },
                },
                "CycleCapacityEstimate": {
                    "type": "object",
                    "required": [
                        "estimate_usd",
                        "raw_estimate_usd",
                        "start_cost_usd",
                        "start_percent",
                        "end_cost_usd",
                        "start_cost_breakdown",
                        "end_cost_breakdown",
                        "end_percent",
                        "cost_usd",
                        "used_percent",
                        "effective_usd_per_percent",
                        "calculation_model",
                        "rate_calculated",
                        "confidence",
                        "observed_at",
                        "starts_at",
                        "resets_at",
                    ],
                    "properties": {
                        "estimate_usd": nullable_number,
                        "raw_estimate_usd": nullable_number,
                        "start_cost_usd": {"type": "number"},
                        "start_percent": {"type": "number"},
                        "end_cost_usd": {"type": "number"},
                        "start_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "end_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "end_percent": {"type": "number"},
                        "cost_usd": {"type": "number"},
                        "used_percent": {"type": "number"},
                        "effective_usd_per_percent": nullable_number,
                        "calculation_model": {
                            "type": "string",
                            "const": "endpoint_ratio",
                        },
                        "rate_calculated": {"type": "boolean"},
                        "confidence": {
                            "type": "string",
                            "enum": ["低", "中", "高"],
                        },
                        "observed_at": {"type": "string", "format": "date-time"},
                        "starts_at": {"type": "string", "format": "date-time"},
                        "resets_at": {"type": "string", "format": "date-time"},
                    },
                },
                "DailyCapacityEstimate": {
                    "type": "object",
                    "required": [
                        "estimate_usd",
                        "minimum_usd",
                        "maximum_usd",
                        "start_cost_usd",
                        "start_percent",
                        "end_cost_usd",
                        "end_percent",
                        "start_cost_breakdown",
                        "end_cost_breakdown",
                        "cost_delta_usd",
                        "percent_delta",
                        "sample_count",
                        "observed_from",
                        "observed_to",
                        "min_percent_span",
                        "sufficient",
                        "reason",
                    ],
                    "properties": {
                        "estimate_usd": nullable_number,
                        "minimum_usd": nullable_number,
                        "maximum_usd": nullable_number,
                        "start_cost_usd": nullable_number,
                        "start_percent": nullable_number,
                        "end_cost_usd": nullable_number,
                        "end_percent": nullable_number,
                        "start_cost_breakdown": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CostBreakdown"},
                                {"type": "null"},
                            ]
                        },
                        "end_cost_breakdown": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/CostBreakdown"},
                                {"type": "null"},
                            ]
                        },
                        "cost_delta_usd": nullable_number,
                        "percent_delta": nullable_number,
                        "sample_count": {"type": "integer"},
                        "observed_from": nullable_string,
                        "observed_to": nullable_string,
                        "min_percent_span": {"type": "number"},
                        "sufficient": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                },
                "CapacityClosingBasis": {
                    "type": "object",
                    "required": [
                        "observed_at",
                        "starts_at",
                        "start_cost_usd",
                        "start_percent",
                        "end_cost_usd",
                        "start_cost_breakdown",
                        "end_cost_breakdown",
                        "end_percent",
                        "raw_estimate_usd",
                        "estimate_usd",
                        "effective_usd_per_percent",
                        "calculation_model",
                        "rate_source",
                        "sample_note",
                    ],
                    "properties": {
                        "observed_at": {"type": "string", "format": "date-time"},
                        "starts_at": nullable_string,
                        "start_cost_usd": {"type": "number"},
                        "start_percent": {"type": "number"},
                        "end_cost_usd": {"type": "number"},
                        "start_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "end_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "end_percent": {"type": "number"},
                        "raw_estimate_usd": nullable_number,
                        "estimate_usd": nullable_number,
                        "effective_usd_per_percent": nullable_number,
                        "calculation_model": {
                            "type": "string",
                            "const": "endpoint_ratio",
                        },
                        "rate_source": {"type": "string"},
                        "sample_note": {"type": "string"},
                    },
                },
                "CapacityDailyClosingBasis": {
                    "type": "object",
                    "required": [
                        "observed_from",
                        "observed_to",
                        "start_cost_usd",
                        "start_percent",
                        "end_cost_usd",
                        "end_percent",
                        "start_cost_breakdown",
                        "end_cost_breakdown",
                        "cost_delta_usd",
                        "percent_delta",
                        "estimate_usd",
                        "minimum_usd",
                        "maximum_usd",
                        "sample_count",
                        "min_percent_span",
                    ],
                    "properties": {
                        "observed_from": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "observed_to": {
                            "type": "string",
                            "format": "date-time",
                        },
                        "start_cost_usd": {"type": "number"},
                        "start_percent": {"type": "number"},
                        "end_cost_usd": {"type": "number"},
                        "end_percent": {"type": "number"},
                        "start_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "end_cost_breakdown": {
                            "$ref": "#/components/schemas/CostBreakdown"
                        },
                        "cost_delta_usd": {"type": "number"},
                        "percent_delta": {"type": "number"},
                        "estimate_usd": {"type": "number"},
                        "minimum_usd": {"type": "number"},
                        "maximum_usd": nullable_number,
                        "sample_count": {"type": "integer"},
                        "min_percent_span": {"type": "number"},
                    },
                },
                "ParticipantUsageSeries": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "participant_name",
                        "account_id",
                        "external_account_id",
                        "sub2api_user_id",
                        "points",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
                        "account_id": {"type": "integer"},
                        "external_account_id": {"type": "integer"},
                        "sub2api_user_id": {"type": "integer"},
                        "points": {
                            "type": "array",
                            "items": {
                                "$ref": "#/components/schemas/ParticipantUsagePoint"
                            },
                        },
                    },
                },
                "ParticipantUsagePoint": {
                    "type": "object",
                    "required": [
                        "observed_at",
                        "label",
                        "account_cycle_usage_usd",
                        "balance_usd",
                    ],
                    "properties": {
                        "observed_at": {"type": "string", "format": "date-time"},
                        "label": {"type": "string"},
                        "account_cycle_usage_usd": {"type": "number"},
                        "balance_usd": nullable_number,
                    },
                },
                "ParticipantApiUsage": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "participant_name",
                        "sub2api_user_id",
                        "starts_at",
                        "observed_to",
                        "cost_basis",
                        "fast_correction_enabled",
                        "participant_total_usd",
                        "participant_weekly_percent",
                        "weekly_total_estimate_usd",
                        "api_keys",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
                        "sub2api_user_id": {"type": "integer"},
                        "starts_at": {"type": "string", "format": "date-time"},
                        "observed_to": {"type": "string", "format": "date-time"},
                        "cost_basis": {
                            "type": "string",
                            "enum": ["actual", "standard"],
                        },
                        "fast_correction_enabled": {"type": "boolean"},
                        "participant_total_usd": {"type": "number"},
                        "weekly_total_estimate_usd": nullable_number,
                        "participant_weekly_percent": {"type": "number"},
                        "api_keys": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ApiKeyUsage"},
                        },
                    },
                },
                "ApiKeyUsage": {
                    "type": "object",
                    "required": [
                        "api_key_id",
                        "name",
                        "status",
                        "usage_usd",
                        "participant_usage_percent",
                        "weekly_quota_percent",
                    ],
                    "properties": {
                        "api_key_id": _nullable("integer"),
                        "name": {"type": "string"},
                        "status": {"type": "string"},
                        "usage_usd": {"type": "number"},
                        "participant_usage_percent": {"type": "number"},
                        "weekly_quota_percent": {"type": "number"},
                    },
                },
            },
        },
    }
