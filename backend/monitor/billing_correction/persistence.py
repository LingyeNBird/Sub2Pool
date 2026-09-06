"""Persist complete, immutable request intervals in the caller's fact transaction."""

from decimal import Decimal
from django.db import transaction

from ..models import BillingUsageFact, ObservationBillingCapture

from .facts import raw_fact_values, validate_interval_logs


@transaction.atomic
def persist_capture(observation, interval) -> None:
    logs = interval.logs
    if logs is None:
        return  # Old import/test intervals contain only frozen FAST summaries.
    if len(logs) != interval.request_count or len({log.id for log in logs}) != len(logs):
        raise ValueError("修正原始请求事实数量不一致或包含重复请求")
    # A complete interval cannot be silently replaced by a subsequent query.
    if ObservationBillingCapture.objects.filter(observation=observation).exists():
        raise ValueError("此观测已有完整的修正原始事实，禁止覆盖")
    validate_interval_logs(logs, account_id=observation.account_id, started_at=interval.started_at, ended_at=interval.ended_at)
    rows = [BillingUsageFact(source_log_id=log.id, **raw_fact_values(log)) for log in logs]
    capture = ObservationBillingCapture.objects.create(
        observation=observation, started_at=interval.started_at,
        ended_at=interval.ended_at, request_count=len(logs),
    )
    for row in rows:
        row.capture = capture
    BillingUsageFact.objects.bulk_create(rows, batch_size=500)
    # No separate upstream calls and no research facts when consent is off.
    from ..research.capture import capture_components
    capture_components(capture, logs)


@transaction.atomic
def persist_api_usage_facts(account_id, logs) -> None:
    from ..models import APIUsageRequestFact

    for offset in range(0, len(logs), 400):
        batch = logs[offset:offset + 400]
        expected = {log.id: raw_fact_values(log) for log in batch}
        APIUsageRequestFact.objects.bulk_create([
            APIUsageRequestFact(account_id=account_id, source_log_id=log.id, **expected[log.id]) for log in batch
        ], ignore_conflicts=True, batch_size=400)
        stored = list(APIUsageRequestFact.objects.filter(account_id=account_id, source_log_id__in=expected))
        if len(stored) != len(expected):
            raise ValueError("API 请求原始事实写入不完整")
        for fact in stored:
            for name, value in expected[fact.source_log_id].items():
                if name == "api_key_name":
                    continue  # Names can change; current names live in key metadata.
                old = getattr(fact, name)
                if name in {"total_cost", "actual_cost"}:
                    old, value = Decimal(old), Decimal(value)
                if old != value:
                    raise ValueError(f"上游请求 {fact.source_log_id} 的原始字段 {name} 与已保存事实冲突；未覆盖")
