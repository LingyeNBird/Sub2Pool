"""Shared OpenAPI builders for responses, parameters, and schemas."""

from __future__ import annotations


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


def security_schemes() -> dict:
    return {
        "ReadOnlyApiKey": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Sub2Pool API Key",
            "description": (
                "系统设置中生成的永久只读 Key，格式为 sub2pool_...。"
            ),
        }
    }


def error_responses() -> dict:
    return {
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
    }


def common_schemas() -> dict:
    return {
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
    }
