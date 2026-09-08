"""CPA carpool read API schemas and query contract."""

from .common import _account_id_parameter, _success_response


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties)}


def _array(schema):
    return {"type": "array", "items": schema}


def cpa_schemas():
    number = {"type": "number"}
    integer = {"type": "integer"}
    text = {"type": "string"}
    boolean = {"type": "boolean"}
    nullable_number = {"type": ["number", "null"]}
    nullable_time = {"type": ["string", "null"], "format": "date-time"}
    time = {"type": "string", "format": "date-time"}
    totals = {
        "usage_usd": number,
        "request_count": integer,
        "token_count": integer,
        "unpriced_request_count": integer,
    }
    coverage = _object(
        {
            "complete": boolean,
            "uncertain_end": boolean,
            "gaps": _array(_object({"started_at": time, "ended_at": time})),
        }
    )
    breakdown = _object(
        {
            "account_id": integer,
            "quota_available": boolean,
            "quota_unavailable_reasons": _array(text),
            "quota_as_of": nullable_time,
            "charged_percent": nullable_number,
            "remaining_share_percent": nullable_number,
            "usage_usd": number,
            "estimated_capacity_usd": nullable_number,
            "expected_entitlement_usd": nullable_number,
            "consumed_entitlement_usd": nullable_number,
            "remaining_entitlement_usd": nullable_number,
        }
    )
    return {
        "CPAPoolSummary": _object(
            {
                "pool_id": integer,
                "pool_name": text,
                "selected_account_id": integer,
                "partial_scope": boolean,
                "cost_estimate": boolean,
                "enforcement_enabled": boolean,
                "generated_at": time,
                "accounts": _array(
                    _object(
                        {
                            **totals,
                            "account_id": integer,
                            "account_name": text,
                            "owner": _object(
                                {
                                    "participant_id": {"type": ["integer", "null"]},
                                    "participant_name": {"type": ["string", "null"]},
                                    "started_at": nullable_time,
                                    "status": {
                                        "type": "string",
                                        "enum": ["active", "missing", "ambiguous"],
                                    },
                                }
                            ),
                            "selected": boolean,
                            "quota_as_of": nullable_time,
                            "requests_as_of": nullable_time,
                            "cycle_started_at": time,
                            "resets_at": nullable_time,
                            "coverage": coverage,
                            "quota_available": boolean,
                            "quota_unavailable_reasons": _array(text),
                        }
                    )
                ),
                "members": _array(
                    _object(
                        {
                            **totals,
                            "participant_id": integer,
                            "participant_name": text,
                            "is_self": boolean,
                            "is_owner": boolean,
                            "share_percent": nullable_number,
                            "quota_available": boolean,
                            "is_overused": boolean,
                            "expected_entitlement_usd": nullable_number,
                            "consumed_entitlement_usd": nullable_number,
                            "remaining_entitlement_usd": nullable_number,
                            "account_breakdowns": _array(breakdown),
                        }
                    )
                ),
                "unattributed": _object(totals),
                "collector": {
                    "type": "object",
                    "description": "采集器状态；普通用户不返回 last_error。",
                },
            }
        ),
        "CPARequest": _object(
            {
                "id": integer,
                "occurred_at": time,
                "request_id": text,
                "api_key_hint": text,
                "model": text,
                "endpoint": text,
                "input_tokens": integer,
                "cached_input_tokens": integer,
                "output_tokens": integer,
                "reasoning_tokens": integer,
                "total_tokens": integer,
                "failed": boolean,
                "latency_ms": integer,
                "ttft_ms": integer,
                "usage_usd": number,
                "unpriced": boolean,
                "requested_service_tier": text,
                "response_service_tier": text,
            }
        ),
        "CPARequests": _object(
            {
                "account_id": integer,
                "items": _array({"$ref": "#/components/schemas/CPARequest"}),
                "total": integer,
                "started_at": time,
                "ended_at": time,
                "summary": {
                    "oneOf": [
                        _object(
                            {
                                **{
                                    name: integer
                                    for name in (
                                        "request_count",
                                        "failed_count",
                                        "input_tokens",
                                        "cached_input_tokens",
                                        "output_tokens",
                                        "reasoning_tokens",
                                        "total_tokens",
                                        "unpriced_request_count",
                                    )
                                },
                                "usage_usd": number,
                                "average_latency_ms": {"type": ["number", "null"]},
                                "average_ttft_ms": {"type": ["number", "null"]},
                            }
                        ),
                        {"type": "null"},
                    ],
                    "description": "include_summary=true 时返回整个授权筛选范围的汇总，不受分页限制；缺失耗时不计入均值。",
                },
                "page": integer,
                "page_size": integer,
                "cost_estimate": boolean,
                "generated_at": time,
                "keys": _array(_object({"id": integer, "name": text, "hint": text})),
                "models": _array(text),
            }
        ),
    }


def cpa_paths():
    def parameter(name, schema):
        return {"name": name, "in": "query", "required": False, "schema": schema}

    result = {}
    for suffix, schema, label in (
        ("summary", "CPAPoolSummary", "读取 CPA 同车成员汇总"),
        ("requests", "CPARequests", "分页读取本人 CPA 请求"),
    ):
        params = [_account_id_parameter()]
        params[0]["required"] = True
        if suffix == "requests":
            params += [
                parameter(name, spec)
                for name, spec in (
                    ("participant_id", {"type": "integer", "minimum": 1}),
                    ("key_id", {"type": "integer", "minimum": 1}),
                    ("model", {"type": "string"}),
                    ("include_summary", {"type": "boolean", "default": False}),
                    (
                        "days",
                        {"type": "integer", "minimum": 1, "maximum": 90, "default": 7},
                    ),
                    ("failed", {"type": "boolean"}),
                    ("started_at", {"type": "string", "format": "date-time"}),
                    ("ended_at", {"type": "string", "format": "date-time"}),
                    ("page", {"type": "integer", "minimum": 1, "default": 1}),
                    (
                        "page_size",
                        {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 50,
                        },
                    ),
                )
            ]
        result[f"/v1/cpa/{suffix}"] = {
            "get": {
                "summary": label,
                "operationId": f"get{schema}",
                "description": "按账号授权与 CPA 池成员关系限制访问。请求和 Key 分项仅本人授权参与者及管理员可读；时间范围左闭右开，默认最近 7 天，最多 90 天。",
                "parameters": params,
                "responses": {
                    "200": _success_response(
                        label, {"$ref": f"#/components/schemas/{schema}"}
                    ),
                    **{
                        str(code): {"$ref": f"#/components/responses/{name}"}
                        for code, name in ((400, "BadRequest"), (401, "Unauthorized"))
                    },
                },
            }
        }
    return result
