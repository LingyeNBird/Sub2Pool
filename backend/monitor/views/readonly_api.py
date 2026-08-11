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
        "path": "/api/v1/participants",
        "description": "读取参与者页面的表格数据。",
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics",
        "description": "读取额度统计、容量历史和参与者用量序列。",
    },
    {
        "method": "GET",
        "path": "/api/v1/statistics/participants/{participant_id}/api-usage",
        "description": "读取一个参与者在当前周期内的 API Key 用量构成。",
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


def _openapi_document() -> dict:
    nullable_number = _nullable("number")
    nullable_string = _nullable("string")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sub2Pool Read-only API",
            "version": "1.0.0",
            "description": (
                "使用永久只读 API Key 获取参与者和额度统计数据。"
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
            "/v1/statistics": {
                "get": {
                    "summary": "读取额度统计",
                    "operationId": "getStatistics",
                    "parameters": [
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
                        }
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
                "NotFound": {
                    "description": "参与者不存在。",
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
                "Participant": {
                    "type": "object",
                    "required": [
                        "id",
                        "name",
                        "sub2api_user_id",
                        "sub2api_identity",
                        "share_percent",
                        "is_owner",
                        "enabled",
                        "notes",
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
                        "latest_selected_cost": nullable_number,
                        "last_checked_at": nullable_string,
                        "snapshot": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/ParticipantSnapshot"},
                                {"type": "null"},
                            ]
                        },
                    },
                },
                "ParticipantSnapshot": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "charged_cycle_percent",
                        "remaining_share_percent",
                        "needs_manual_update",
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
                        "allocation_model": {"type": "string"},
                    },
                },
                "Statistics": {
                    "type": "object",
                    "required": [
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
                        "capacity_period": {
                            "type": "string",
                            "enum": ["day", "month"],
                        },
                        "capacity_series": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/CapacityPoint"},
                        },
                        "fast_correction_enabled": {"type": "boolean"},
                        "capacity_summary": {"type": "object"},
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
                    ],
                    "properties": {
                        "period": {"type": "string"},
                        "weekly_total_usd": {"type": "number"},
                        "minimum_usd": {"type": "number"},
                        "maximum_usd": {"type": "number"},
                        "sample_count": {"type": "integer"},
                        "basis": {"type": "object"},
                        "daily_total_usd": nullable_number,
                        "daily_basis": {
                            "oneOf": [{"type": "object"}, {"type": "null"}]
                        },
                    },
                    "additionalProperties": True,
                },
                "ParticipantUsageSeries": {
                    "type": "object",
                    "required": [
                        "participant_id",
                        "participant_name",
                        "sub2api_user_id",
                        "points",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
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
                        "api_keys",
                    ],
                    "properties": {
                        "participant_id": {"type": "integer"},
                        "participant_name": {"type": "string"},
                        "sub2api_user_id": {"type": "integer"},
                        "starts_at": {"type": "string", "format": "date-time"},
                        "observed_to": {"type": "string", "format": "date-time"},
                        "cost_basis": {"type": "string"},
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
                        "api_key_id": {"type": "integer"},
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
