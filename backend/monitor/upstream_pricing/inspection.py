"""Read upstream configuration without changing policy or pricing ownership."""
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase

from .planning import PRICE_FIELDS, matching_card


def _number(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("上游价格包含无效数字") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("上游价格包含无效数字")
    return result


def _text(value):
    return format(value.normalize(), "f")


def _compare_prices(row, card, source, catalog):
    try:
        ratios = {}
        incomplete = False
        for field in PRICE_FIELDS:
            actual = card.get(field)
            original = source.get(field)
            if actual is None or original is None:
                if "*" in row["model"]:
                    if actual is not None or original is not None or field in ("input_price", "output_price"):
                        incomplete = True
                    continue
                fallback = (catalog or {}).get(field)
                actual = fallback if actual is None else actual
                original = fallback if original is None else original
            actual, original = _number(actual), _number(original)
            if actual is None and original is None:
                continue
            if actual is None or original is None or (original == 0 and actual != 0):
                incomplete = True
            elif original != 0:
                ratios[field] = actual / original
        row["ratios"] = {field: _text(value) for field, value in ratios.items()}
        values = list(ratios.values())
        if values and not incomplete and max(values) - min(values) <= max(Decimal(1), max(values)) * Decimal("0.000000001"):
            row["multiplier"] = _text(values[0])
        else:
            row["warning"] = "基础价不完整，无法确认统一倍率。" if incomplete or not values else "各价格项倍率不同，请查看分项倍率。"
    except ValueError as exc:
        row["warning"] = str(exc)


def _ordered_fast_rows(cards, rules):
    """Split mixed cards by their first matching policy rule, then sort before pagination."""
    ranked = []
    for card in cards:
        buckets = {}
        for model in card.get("models", []):
            rank = next(
                (index for index, rule in enumerate(rules)
                 if fnmatchcase(model.casefold(), rule["model_pattern"].strip().casefold())),
                len(rules),
            )
            buckets.setdefault(rank, []).append(model)
        if not buckets:
            buckets[len(rules)] = []
        for rank, models in buckets.items():
            row = {"models": models, "multiplier": card.get("fast_multiplier")}
            ranked.append((rank, row))
    ranked.sort(key=lambda item: (item[0], item[1]["multiplier"] is None))
    return [row for _rank, row in ranked]


def current_pricing(client, state, base_url, group_id, kind):
    group = client.group_pricing(group_id)
    if group.get("platform") != "openai":
        raise ValueError("只能查看 OpenAI 分组")
    result = {"kind": kind, "group_id": group_id, "group_name": group.get("name") or str(group_id)}
    if kind == "context":
        result["enabled"] = group.get("long_context_pricing_enabled")
        return result
    cards = [card for card in group.get("model_pricing") or [] if card.get("platform", "openai") == "openai"]
    if kind == "fast":
        result["free_fast"] = bool(group.get("free_openai_fast", False))
        rules = state.policy.get("fast_rules", [])
        result["rows"] = _ordered_fast_rows(cards, rules)
        return result
    if kind != "model":
        raise ValueError("无效的计费查看类型")

    target = next((row for row in state.targets if row["group_id"] == group_id), None) if state.base_url.rstrip("/") == base_url.rstrip("/") else None
    baseline = (target or {}).get("baseline", {}).get("model_pricing", [])
    channels = None
    pending = []
    needed = set()
    rows = []
    for card in cards:
        for model in card.get("models", []):
            row = {"model": model, "multiplier": None, "reference": "", "warning": "", "ratios": {},
                   "prices": {field: card.get(field) for field in PRICE_FIELDS}}
            rows.append(row)
            if (card.get("billing_mode") or "token") != "token":
                row["warning"] = "此规则不是 Token 计价，不能换算为统一模型倍率。"
                continue
            source = matching_card(baseline, model)
            if source is not None:
                row["reference"] = "接管前分组价；未设置字段采用当前目录价"
            else:
                if channels is None:
                    channels = [channel for channel in client.list_active_pricing_channels() if group_id in channel.get("group_ids", [])]
                if len(channels) > 1:
                    row["warning"] = "存在多个有效渠道，无法确认唯一基础价。"
                    continue
                source = matching_card(channels[0].get("model_pricing") or [], model) if channels else None
                row["reference"] = "当前渠道价；未设置字段采用当前目录价" if source else "当前模型目录价"
            if source and (source.get("intervals") or source.get("time_pricing")):
                row["warning"] = "基础价包含阶梯或分时规则，不能合并为单一倍率。"
                continue
            source = source or {}
            try:
                unchanged = all(_number(card.get(field)) == _number(source.get(field)) for field in PRICE_FIELDS)
            except ValueError as exc:
                row["warning"] = str(exc)
                continue
            if unchanged:
                # Inherited fields have the same reference on both sides. FAST-only
                # cards must not trigger one catalog request per unmodified model.
                row["multiplier"] = "1"
                row["reference"] += "（价格字段一致，未额外调整）"
                row["ratios"] = {field: "1" for field in PRICE_FIELDS if _number(card.get(field)) not in (None, 0)}
                continue
            pending.append((row, card, source))
            if "*" not in model and any(card.get(field) is None or source.get(field) is None for field in PRICE_FIELDS):
                needed.add(model)
    # The upstream API has no bulk-price endpoint. Fetch only missing references,
    # deduplicate them, and bound concurrency rather than serializing network RTTs.
    if len(needed) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(needed))) as executor:
            futures = {model: executor.submit(client.catalog_model_pricing, model) for model in sorted(needed)}
            catalog = {model: future.result() for model, future in futures.items()}
    else:
        catalog = {model: client.catalog_model_pricing(model) for model in needed}
    for row, card, source in pending:
        _compare_prices(row, card, source, catalog.get(row["model"]))
    result["rows"] = rows
    return result
