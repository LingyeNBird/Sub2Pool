"""Assembly of the external read-only OpenAPI document."""

from __future__ import annotations

from .accounts import account_schemas
from .common import _nullable, common_schemas, error_responses, security_schemes
from .observations import observation_schemas
from .participants import participant_schemas
from .paths import openapi_paths
from .statistics import statistics_schemas


def openapi_document() -> dict:
    nullable_number = _nullable("number")
    nullable_string = _nullable("string")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sub2Pool Read-only API",
            "version": "1.3.0",
            "description": (
                "使用永久只读 API Key 获取账号、额度、参与者、观测、模型和通知数据。"
                "所有端点只允许 GET、HEAD 和 OPTIONS。"
            ),
        },
        "servers": [{"url": "/api"}],
        "security": [{"ReadOnlyApiKey": []}],
        "paths": openapi_paths(),
        "components": {
            "securitySchemes": security_schemes(),
            "responses": error_responses(),
            "schemas": {
                **common_schemas(),
                **account_schemas(nullable_number, nullable_string),
                **participant_schemas(nullable_number, nullable_string),
                **observation_schemas(nullable_number, nullable_string),
                **statistics_schemas(nullable_number, nullable_string),
            },
        },
    }
