"""Reusable validators shared by database model modules."""

from decimal import Decimal
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator


PERCENT_VALIDATORS = [
    MinValueValidator(Decimal("0")),
    MaxValueValidator(Decimal("100")),
]


def validate_service_url(value: str) -> None:
    """Allow Docker service names while requiring an explicit HTTP(S) URL."""

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError("请输入有效的 HTTP(S) 地址。") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("请输入有效的 HTTP(S) 地址。")
