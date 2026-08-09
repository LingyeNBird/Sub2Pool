"""Sub2API user discovery, balance reads, and the dedicated recommendation write."""
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from .dto import Sub2APIError, UserBalance, _decimal


class UserResourceMixin:
    def list_users(self) -> list[dict[str, Any]]:
        """分页读取可作为拼车参与者的 Sub2API 用户，只返回下拉框所需字段。"""
        users: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "api/v1/admin/users",
                params={
                    "page": page,
                    "page_size": 100,
                    "sort_by": "email",
                    "sort_order": "asc",
                    "include_subscriptions": "false",
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise Sub2APIError("Sub2API 用户列表响应结构错误")

            for raw in data["items"]:
                if not isinstance(raw, dict):
                    continue
                try:
                    user_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                if user_id <= 0:
                    continue
                users.append(
                    {
                        "id": user_id,
                        "email": str(raw.get("email") or ""),
                        "username": str(raw.get("username") or ""),
                        "status": str(raw.get("status") or ""),
                        "role": str(raw.get("role") or ""),
                    }
                )

            try:
                pages = max(1, int(data.get("pages") or 1))
            except (TypeError, ValueError):
                raise Sub2APIError("Sub2API 用户列表分页字段无效")
            if page >= pages:
                break
            page += 1
            if page > 100:
                raise Sub2APIError("Sub2API 用户数量异常，已停止读取")
        return users

    def user_balance(self, user_id: int) -> UserBalance:
        """读取用户全局余额；只调用详情 GET 接口，不会修改余额。"""
        data = self._get(f"api/v1/admin/users/{user_id}")
        if not isinstance(data, dict):
            raise Sub2APIError(f"用户 {user_id} 的详情响应结构错误")
        try:
            returned_id = int(data.get("id"))
        except (TypeError, ValueError) as exc:
            raise Sub2APIError(f"用户 {user_id} 的详情缺少有效 ID") from exc
        if returned_id != user_id:
            raise Sub2APIError(f"用户 {user_id} 的详情 ID 不匹配")
        return UserBalance(
            balance=_decimal(data.get("balance"), "balance"),
            frozen_balance=_decimal(data.get("frozen_balance"), "frozen_balance"),
        )

    def set_user_balance_from_recommendation(
        self,
        user_id: int,
        balance: Decimal,
    ) -> Decimal:
        """把用户余额设为建议值；不会修改并发、分组、订阅或任何其他配置。"""
        if balance <= 0:
            raise Sub2APIError(
                "Sub2API 原生余额调整接口只接受大于 0 的余额，请前往管理后台手动处理"
            )
        url = urljoin(
            self.base_url,
            f"api/v1/admin/users/{user_id}/balance",
        )
        try:
            response = self.client.post(
                url,
                json={
                    "balance": float(balance),
                    "operation": "set",
                    "notes": "Sub2Pool 一键应用额度建议",
                },
                headers={"Idempotency-Key": uuid4().hex},
            )
        except httpx.HTTPError as exc:
            raise Sub2APIError(
                f"无法连接 Sub2API：{exc.__class__.__name__}"
            ) from exc
        data = self._response_data(response)
        if not isinstance(data, dict):
            raise Sub2APIError("Sub2API 用户余额更新响应结构错误")
        try:
            returned_id = int(data.get("id"))
        except (TypeError, ValueError) as exc:
            raise Sub2APIError("Sub2API 用户余额更新响应缺少有效 ID") from exc
        if returned_id != user_id:
            raise Sub2APIError("Sub2API 用户余额更新响应 ID 不匹配")
        confirmed = _decimal(data.get("balance"), "balance")
        if abs(confirmed - balance) > Decimal("0.0001"):
            raise Sub2APIError("Sub2API 返回的用户余额与建议值不一致")
        return confirmed
