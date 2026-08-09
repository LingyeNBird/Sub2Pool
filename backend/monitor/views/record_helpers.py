"""记录类 API 共用的查询参数与分页辅助函数。"""

from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .query_params import bounded_query_int


def query_datetime(request, name: str):
    """读取 ISO 日期时间查询参数；无时区值按 Django 当前时区解释。"""
    value = request.query_params.get(name)
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"{name} 不是有效的日期时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def paginated_rows(request, queryset, default_size: int = 20) -> tuple[list, dict]:
    """统一记录接口的分页结构，防止审计表随数据库增长而一次性加载。"""
    page_size = bounded_query_int(request, "page_size", default_size, 100)
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.query_params.get("page", 1))
    return list(page.object_list), {
        "page": page.number,
        "page_size": page_size,
        "total": paginator.count,
        "total_pages": paginator.num_pages,
    }
