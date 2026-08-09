"""API 查询参数的边界解析。"""


def bounded_query_int(request, name: str, default: int, maximum: int) -> int:
    try:
        return min(max(int(request.query_params.get(name, default)), 1), maximum)
    except (TypeError, ValueError):
        return default
