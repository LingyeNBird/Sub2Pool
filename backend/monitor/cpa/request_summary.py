"""Request-list totals computed from the already authorized, filtered queryset."""

from decimal import Decimal

from .usage import cpa_event_cost


def request_summary(events, config):
    result = dict.fromkeys(
        [
            "request_count",
            "failed_count",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "unpriced_request_count",
        ],
        0,
    )
    cost = Decimal(0)
    latency_sum = ttft_sum = latency_count = ttft_count = 0
    fields = [
        "model",
        "requested_service_tier",
        "response_service_tier",
        "failed",
        "latency_ms",
        "ttft_ms",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    ]
    for event in events.order_by().only(*fields).iterator(chunk_size=2000):
        amount, unknown = cpa_event_cost(event, config)
        cost += amount
        result["request_count"] += 1
        result["failed_count"] += int(event.failed)
        result["unpriced_request_count"] += int(unknown)
        for field in fields[-5:]:
            result[field] += getattr(event, field)
        if event.latency_ms > 0:
            latency_sum += event.latency_ms
            latency_count += 1
        if event.ttft_ms > 0:
            ttft_sum += event.ttft_ms
            ttft_count += 1
    result.update(
        usage_usd=float(cost),
        average_latency_ms=latency_sum / latency_count if latency_count else None,
        average_ttft_ms=ttft_sum / ttft_count if ttft_count else None,
    )
    return result
