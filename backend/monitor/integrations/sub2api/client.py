"""Stable facade combining the resource-specific Sub2API clients."""
from .accounts import AccountResourceMixin
from .quota import QuotaResourceMixin
from .transport import Sub2APITransport
from .usage import UsageResourceMixin
from .users import UserResourceMixin
from .pricing import PricingResourceMixin


class Sub2APIClient(
    AccountResourceMixin,
    UserResourceMixin,
    UsageResourceMixin,
    QuotaResourceMixin,
    PricingResourceMixin,
    Sub2APITransport,
):
    """Admin API facade; normal sampling methods are read-only."""
