"""Sub2API v0.2.1 group-pricing administration resources."""
from typing import Any
from urllib.parse import urljoin

import httpx

from .dto import Sub2APIError


OPENAI_PLATFORM = "openai"


def _positive_id(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}") from exc
    if parsed <= 0:
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}")
    return parsed


def _id_list(value: Any, field: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise Sub2APIError(f"Sub2API 返回了无效字段 {field}")
    result: list[int] = []
    for index, item in enumerate(value):
        parsed = _positive_id(item, f"{field}[{index}]")
        if parsed not in result:
            result.append(parsed)
    return result


class PricingResourceMixin:
    """Minimal safe facade for account/group/channel pricing migration."""

    def selectable_pricing_groups(self):
        data = self._get("api/v1/admin/groups/all", params={"platform": "openai"})
        if not isinstance(data, list):
            raise Sub2APIError("Sub2API 分组列表响应结构错误")
        groups = []
        for item in data:
            if not isinstance(item, dict):
                raise Sub2APIError("Sub2API 分组列表响应结构错误")
            if item.get("platform") == OPENAI_PLATFORM:
                groups.append({"id": _positive_id(item.get("id"), "groups.id"), "name": str(item.get("name") or item["id"])})
        return groups

    def account_group_ids(self, account_id: int) -> list[int]:
        data = self._get(f"api/v1/admin/accounts/{account_id}")
        if not isinstance(data, dict):
            raise Sub2APIError("OpenAI 账号详情响应结构错误")
        returned_id = _positive_id(data.get("id"), "id")
        if returned_id != account_id:
            raise Sub2APIError("Sub2API 返回了不匹配的账号")
        if data.get("platform") != OPENAI_PLATFORM:
            raise Sub2APIError("配置的账号不是 OpenAI 账号")
        return _id_list(data.get("group_ids"), "group_ids")

    def group_pricing(self, group_id: int) -> dict[str, Any]:
        data = self._get(f"api/v1/admin/groups/{group_id}")
        if not isinstance(data, dict):
            raise Sub2APIError(f"分组 {group_id} 的详情响应结构错误")
        returned_id = _positive_id(data.get("id"), "id")
        if returned_id != group_id:
            raise Sub2APIError(f"分组 {group_id} 的详情 ID 不匹配")
        pricing = data.get("model_pricing")
        if pricing is not None and not isinstance(pricing, list):
            raise Sub2APIError(f"分组 {group_id} 的 model_pricing 无效")
        for item in pricing or []:
            if not isinstance(item, dict):
                raise Sub2APIError(f"分组 {group_id} 的 model_pricing 无效")
        return data

    def list_active_pricing_channels(self) -> list[dict[str, Any]]:
        channels: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._get(
                "api/v1/admin/channels",
                params={"status": "active", "page": page, "page_size": 100},
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise Sub2APIError("Sub2API 渠道列表响应结构错误")
            for item in data["items"]:
                if not isinstance(item, dict):
                    raise Sub2APIError("Sub2API 渠道列表响应结构错误")
                channel_id = _positive_id(item.get("id"), "channels.id")
                channels.append(
                    {
                        **item,
                        "id": channel_id,
                        "group_ids": _id_list(
                            item.get("group_ids"),
                            f"channels[{channel_id}].group_ids",
                        ),
                    }
                )
            try:
                pages = max(1, int(data.get("pages") or 1))
            except (TypeError, ValueError) as exc:
                raise Sub2APIError("Sub2API 渠道列表分页字段无效") from exc
            if page >= pages:
                break
            page += 1
            if page > 100:
                raise Sub2APIError("Sub2API 渠道数量异常，已停止读取")
        return channels

    def channel_pricing(self, channel_id: int) -> dict[str, Any]:
        data = self._get(f"api/v1/admin/channels/{channel_id}")
        if not isinstance(data, dict):
            raise Sub2APIError(f"渠道 {channel_id} 的详情响应结构错误")
        returned_id = _positive_id(data.get("id"), "id")
        if returned_id != channel_id:
            raise Sub2APIError(f"渠道 {channel_id} 的详情 ID 不匹配")
        pricing = data.get("model_pricing")
        if pricing is not None and not isinstance(pricing, list):
            raise Sub2APIError(f"渠道 {channel_id} 的 model_pricing 无效")
        for item in pricing or []:
            if not isinstance(item, dict):
                raise Sub2APIError(f"渠道 {channel_id} 的 model_pricing 无效")
        return data

    def catalog_models(self, platform: str = OPENAI_PLATFORM) -> list[str]:
        data = self._get(
            "api/v1/admin/channels/pricing/sync-models",
            params={"platform": platform},
        )
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise Sub2APIError("Sub2API 模型目录响应结构错误")
        result: list[str] = []
        seen: set[str] = set()
        for item in models:
            if not isinstance(item, str) or not item.strip():
                raise Sub2APIError("Sub2API 模型目录包含无效模型名")
            name = item.strip()
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                result.append(name)
        return result

    def catalog_model_pricing(self, model: str) -> dict[str, Any] | None:
        data = self._get(
            "api/v1/admin/channels/model-pricing",
            params={"model": model},
        )
        if not isinstance(data, dict) or not isinstance(data.get("found"), bool):
            raise Sub2APIError(f"模型 {model} 的目录价格响应结构错误")
        if not data["found"]:
            return None
        return data

    def update_group_pricing(
        self,
        group_id: int,
        managed_fields: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "model_pricing",
            "long_context_pricing_enabled",
            "free_openai_fast",
        }
        if not managed_fields or not set(managed_fields).issubset(allowed):
            raise ValueError("分组价格更新包含无效或空的托管字段")
        url = urljoin(self.base_url, f"api/v1/admin/groups/{group_id}")
        try:
            response = self.client.put(url, json=managed_fields)
        except httpx.HTTPError as exc:
            raise Sub2APIError(
                f"无法连接 Sub2API：{exc.__class__.__name__}"
            ) from exc
        data = self._response_data(response)
        if not isinstance(data, dict):
            raise Sub2APIError(f"分组 {group_id} 的更新响应结构错误")
        returned_id = _positive_id(data.get("id"), "id")
        if returned_id != group_id:
            raise Sub2APIError(f"分组 {group_id} 的更新响应 ID 不匹配")
        return data
