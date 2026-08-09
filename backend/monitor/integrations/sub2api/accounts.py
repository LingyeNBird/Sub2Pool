"""OpenAI upstream account discovery resource."""
from typing import Any

from .dto import Sub2APIError

OPENAI_PLATFORM = "openai"


class AccountResourceMixin:
    def list_openai_accounts(self) -> list[dict[str, Any]]:
        """分页读取 Sub2API 中的 OpenAI 上游账号，只返回下拉框所需的非敏感字段。"""
        accounts: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "api/v1/admin/accounts",
                params={
                    "page": page,
                    "page_size": 100,
                    "platform": OPENAI_PLATFORM,
                    "sort_by": "name",
                    "sort_order": "asc",
                    "lite": "true",
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise Sub2APIError("OpenAI 账号列表响应结构错误")

            for raw in data["items"]:
                if not isinstance(raw, dict) or raw.get("platform") != OPENAI_PLATFORM:
                    continue
                try:
                    account_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                if account_id <= 0:
                    continue
                accounts.append(
                    {
                        "id": account_id,
                        "name": str(raw.get("name") or f"OpenAI 账号 {account_id}"),
                        "type": str(raw.get("type") or ""),
                        "status": str(raw.get("status") or ""),
                        "schedulable": bool(raw.get("schedulable")),
                    }
                )

            try:
                pages = max(1, int(data.get("pages") or 1))
            except (TypeError, ValueError):
                raise Sub2APIError("OpenAI 账号列表分页字段无效")
            if page >= pages:
                break
            page += 1
            if page > 100:
                raise Sub2APIError("OpenAI 账号数量异常，已停止读取")
        return accounts
