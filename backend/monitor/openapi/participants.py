"""OpenAPI schemas for participants and aggregate recommendations."""

from __future__ import annotations


def participant_schemas(nullable_number: dict, nullable_string: dict) -> dict:
    return {
        "ParticipantPoolAllocation": {
            "type": "object",
            "required": [
                "pool_id",
                "pool_name",
                "share_percent",
                "account_ids",
            ],
            "properties": {
                "pool_id": {"type": "integer"},
                "pool_name": {"type": "string"},
                "share_percent": {"type": "number"},
                "account_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
        },
        "Participant": {
            "type": "object",
            "required": [
                "id",
                "name",
                "email",
                "sub2api_user_id",
                "sub2api_username",
                "sub2api_email",
                "sub2api_identity",
                "pool_allocations",
                "is_owner",
                "enabled",
                "notes",
                "latest_balance_usd",
                "last_checked_at",
                "account_breakdowns",
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
                "pool_allocations": {
                    "type": "array",
                    "items": {
                        "$ref": "#/components/schemas/ParticipantPoolAllocation"
                    },
                },
                "is_owner": {"type": "boolean"},
                "enabled": {"type": "boolean"},
                "notes": {"type": "string"},
                "latest_balance_usd": nullable_number,
                "last_checked_at": nullable_string,
                "account_breakdowns": {
                    "type": "array",
                    "items": {
                        "$ref": "#/components/schemas/AccountBreakdown"
                    },
                },
                "snapshot": {
                    "oneOf": [
                        {
                            "$ref": "#/components/schemas/AggregateRecommendation"
                        },
                        {"type": "null"},
                    ]
                },
            },
        },
        "ParticipantSnapshot": {
            "type": "object",
            "required": [
                "participant_id",
                "participant_name",
                "quota_pool_id",
                "quota_pool_name",
                "pool_contract_revision",
                "share_percent",
                "selected_cost",
                "delta_cost",
                "charged_delta_percent",
                "charged_cycle_percent",
                "charged_percent_lower",
                "charged_percent_upper",
                "remaining_share_percent",
                "current_balance_usd",
                "recommended_balance_usd",
                "recommended_balance_min_usd",
                "recommended_balance_max_usd",
                "deterministic_balance_min_usd",
                "deterministic_balance_max_usd",
                "balance_difference_usd",
                "is_overused",
                "overused_percent",
                "overused_percent_min",
                "overused_percent_max",
                "needs_manual_update",
                "recommendation_applied",
                "reason",
                "allocation_model",
            ],
            "properties": {
                "participant_id": {"type": "integer"},
                "participant_name": {"type": "string"},
                "quota_pool_id": {
                    "type": ["integer", "null"]
                },
                "quota_pool_name": {"type": "string"},
                "pool_contract_revision": {
                    "type": ["integer", "null"]
                },
                "share_percent": {"type": "number"},
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
                "allocation_model": {
                    "type": "string",
                    "enum": ["time_varying", "constant_average"],
                },
            },
        },
        "AccountBreakdown": {
            "type": "object",
            "required": [
                "id",
                "account_id",
                "external_account_id",
                "account_name",
                "account_enabled",
                "pool_id",
                "pool_name",
                "contract_share_percent",
                "allocated",
                "snapshot",
                "latest_selected_cost",
                "last_checked_at",
            ],
            "properties": {
                "id": {
                    "oneOf": [
                        {"type": "integer"},
                        {"type": "null"},
                    ]
                },
                "account_id": {"type": "integer"},
                "external_account_id": {"type": "integer"},
                "account_name": {"type": "string"},
                "account_enabled": {"type": "boolean"},
                "pool_id": {"type": "integer"},
                "pool_name": {"type": "string"},
                "contract_share_percent": {"type": "number"},
                "allocated": {"type": "boolean"},
                "latest_selected_cost": nullable_number,
                "last_checked_at": nullable_string,
                "snapshot": {
                    "oneOf": [
                        {
                            "$ref": "#/components/schemas/ParticipantSnapshot"
                        },
                        {"type": "null"},
                    ]
                },
            },
        },
        "AggregateRecommendationSource": {
            "type": "object",
            "required": [
                "account_id",
                "external_account_id",
                "account_name",
                "pool_id",
                "pool_name",
                "pool_contract_revision",
                "contract_share_percent",
                "snapshot",
                "net_position_usd",
                "net_position_min_usd",
                "net_position_max_usd",
                "contribution_usd",
                "contribution_min_usd",
                "contribution_max_usd",
                "estimated_capacity_usd",
                "expected_entitlement_usd",
                "consumed_entitlement_usd",
                "remaining_entitlement_usd",
                "entitlement_usage_percent",
            ],
            "properties": {
                "account_id": {"type": "integer"},
                "external_account_id": {"type": "integer"},
                "account_name": {"type": "string"},
                "pool_id": {"type": "integer"},
                "pool_name": {"type": "string"},
                "pool_contract_revision": {"type": "integer"},
                "contract_share_percent": {"type": "number"},
                "net_position_usd": nullable_number,
                "net_position_min_usd": nullable_number,
                "net_position_max_usd": nullable_number,
                "estimated_capacity_usd": nullable_number,
                "expected_entitlement_usd": nullable_number,
                "consumed_entitlement_usd": nullable_number,
                "remaining_entitlement_usd": nullable_number,
                "entitlement_usage_percent": nullable_number,
                "snapshot": {
                    "oneOf": [
                        {
                            "$ref": "#/components/schemas/ParticipantSnapshot"
                        },
                        {"type": "null"},
                    ]
                },
                "contribution_usd": nullable_number,
                "contribution_min_usd": nullable_number,
                "contribution_max_usd": nullable_number,
            },
        },
        "AggregateRecommendation": {
            "type": "object",
            "required": [
                "participant_id",
                "participant_name",
                "pool_allocations",
                "selected_cost",
                "charged_cycle_percent",
                "expected_entitlement_usd",
                "consumed_entitlement_usd",
                "remaining_entitlement_usd",
                "entitlement_usage_percent",
                "current_balance_usd",
                "recommended_balance_usd",
                "recommended_balance_min_usd",
                "recommended_balance_max_usd",
                "balance_difference_usd",
                "is_overused",
                "needs_manual_update",
                "recommendation_applied",
                "recommendation_complete",
                "account_count",
                "pool_count",
                "reason",
                "allocation_model",
                "sources",
            ],
            "properties": {
                "participant_id": {"type": "integer"},
                "participant_name": {"type": "string"},
                "pool_allocations": {
                    "type": "array",
                    "items": {
                        "$ref": "#/components/schemas/ParticipantPoolAllocation"
                    },
                },
                "selected_cost": {"type": "number"},
                "charged_cycle_percent": {"type": "number"},
                "expected_entitlement_usd": nullable_number,
                "consumed_entitlement_usd": nullable_number,
                "remaining_entitlement_usd": nullable_number,
                "entitlement_usage_percent": nullable_number,
                "current_balance_usd": nullable_number,
                "recommended_balance_usd": nullable_number,
                "recommended_balance_min_usd": nullable_number,
                "recommended_balance_max_usd": nullable_number,
                "balance_difference_usd": nullable_number,
                "is_overused": {"type": "boolean"},
                "needs_manual_update": {"type": "boolean"},
                "recommendation_applied": {"type": "boolean"},
                "recommendation_complete": {"type": "boolean"},
                "account_count": {"type": "integer"},
                "pool_count": {"type": "integer"},
                "reason": {"type": "string"},
                "allocation_model": {
                    "type": "string",
                    "const": "partitioned_pool_sum",
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "$ref": "#/components/schemas/AggregateRecommendationSource"
                    },
                },
            },
        },
    }
