"""OpenAPI schemas for accounts, dashboards, and account status."""

from __future__ import annotations

from .common import _nullable


def account_schemas(nullable_number: dict, nullable_string: dict) -> dict:
    return {
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
                "account_cost_usd": nullable_number,
                "fast_correction_usd": nullable_number,
                "account_cost_with_fast_correction_usd": nullable_number,
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
        "MonitoredAccountSummary": {
            "type": "object",
            "required": ["id", "external_account_id", "name"],
            "properties": {
                "id": {"type": "integer"},
                "external_account_id": {"type": "integer"},
                "name": {"type": "string"},
            },
        },
    }
