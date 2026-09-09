"""Translate target multipliers without changing unrelated upstream prices."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase

PRICE_FIELDS = (
    "input_price", "output_price", "cache_write_price", "cache_write_1h_price",
    "cache_read_price", "image_input_price", "image_output_price", "per_request_price",
)
MANAGED_FIELDS = ("model_pricing", "long_context_pricing_enabled", "free_openai_fast")
RESPONSE_FIELDS = {"id", "channel_id", "created_at", "updated_at"}


def normalize_policy(value):
    if not isinstance(value, dict) or set(value) != {
        "fast_rules", "model_rules", "long_context_pricing_enabled"
    }:
        raise ValueError("上游计费策略字段不完整或包含未知字段")
    result = {}
    for kind in ("fast_rules", "model_rules"):
        rows = value[kind]
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("每类倍率规则最多 100 条")
        result[kind] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"model_pattern", "multiplier"}:
                raise ValueError("倍率规则需包含模型匹配与目标倍率")
            pattern = row["model_pattern"]
            if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 160:
                raise ValueError("模型匹配不能为空，且不能超过 160 字符")
            pattern = pattern.strip().casefold()
            if "*" in pattern[:-1] or "?" in pattern or "[" in pattern:
                raise ValueError("上游模型匹配仅支持模型名或末尾 * 前缀匹配")
            try:
                multiplier = Decimal(str(row["multiplier"]))
            except InvalidOperation as exc:
                raise ValueError("倍率必须是有效数字") from exc
            if not multiplier.is_finite() or not Decimal("0.01") <= multiplier <= 100:
                raise ValueError("倍率必须在 0.01 到 100 之间")
            result[kind].append({"model_pattern": pattern, "multiplier": format(multiplier.normalize(), "f")})
    long_context = value["long_context_pricing_enabled"]
    if long_context is not None and type(long_context) is not bool:
        raise ValueError("长上下文配置必须为启用、关闭或不接管")
    result["long_context_pricing_enabled"] = long_context
    return result


def managed_fields(group):
    return {
        "model_pricing": deepcopy(group.get("model_pricing") or []),
        "long_context_pricing_enabled": group["long_context_pricing_enabled"],
        "free_openai_fast": group["free_openai_fast"],
    }


def canonical_fields(value):
    """Group PUT normalizes response IDs and fills nullable schema fields."""
    def canonical(item):
        if isinstance(item, dict):
            return {key: canonical(val) for key, val in item.items()
                    if key not in RESPONSE_FIELDS and val is not None and val != []}
        if isinstance(item, list):
            return [canonical(row) for row in item]
        return item
    return canonical(value)


def rule_factor(rules, model):
    for row in rules:
        if fnmatchcase(model.casefold(), row["model_pattern"]):
            return Decimal(row["multiplier"])
    return None


def uniform_factor(rules, pattern):
    """Prove one first-match factor for a whole upstream prefix."""
    pattern = pattern.casefold()
    if not pattern.endswith("*"):
        return rule_factor(rules, pattern)
    prefix = pattern[:-1]
    subsets = []
    for row in rules:
        candidate = row["model_pattern"]
        factor = Decimal(row["multiplier"])
        if candidate.endswith("*") and prefix.startswith(candidate[:-1]):
            if all(value == factor for _, value in subsets):
                return factor
            break
        if candidate.startswith(prefix):
            if not any(
                prior == candidate or (prior.endswith("*") and candidate.startswith(prior[:-1]))
                for prior, _ in subsets
            ):
                subsets.append((candidate, factor))
    if subsets:
        raise ValueError("目标规则仅覆盖已有通配符价卡的一部分，无法保留完整覆盖范围；请先在上游拆分价卡")
    return None


def matching_card(cards, model):
    wildcard = None
    for card in cards:
        if card.get("platform", "openai") != "openai":
            continue
        for pattern in card.get("models", []):
            pattern = pattern.strip().casefold()
            if pattern == model.casefold():
                return card
            if wildcard is None and pattern.endswith("*") and model.casefold().startswith(pattern[:-1]):
                wildcard = card
    return wildcard


class PricingPlanner:
    def __init__(self, client, guard):
        self.client = client
        self.guard = guard
        self.models = client.catalog_models()
        self.channels = client.list_active_pricing_channels()
        self.catalog = {}

    def base_prices(self, model):
        if model not in self.catalog:
            self.guard.renew()
            self.catalog[model] = self.client.catalog_model_pricing(model)
        data = self.catalog[model]
        if data is None:
            raise ValueError(f"模型 {model} 无可验证基础价，未写入上游")
        return data

    def plan(self, group_id, baseline, policy):
        desired = deepcopy(baseline)
        if policy["long_context_pricing_enabled"] is not None:
            desired["long_context_pricing_enabled"] = policy["long_context_pricing_enabled"]
        if policy["fast_rules"]:
            if baseline["free_openai_fast"] and not any(row["model_pattern"] == "*" for row in policy["fast_rules"]):
                raise ValueError("分组启用了免费 FAST，不能只修改部分模型而改变其余模型扣费；请先在上游处理该开关或设置全模型规则")
            desired["free_openai_fast"] = False
        if not policy["fast_rules"] and not policy["model_rules"]:
            return desired
        channels = [row for row in self.channels if group_id in row.get("group_ids", [])]
        if len(channels) > 1:
            raise ValueError("同一分组存在多个有效渠道，无法确定基础价格")
        channel_cards = channels[0].get("model_pricing", []) if channels else []
        original = baseline["model_pricing"]
        models = set(self.models)
        for card in [*original, *channel_cards]:
            if card.get("platform", "openai") == "openai":
                models.update(model for model in card.get("models", []) if "*" not in model)
        result = []
        handled = set()
        wildcard_cards = set()
        wildcard_matched = False
        for card in original:
            patterns = card.get("models", [])
            if card.get("platform", "openai") != "openai" or not any("*" in model for model in patterns):
                continue
            for pattern in patterns:
                fast = uniform_factor(policy["fast_rules"], pattern)
                factor = uniform_factor(policy["model_rules"], pattern)
                copy = {key: deepcopy(value) for key, value in card.items() if key not in RESPONSE_FIELDS}
                copy["models"] = [pattern]
                if factor is not None and factor != 1:
                    if pattern.endswith("*") and any(copy.get(field) is None for field in PRICE_FIELDS[:-1]):
                        raise ValueError("已有通配符价卡继承各模型基础价，无法用同一绝对价无损应用模型倍率")
                    base = {} if pattern.endswith("*") else self.base_prices(pattern)
                    for field in PRICE_FIELDS:
                        price = copy.get(field)
                        if price is None:
                            price = base.get(field)
                        if price is not None:
                            copy[field] = float(Decimal(str(price)) * factor)
                if fast is not None:
                    copy["fast_multiplier"] = float(fast)
                wildcard_matched |= fast is not None or factor is not None
                result.append(copy)
            wildcard_cards.add(id(card))
        for model in sorted(models):
            fast = rule_factor(policy["fast_rules"], model)
            factor = rule_factor(policy["model_rules"], model)
            if fast is None and factor is None:
                continue
            group_card = matching_card(original, model)
            if group_card is not None and id(group_card) in wildcard_cards:
                continue
            channel_card = matching_card(channel_cards, model) if group_card is None else None
            source = group_card or channel_card
            if source and (source.get("billing_mode", "token") or "token") != "token":
                raise ValueError(f"模型 {model} 不是 token 定价，不能自动换算模型倍率")
            if channel_card and (channel_card.get("intervals") or channel_card.get("time_pricing")):
                raise ValueError(f"模型 {model} 使用渠道阶梯或分时价格，分组覆盖不能无损保留，未修改此分组")
            card = deepcopy(source) if source else {"platform": "openai", "billing_mode": "token"}
            card = {key: val for key, val in card.items() if key not in RESPONSE_FIELDS}
            card["models"] = [model]
            # Missing group fields inherit catalog, never the lower-priority channel.
            if factor is not None and factor != 1:
                base = self.base_prices(model)
                for field in PRICE_FIELDS:
                    price = card.get(field)
                    if price is None:
                        price = base.get(field)
                    if price is not None:
                        decimal_price = Decimal(str(price))
                        if not decimal_price.is_finite() or decimal_price < 0:
                            raise ValueError(f"模型 {model} 的基础价无效")
                        card[field] = float(decimal_price * factor)
            if fast is not None:
                card["fast_multiplier"] = float(fast)
            result.append(card)
            handled.add(model.casefold())
        if not handled and not wildcard_matched:
            raise ValueError("当前模型目录和自定义价卡均未匹配目标规则，未修改上游")
        for card in original:
            if id(card) in wildcard_cards:
                continue
            retained = [model for model in card.get("models", []) if model.casefold() not in handled]
            if card.get("platform", "openai") != "openai" or retained:
                copy = deepcopy(card)
                if card.get("platform", "openai") == "openai":
                    copy["models"] = retained
                result.append(copy)
        desired["model_pricing"] = result
        return desired
