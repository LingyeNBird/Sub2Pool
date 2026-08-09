"""Database models grouped by business domain."""
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


from .settings import AppSettings
from .participants import Participant
from .observations import (
    Observation,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from .fast_correction import ObservationFastCorrection
from .audit import BlockedIPAddress, LoginEvent, NotificationEvent

__all__ = [
    "AppSettings",
    "BlockedIPAddress",
    "LoginEvent",
    "NotificationEvent",
    "Observation",
    "ObservationFastCorrection",
    "Participant",
    "ParticipantSnapshot",
    "ParticipantUsageSample",
    "Sub2APIUserUsageSample",
    "PERCENT_VALIDATORS",
    "validate_service_url",
]
