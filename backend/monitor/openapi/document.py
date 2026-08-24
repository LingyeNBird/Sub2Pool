"""Assembly of the external OpenAPI document."""

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
            "title": "Sub2Pool API",
            "version": "1.4.0",
            "description": (
                "使用管理员在系统设置中生成的永久 API Key 调用全部已开放接口，"
                "包括读取业务数据和一键设置当前建议余额。"
            ),
        },
        "servers": [{"url": "/api"}],
        "security": [{"ApiKey": []}],
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
