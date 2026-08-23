"""OpenAPI schemas for reporting, notifications, and API-key usage."""

from __future__ import annotations

from .common import _nullable


def statistics_schemas(nullable_number: dict, nullable_string: dict) -> dict:
    return {
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
    }
