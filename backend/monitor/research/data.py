"""Build disjoint quota blocks from raw snapshots, never from the quota filter.

All identities and times in these dataclasses stay LOCAL. The report builder
exports only aggregate counts, scores and covariance, never these objects.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re

from django.db import transaction
from django.utils import timezone
from ..accounting.boundaries import same_official_reset
from ..billing_correction.facts import validate_capture
from ..models import MonitoredAccount, Observation, ObservationBillingCapture
from .protocol import EXCLUSION_KEYS, WINDOW_DAYS

TARGET = re.compile(r"^gpt-6(?:[.\-]|$)", re.I)
BASELINE = re.compile(r"^gpt-5\.6(?:[.\-]|$)", re.I)


@dataclass(frozen=True)
class Block:
    start: float  # hours; only local
    end: float
    quota: float  # raw percentage POINTS (not a resource attribution estimate)
    baseline: float
    target: tuple[float, float, float, float]
    baseline_requests: int
    target_requests: int


class Ineligible(ValueError):
    pass


def quota_time(observation):
    raw = observation.raw_window
    value = raw.get("sampled_at")
    if value:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if result.utcoffset() is None:
                raise ValueError
        except (ValueError, TypeError):
            raise Ineligible("missing_snapshot_time") from None
    elif raw.get("query_mode") == "direct":
        result = observation.observed_at
    else:
        # A passive polling time is NOT the upstream quota measurement time.
        raise Ineligible("missing_snapshot_time")
    if not observation.upstream_resets_at - timedelta(seconds=observation.window_seconds) <= result <= observation.observed_at + timedelta(seconds=60):
        raise Ineligible("missing_snapshot_time")
    return result


def _standard_short(fact):
    if fact.service_tier.strip().casefold() not in ("", "default", "standard", "auto"):
        return False
    if fact.long_context_billing_applied is not None:
        return not fact.long_context_billing_applied
    tokens = (fact.input_tokens, fact.cache_creation_tokens, fact.cache_read_tokens)
    return all(value is not None for value in tokens) and sum(tokens) <= 272000


def _block(account_id, start, end, delta):
    """Verify coverage at true quota timestamps, then clip logs to [start,end)."""
    captures = list(ObservationBillingCapture.objects.filter(
        observation__account_id=account_id, started_at__lt=end, ended_at__gt=start,
    ).select_related("observation").order_by("started_at", "pk"))
    cursor = start
    for capture in captures:
        if capture.started_at > cursor:
            raise Ineligible("capture_gap")
        cursor = max(cursor, capture.ended_at)
    if cursor < end:
        raise Ineligible("capture_gap")
    if sum(item.request_count for item in captures) > 50000:
        raise Ineligible("resource_limit")
    baseline, target = Decimal(0), [Decimal(0)] * 4
    base_count = target_count = 0
    seen = {}
    for capture in captures:
        facts = list(capture.facts.select_related("research_components"))
        try:
            validate_capture(capture, capture.observation, facts)
        except ValueError:
            raise Ineligible("invalid_capture") from None
        for fact in facts:
            if not start <= fact.created_at < end:
                continue
            component = getattr(fact, "research_components", None)
            if component is None:
                raise Ineligible("missing_components")
            raw = tuple(getattr(component, name) for name in ("input_cost", "cache_creation_cost", "cache_read_cost", "output_cost"))
            signature = (fact.created_at, fact.model, fact.service_tier, fact.total_cost, raw)
            if fact.source_log_id in seen:
                if seen[fact.source_log_id] != signature:
                    raise Ineligible("invalid_capture")
                continue
            seen[fact.source_log_id] = signature
            if not _standard_short(fact):
                raise Ineligible("nonstandard_request")
            is_target, is_base = bool(TARGET.match(fact.model)), bool(BASELINE.match(fact.model))
            if not (is_target or is_base):
                raise Ineligible("other_model")
            try:
                costs = tuple(Decimal(value) for value in raw)
                total = Decimal(fact.total_cost)
                if any(not value.is_finite() or value < 0 for value in costs):
                    raise ValueError
                # Covers rounding only. Images/audio/extra prices must NOT be
                # silently assigned to the input or output hypothesis.
                if abs(sum(costs) - total) > max(Decimal("0.000004"), total * Decimal("0.00001")):
                    raise ValueError
            except (ValueError, ArithmeticError):
                raise Ineligible("cost_mismatch") from None
            if is_target:
                target_count += 1
                target = [old + value for old, value in zip(target, costs, strict=True)]
            else:
                base_count += 1
                baseline += total
    if baseline + sum(target) <= 0:
        raise Ineligible("cost_mismatch")
    return Block(start.timestamp() / 3600, end.timestamp() / 3600, delta, float(baseline), tuple(map(float, target)), base_count, target_count)


def collect_cycles(now=None):
    """Bounded snapshot reads. Busy/error paths are isolated from core monitoring.

    Manually excluded measurements are respected; automatic exclusions based on
    an existing (possibly mispriced) quota filter are NOT reused as truth.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    cycles, exclusions = [], dict.fromkeys(EXCLUSION_KEYS, 0)
    accounts = list(MonitoredAccount.objects.filter(enabled=True, provider="sub2api").order_by("pk")[:101])
    if len(accounts) > 100:
        exclusions["resource_limit"] += 1
    for account in accounts[:100]:
        # Reading one account at a time avoids a process-wide read transaction.
        with transaction.atomic():
            observations = list(Observation.objects.filter(account_id=account.fact_key, observed_at__gte=cutoff, observed_at__lte=now).order_by("-observed_at", "-pk")[:16001])
            if len(observations) > 16000:
                exclusions["resource_limit"] += 1
            observations = list(reversed(observations[:16000]))
            points, duplicates = [], {}
            for observation in observations:
                if observation.exclusion_source == "manual" and observation.excluded_at:
                    exclusions["excluded_observation"] += 1
                    continue
                try:
                    at = quota_time(observation)
                except Ineligible as exc:
                    exclusions[str(exc)] += 1
                    continue
                percent = float(observation.upstream_used_percent)
                if not 0 <= percent < 100:
                    exclusions["reset_or_saturation"] += 1
                    continue
                key = at
                if key in duplicates:
                    if duplicates[key][1] != percent:
                        exclusions["snapshot_conflict"] += 1
                        duplicates[key] = None, -1
                    continue
                duplicates[key] = observation, percent
            points = [(at, obs, pct) for at, (obs, pct) in sorted(duplicates.items()) if obs is not None]
            groups = []
            for point in points:
                if not groups or not same_official_reset(groups[-1][-1][1].upstream_resets_at, point[1].upstream_resets_at):
                    groups.append([])
                groups[-1].append(point)
            if len(groups) > 14:
                exclusions["resource_limit"] += len(groups) - 14
            for group in groups[-14:]:
                blocks = []
                anchor = group[0]
                for point in group[1:]:
                    delta = point[2] - anchor[2]
                    hours = (point[0] - anchor[0]).total_seconds() / 3600
                    if delta < 0 or hours <= 0 or hours > 6:
                        exclusions["reset_or_saturation" if delta < 0 else "insufficient_progress"] += 1
                        anchor = point
                        continue
                    if delta < 3:
                        continue
                    try:
                        blocks.append(_block(account.fact_key, anchor[0], point[0], delta))
                    except Ineligible as exc:
                        exclusions[str(exc)] += 1
                    anchor = point
                # Need within-cycle baseline anchors AND target use. Pure-target
                # cycles cannot identify an overall price/capacity rescaling.
                if len(blocks) < 8 or sum(b.baseline_requests for b in blocks) < 10 or sum(b.target_requests for b in blocks) < 10:
                    if blocks:
                        exclusions["insufficient_cycle"] += 1
                    continue
                cycles.append(blocks)
    return cycles, exclusions
