"""累计样本有效性与保守汇率选择的纯计算核心。"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

RATE_PRECISION = Decimal("0.000001")
ZERO = Decimal("0")


@dataclass(frozen=True)
class RateDecision:
    valid_sample: bool
    sample_rate: Decimal | None
    effective_rate: Decimal
    source: str


def quantize_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)


def weighted_percentile(
    samples: list[tuple[Decimal, Decimal]],
    percentile: int,
) -> Decimal:
    ordered = sorted(
        (
            (rate, max(weight, Decimal("0.0001")))
            for rate, weight in samples
            if rate > 0
        ),
        key=lambda item: item[0],
    )
    if not ordered:
        raise ValueError("no samples")
    target = (
        sum((weight for _rate, weight in ordered), ZERO)
        * Decimal(percentile)
        / Decimal(100)
    )
    cumulative = ZERO
    for rate, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return rate
    return ordered[-1][0]


def select_rate(
    *,
    selected_total: Decimal,
    interval_percent: Decimal,
    delta_percent: Decimal | None,
    delta_cost: Decimal | None,
    has_previous: bool,
    history: list[tuple[Decimal, Decimal]],
    history_samples: int,
    percentile: int,
    fallback_rate: Decimal | None,
    initial_rate: Decimal,
) -> RateDecision:
    valid_sample = bool(
        selected_total > 0
        and interval_percent > 0
        and (
            not has_previous
            or (
                delta_percent is not None
                and delta_percent > 0
                and delta_cost is not None
                and delta_cost > 0
            )
        )
    )
    sample_rate = (
        quantize_rate(selected_total / interval_percent)
        if valid_sample
        else None
    )
    previous_count = max(0, history_samples - 1)
    history_start = max(0, len(history) - previous_count)
    candidates = history[history_start : len(history)]
    if sample_rate is not None:
        candidates = [*candidates, (sample_rate, interval_percent)]
    if candidates:
        effective_rate = quantize_rate(
            weighted_percentile(candidates, percentile)
        )
        source = "current_interval_samples"
    elif fallback_rate is not None:
        effective_rate = fallback_rate
        source = "previous_interval_history"
    else:
        effective_rate = quantize_rate(initial_rate)
        source = "initial_fallback"
    return RateDecision(
        valid_sample=valid_sample,
        sample_rate=sample_rate,
        effective_rate=effective_rate,
        source=source,
    )


def sample_note(
    *,
    has_previous: bool,
    delta_percent: Decimal | None,
    delta_cost: Decimal | None,
    valid_sample: bool,
    rate_source: str,
) -> str:
    if rate_source == "previous_interval_history":
        return "当前区间尚无有效样本，暂沿用上一归属区间的有效估值"
    if not has_previous:
        return (
            "当前归属区间累计口径初始化样本"
            if valid_sample
            else "区间首次观测，当前没有足够数据形成累计口径样本"
        )
    if valid_sample:
        return "有效累计口径样本"
    if delta_percent == 0:
        return "上游百分比未变化，本次不更新美元/百分比"
    if delta_percent is not None and delta_percent < 0:
        return "上游百分比回退，本次不倒扣参与者账本"
    if delta_cost is not None and delta_cost <= 0:
        return "成本没有正向变化，本次样本无效"
    return "本次样本无效"
