"""Validation/serialization of primary request facts; never stores credentials."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

RAW_FIELDS = (
    "user_id", "created_at", "model", "service_tier", "input_tokens",
    "cache_creation_tokens", "cache_read_tokens", "long_context_billing_applied",
    "api_key_id", "api_key_name",
)


def raw_fact_values(log) -> dict:
    result = {name: getattr(log, name) for name in RAW_FIELDS}
    for name, maximum in (("model", 255), ("service_tier", 32), ("api_key_name", 255)):
        if not isinstance(result[name], str) or len(result[name]) > maximum:
            raise ValueError(f"请求原始字段 {name} 格式无效或过长")
    for name, minimum in (("user_id", 1), ("api_key_id", 0)):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value < 2**63:
            raise ValueError(f"请求原始字段 {name} 无效")
    for name in ("input_tokens", "cache_creation_tokens", "cache_read_tokens"):
        value = result[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63):
            raise ValueError(f"请求原始字段 {name} 无效")
    flag = result["long_context_billing_applied"]
    if flag is not None and not isinstance(flag, bool):
        raise ValueError("请求原始字段 long_context_billing_applied 无效")
    at = result["created_at"]
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError("请求原始时间必须包含时区")
    for name in ("total_cost", "actual_cost"):
        try:
            value = Decimal(str(getattr(log, name)))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("无效的修正请求原始成本") from exc
        if not value.is_finite() or value < 0 or value > Decimal("1e12") or len(str(value)) > 96:
            raise ValueError("无效的修正请求原始成本")
        result[name] = str(value)
    return result


def validate_interval_logs(logs, *, account_id, started_at, ended_at, user_id=None) -> None:
    ids = set()
    for log in logs:
        if isinstance(log.id, bool) or not isinstance(log.id, int) or not 0 < log.id < 2**63 or log.id in ids:
            raise ValueError("请求日志 ID 无效或重复")
        ids.add(log.id)
        raw_fact_values(log)
        if log.account_id != account_id or not started_at <= log.created_at < ended_at:
            raise ValueError("请求事实不属于此账号或半开采样区间")
        if user_id is not None and log.user_id != user_id:
            raise ValueError("请求事实不属于指定用户")


def validate_capture(capture, observation, facts) -> None:
    if capture.schema_version != 1:
        raise ValueError("无法识别的修正原始事实版本")
    if capture.ended_at != observation.observed_at or capture.started_at > capture.ended_at:
        raise ValueError("修正原始事实区间与观测时刻不一致")
    if len(facts) != capture.request_count:
        raise ValueError("修正原始事实不完整，请检查数据库备份；不会使用残缺事实计算")
    ids = set()
    for fact in facts:
        if not 0 < fact.source_log_id < 2**63 or fact.source_log_id in ids:
            raise ValueError("修正原始事实的请求 ID 无效或重复")
        ids.add(fact.source_log_id)
        raw_fact_values(fact)
        if not capture.started_at <= fact.created_at < capture.ended_at:
            raise ValueError("修正原始请求不在已保存的半开采样区间内")
