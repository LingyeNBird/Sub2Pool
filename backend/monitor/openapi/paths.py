"""OpenAPI path definitions for the read-only API."""

from __future__ import annotations

from .common import _account_id_parameter, _pagination_parameters, _success_response


def openapi_paths() -> dict:
    return {
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
    }
