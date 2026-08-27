"""Quota-plan detection and particle-capacity range profiles."""

from __future__ import annotations

from dataclasses import dataclass, replace

QUOTA_PROFILE_AUTO = "auto"
QUOTA_PROFILE_PLUS = "plus"
QUOTA_PROFILE_PRO_5X = "pro_5x"
QUOTA_PROFILE_PRO_20X = "pro_20x"

QUOTA_PROFILE_CHOICES = (
    (QUOTA_PROFILE_AUTO, "自动识别 Plus / Pro"),
    (QUOTA_PROFILE_PLUS, "ChatGPT Plus"),
    (QUOTA_PROFILE_PRO_5X, "ChatGPT Pro 5X"),
    (QUOTA_PROFILE_PRO_20X, "ChatGPT Pro 20X"),
)


@dataclass(frozen=True)
class CapacityRangeProfile:
    code: str
    capacity_min_usd: float
    capacity_max_usd: float
    upper_stages_usd: tuple[float, ...]
    lower_stages_usd: tuple[float, ...]
    initial_capacity_sd_usd: float


PLUS_CAPACITY_PROFILE = CapacityRangeProfile(
    code=QUOTA_PROFILE_PLUS,
    capacity_min_usd=100.0,
    capacity_max_usd=200.0,
    upper_stages_usd=(300.0, 500.0, 1000.0),
    lower_stages_usd=(50.0, 25.0, 10.0),
    initial_capacity_sd_usd=5.0,
)
PRO_5X_CAPACITY_PROFILE = CapacityRangeProfile(
    code=QUOTA_PROFILE_PRO_5X,
    capacity_min_usd=500.0,
    capacity_max_usd=1500.0,
    upper_stages_usd=(3000.0, 5000.0, 10000.0),
    lower_stages_usd=(350.0, 125.0, 25.0),
    initial_capacity_sd_usd=60.0,
)
PRO_20X_CAPACITY_PROFILE = CapacityRangeProfile(
    code=QUOTA_PROFILE_PRO_20X,
    capacity_min_usd=1400.0,
    capacity_max_usd=4000.0,
    upper_stages_usd=(6000.0, 10000.0, 20000.0),
    lower_stages_usd=(700.0, 250.0, 50.0),
    initial_capacity_sd_usd=120.0,
)

_CAPACITY_PROFILES = {
    profile.code: profile
    for profile in (
        PLUS_CAPACITY_PROFILE,
        PRO_5X_CAPACITY_PROFILE,
        PRO_20X_CAPACITY_PROFILE,
    )
}


def normalize_detected_plan_type(value: object) -> str:
    """Reduce upstream plan labels to the Plus/Pro distinction it exposes."""

    normalized = "".join(
        character
        for character in str(value or "").strip().lower()
        if character.isalnum()
    )
    if normalized in {"plus", "chatgptplus"}:
        return "plus"
    if normalized in {"pro", "chatgptpro"}:
        return "pro"
    return ""


def effective_quota_profile(selected: str, detected_plan_type: str) -> str:
    if selected in _CAPACITY_PROFILES:
        return selected
    if normalize_detected_plan_type(detected_plan_type) == "plus":
        return QUOTA_PROFILE_PLUS
    # OpenAI only distinguishes Plus from Pro here. Pro has no 5X/20X signal,
    # so automatic mode preserves the existing 20X behavior.
    return QUOTA_PROFILE_PRO_20X


def capacity_range_profile(
    selected: str,
    detected_plan_type: str = "",
    capacity_min_usd_override: object | None = None,
    capacity_max_usd_override: object | None = None,
) -> CapacityRangeProfile:
    profile = _CAPACITY_PROFILES[effective_quota_profile(selected, detected_plan_type)]
    if capacity_min_usd_override is None and capacity_max_usd_override is None:
        return profile
    if capacity_min_usd_override is None or capacity_max_usd_override is None:
        raise ValueError("容量范围上下限必须同时设置")
    capacity_min = float(capacity_min_usd_override)
    capacity_max = float(capacity_max_usd_override)
    if not 1.0 <= capacity_min < capacity_max <= 50000.0:
        raise ValueError("容量范围必须满足 1 ≤ 下限 < 上限 ≤ 50000")
    return replace(
        profile,
        capacity_min_usd=capacity_min,
        capacity_max_usd=capacity_max,
        upper_stages_usd=tuple(
            value for value in profile.upper_stages_usd if value > capacity_max
        ),
        lower_stages_usd=tuple(
            value for value in profile.lower_stages_usd if value < capacity_min
        ),
    )
