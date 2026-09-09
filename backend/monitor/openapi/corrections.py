"""Frozen historical correction amounts and upstream pricing provenance."""


def with_correction_schemas(schemas: dict) -> dict:
    amounts = {
        name: {"type": ["number", "null"]}
        for name in (
            "fast_correction_usd", "long_context_correction_usd",
            "model_correction_usd", "correction_total_usd",
        )
    }
    coverage = {
        "correction_calculated": {
            "type": "boolean",
            "description": "历史 local 区间已有原始请求捕获并按冻结规则计算；新 upstream/none 记录不执行本地修正，须结合 correction_source 判断。",
        },
        "correction_facts_complete": {"type": "boolean"},
        "legacy_fast_only": {"type": "boolean"},
        "missing_correction_intervals": {"type": "integer", "minimum": 0},
        "unknown_long_context_request_count": {"type": "integer", "minimum": 0},
        "missing_model_request_count": {"type": "integer", "minimum": 0},
    }
    schemas["CorrectionBreakdown"] = {
        "type": "object",
        "description": (
            "冻结历史规则下的有符号修正：FAST → 长上下文 → 模型倍率。"
            "三项之和等于 correction_total_usd；负数代表减少成本。"
            "null 表示未知或不适用，不能作为零。旧区间缺少原始事实时仅保留旧 FAST 金额。"
        ),
        "properties": {**amounts, **coverage},
    }
    for name in (
        "CostBreakdown", "AccountUsageStats", "Observation",
        "FastCorrectionDetail", "FastCorrectionUser", "ParticipantApiUsage", "ApiKeyUsage",
    ):
        properties = schemas[name]["properties"]
        # Preserve the exact type of pre-existing fields for API clients.
        for key, value in {**amounts, **coverage}.items():
            properties.setdefault(key, value)
    for name in ("Observation", "FastCorrectionDetail"):
        schemas[name]["properties"].update({
            "correction_source": {
                "type": "string", "enum": ["local", "upstream", "none"],
                "description": "local 为冻结的历史本地修正；upstream 为本服务已确认上游配置成功；none 不进行本地修正。",
            },
            "pricing_epoch": {"type": "string", "description": "计费边界标识；变化时自动建立新的测算基线。"},
        })
    schemas["AccountUsageStats"]["properties"].update({
        "account_cost_with_correction_usd": {"type": ["number", "null"]},
        "correction_collected_until": {"type": ["string", "null"], "format": "date-time"},
    })
    for name in ("FastCorrectionDetail", "FastCorrectionUser"):
        schemas[name]["properties"].update({
            "raw_cost_usd": {"type": ["number", "null"]},
            "corrected_cost_usd": {"type": ["number", "null"]},
        })
    schemas["FastCorrectionDetail"]["properties"].update({
        "calculation_order": {"type": "array", "items": {"type": "string", "enum": ["fast", "long_context", "model"]}},
        "rules_digest": {"type": "string", "description": "此历史观测冻结的六个修正字段与计算版本的 SHA-256"},
        "rules": {"type": "object", "description": "旧观测冻结的开关、规则、倍率与阈值；以后修改上游策略不会改变它们"},
        "model_details": {"type": "array", "items": {"$ref": "#/components/schemas/CorrectionModelDetail"}},
    })
    detail = schemas["FastCorrectionDetail"]
    historical_required = detail["required"]
    detail["required"] = ["observation_id", "correction_source", "pricing_epoch"]
    detail["properties"]["message"] = {"type": "string"}
    detail["oneOf"] = [
        {"properties": {"correction_source": {"const": "local"}}, "required": historical_required},
        {"properties": {"correction_source": {"enum": ["upstream", "none"]}}, "required": ["message"]},
    ]
    schemas["CorrectionModelDetail"] = {
        "type": "object", "properties": {
            **amounts,
            "model": {"type": "string"}, "service_tier": {"type": "string"},
            "request_count": {"type": "integer"},
            "raw_cost_usd": {"type": "number"}, "corrected_cost_usd": {"type": "number"},
            "fast_factor": {"type": "string"}, "long_context_factor": {"type": "string"},
            "model_factor": {"type": "string"},
            "long_context_evidence": {"type": "string", "enum": ["not_matched", "upstream_flag", "input_tokens_threshold", "missing_input_facts"]},
        },
    }
    schemas["ParticipantApiUsage"]["properties"]["corrections_enabled"] = {"type": "boolean"}
    return schemas
