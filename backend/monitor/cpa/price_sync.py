"""Fill missing CPA base prices from the public models.dev OpenAI catalog."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

import httpx
from django.db.models import Count, Sum
from django.utils import timezone

from ..history_state import fenced_fact_write
from ..models import AppSettings, CPAUsageEvent, MonitoredAccount
from ..replay import rebuild_account
from .pricing import resolve_model_price
from .usage import refresh_cpa_history

CATALOG_URL = "https://models.dev/api.json"
MAX_CATALOG_BYTES = 16 * 1024 * 1024


class PriceCatalogError(ValueError):
    pass


def fetch_catalog():
    # Model names, keys and usage never leave this service.
    try:
        with httpx.stream(
            "GET",
            CATALOG_URL,
            timeout=20,
            headers={"User-Agent": "Sub2Pool/1.0", "Accept": "application/json"},
        ) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_CATALOG_BYTES:
                    raise PriceCatalogError("价格目录过大，请稍后重试")
                chunks.append(chunk)
        import json

        catalog = json.loads(b"".join(chunks), parse_float=Decimal)
        models = catalog["openai"]["models"]
        if not isinstance(models, dict) or not models:
            raise ValueError("empty catalog")
        return models
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise PriceCatalogError(
            "无法读取 models.dev 价格目录，请稍后重试或手动填写价格；已有价格未更改"
        ) from exc


def observed_models(account_id=None):
    events = CPAUsageEvent.objects.all()
    if account_id is not None:
        events = events.filter(account_id=account_id)
    return (
        events.order_by()
        .values("model")
        .annotate(request_count=Count("id"), token_count=Sum("total_tokens"))
        .order_by("model")
    )


def pricing_inventory(config, account_id=None):
    models = []
    for row in observed_models(account_id):
        resolved = resolve_model_price(config.cpa_model_pricing, row["model"])
        models.append(
            {
                **row,
                "missing": resolved is None,
                "pricing_model": resolved[0] if resolved else None,
            }
        )
    return {
        "pricing": config.cpa_model_pricing,
        "models": models,
        "missing_model_count": sum(row["missing"] for row in models),
        "unpriced_request_count": sum(
            row["request_count"] for row in models if row["missing"]
        ),
        "source": "models.dev",
        "source_url": CATALOG_URL,
        "generated_at": timezone.now().isoformat(),
    }


def catalog_price(models, model):
    # Match official OpenAI IDs only; don't guess prices from another provider,
    # dates, reasoning suffixes, or an arbitrary custom alias.
    candidates = [model]
    if model.endswith("-latest"):
        candidates.append(model[:-7])
    if model.startswith("openai/"):
        candidates.append(model[len("openai/") :])
    for name in candidates:
        entry = models.get(name)
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost")
        if not isinstance(cost, dict):
            continue
        result = {}
        for local, remote in (
            ("input", "input"),
            ("cached_input", "cache_read"),
            ("output", "output"),
        ):
            try:
                value = Decimal(str(cost[remote]))
                if isinstance(cost[remote], bool) or not value.is_finite() or value < 0:
                    return None
                result[local] = str(value)
            except (KeyError, ValueError, InvalidOperation, TypeError):
                return None
        return name, result
    return None


def sync_missing_prices(account_id=None):
    config = AppSettings.load()
    inventory = pricing_inventory(config, account_id)
    if not inventory["missing_model_count"]:
        return {**inventory, "added": [], "unresolved": []}
    models = fetch_catalog()
    accounts = list(MonitoredAccount.objects.filter(provider="cpa").order_by("id"))
    with fenced_fact_write(
        [account.fact_key for account in accounts], ttl=timedelta(minutes=30)
    ) as guards:
        config = AppSettings.objects.select_for_update().get(pk=config.pk)
        pricing = dict(config.cpa_model_pricing)
        added, unresolved = [], []
        for row in observed_models(account_id):
            model = row["model"]
            # Recheck after taking the lock so concurrent manual edits win.
            if resolve_model_price(pricing, model) is not None:
                continue
            match = catalog_price(models, model)
            if match is None:
                unresolved.append(
                    {
                        "model": model,
                        "reason": "目录未收录此名称或缺少完整的三项价格，请手动填写",
                    }
                )
                continue
            source_model, price = match
            pricing[model] = price
            added.append(
                {
                    "model": model,
                    "source_model": f"openai/{source_model}",
                    "price": price,
                }
            )
        if added:
            config.cpa_model_pricing = pricing
            config.save(update_fields=["cpa_model_pricing", "updated_at"])
            refresh_cpa_history(config, rebuild=False)
            for account in accounts:
                rebuild_account(
                    account.fact_key, config, guard=guards[account.fact_key]
                )
        return {
            **pricing_inventory(config, account_id),
            "added": added,
            "unresolved": unresolved,
        }
