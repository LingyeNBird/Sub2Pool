"""API 查询参数的边界解析。"""


from ..models import MonitoredAccount


def monitored_account_query(
    request,
    *,
    enabled_only: bool = False,
) -> MonitoredAccount | None:
    """Resolve an internal monitored-account id, defaulting to the first enabled."""
    queryset = MonitoredAccount.objects.order_by("name", "external_account_id")
    raw = request.query_params.get("account_id")
    if raw is None or raw == "":
        return queryset.filter(enabled=True).first()
    try:
        account_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("监控账号参数无效") from exc
    if account_id <= 0:
        raise ValueError("监控账号参数无效")
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    account = queryset.filter(pk=account_id).first()
    if account is None:
        raise ValueError("监控账号不存在或已停用")
    return account


def bounded_query_int(request, name: str, default: int, maximum: int) -> int:
    try:
        return min(max(int(request.query_params.get(name, default)), 1), maximum)
    except (TypeError, ValueError):
        return default
