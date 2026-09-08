"""Administrator-only pricing inventory and non-destructive catalog sync."""

from ..cpa.price_sync import PriceCatalogError, pricing_inventory, sync_missing_prices
from ..models import AppSettings
from .base import error, ok
from .cpa_participants import CPAAdminView, cpa_account


class CPAPricingView(CPAAdminView):
    def get(self, request):
        account = (
            cpa_account(request, enabled_only=False)
            if request.query_params.get("account_id")
            else None
        )
        return ok(
            pricing_inventory(AppSettings.load(), account.id if account else None)
        )

    def post(self, request):
        account = (
            cpa_account(request, enabled_only=False)
            if request.query_params.get("account_id")
            else None
        )
        try:
            result = sync_missing_prices(account.id if account else None)
        except PriceCatalogError as exc:
            return error(str(exc), 502)
        except ValueError:
            return error("价格未保存：历史额度重算失败，请稍后重试", 409)
        return ok(result)
