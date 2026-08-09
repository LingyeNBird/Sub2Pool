"""报告 JSON 的通用格式化工具。"""


def iso(value):
    return value.isoformat() if value else None
