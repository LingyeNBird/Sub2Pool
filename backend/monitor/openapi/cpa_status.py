"""CPA account-window presentation, scoped summary metadata, and reset history."""

from .cpa import _object, _array


def cpa_status_schema():
    number = {"type": "number"}
    nullable_number = {"type": ["number", "null"]}
    text = {"type": "string"}
    nullable_text = {"type": ["string", "null"]}
    boolean = {"type": "boolean"}
    metrics = {
        "type": "object",
        "properties": {
            "request_count": number,
            "token_count": number,
            "usage_usd": number,
            "success_rate": nullable_number,
            "unpriced_request_count": {"type": "integer"},
        },
    }
    period = _object(
        {
            "started_at": text,
            "ended_at": text,
            "metrics": metrics,
            "coverage_complete": boolean,
        }
    )
    optional = lambda schema: {"oneOf": [schema, {"type": "null"}]}
    return _object(
        {
            "totals": period,
            "windows": _array(
                _object(
                    {
                        "id": text,
                        "label": text,
                        "used_percent": nullable_number,
                        "reset_at": nullable_text,
                        "updated_at": nullable_text,
                        "source": text,
                        "boundary": {"type": "string", "enum": ["unknown", "provider"]},
                        "current": optional(period),
                        "previous": optional(period),
                        "prediction": optional(metrics),
                        "notice": nullable_text,
                    }
                )
            ),
            "reset": _object(
                {
                    "available_count": {"type": ["integer", "null"]},
                    "can_reset": boolean,
                    "history": _array(
                        _object(
                            {
                                "id": text,
                                "status": text,
                                "created_at": text,
                                "finished_at": nullable_text,
                            }
                        )
                    ),
                }
            ),
        }
    )
