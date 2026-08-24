"""Assembly of the external OpenAPI document."""

from __future__ import annotations

from .accounts import account_schemas
from .common import _nullable, common_schemas, error_responses, security_schemes
from .observations import observation_schemas
from .participants import participant_schemas
from .paths import openapi_paths
from .statistics import statistics_schemas


def openapi_document(
    *,
    endpoint_paths: set[str] | None = None,
    full_access: bool = True,
) -> dict:
    nullable_number = _nullable("number")
    nullable_string = _nullable("string")
    paths = openapi_paths()
    if endpoint_paths is not None:
        visible_paths = {
            "/v1",
            "/v1/openapi.json",
            *(path.removeprefix("/api") for path in endpoint_paths),
        }
        paths = {
            path: definition
            for path, definition in paths.items()
            if path in visible_paths
        }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Sub2Pool API",
            "version": "1.5.1",
            "description": (
                "使用管理员在系统设置中生成的永久 API Key 调用全部已开放接口，"
                "包括读取业务数据和一键设置当前建议余额。"
                if full_access
                else (
                    "普通系统用户无需单独分配“系统设置”权限，即可在系统设置页"
                    "生成个人永久 API Key；该 Key 只可调用其可配置页面权限允许的"
                    "只读接口，账号和参与者数据继续受该用户可见范围限制。"
                )
            ),
        },
        "servers": [{"url": "/api"}],
        "security": [{"ApiKey": []}],
        "paths": paths,
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
