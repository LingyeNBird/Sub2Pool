"""Persistent per-account-cycle batches from raw facts, not derived quota models.

Only batch UUIDs/aggregate evidence are sent. Account IDs, timestamps and raw
request fingerprints below stay local, for alignment and loss-safe replacement.
"""
import hashlib
from datetime import timedelta
from decimal import Decimal
import numpy as np
from django.db import transaction
from ..accounting.boundaries import same_official_reset
from ..billing_correction.facts import validate_capture
from ..billing_correction.rules import compile_rules, first_match
from ..models import AppSettings, Observation, ObservationBillingCapture
from ..models.research import ResearchEvidenceBatch
from .data import TARGET, BASELINE, quota_time, Ineligible
from .pooled import Interval, summarize
from .pooled_protocol import canonical, QUALITY_KEYS


RAW_OBSERVATION_FIELDS = (
    "id", "account_id", "observed_at", "upstream_resets_at", "window_seconds",
    "upstream_used_percent", "raw_window", "exclusion_source", "excluded_at",
)


class UnknownControl(ValueError):
    pass


def normalized_cost(fact, config):
    """Hold FAST=2 and target/baseline long-context=1 fixed for this study.

    Only source multipliers (not the running FAST target or GPT-6 model factor)
    enter normalization. Other-model prices are treated as the declared control.
    """
    if not fact.model.strip():
        raise UnknownControl("unknown_control")
    target = bool(TARGET.match(fact.model))
    base = bool(BASELINE.match(fact.model))
    factor = Decimal(1)
    if fact.service_tier.strip().casefold() in {"fast", "priority"}:
        match = first_match(compile_rules(config.fast_correction_rules), fact.model)
        source = Decimal(str(match["source_multiplier"])) if match else Decimal(2)
        factor *= Decimal(2) / source
    match = first_match(compile_rules(config.long_context_correction_rules), fact.model)
    if target or base or (match and config.long_context_correction_enabled):
        long = fact.long_context_billing_applied
        if long is None:
            tokens = (fact.input_tokens, fact.cache_creation_tokens, fact.cache_read_tokens)
            threshold = match["threshold_tokens"] if match else 272000
            known = sum(v for v in tokens if v is not None)
            if known > threshold:
                long = True
            elif all(v is not None for v in tokens):
                long = False
            else:
                raise UnknownControl("unknown_control")
        if long:
            source = Decimal(str(match["source_multiplier"])) if match else Decimal(2)
            goal = Decimal(1) if target or base else Decimal(str(match["target_multiplier"]))
            factor *= goal / source
    if not target and config.model_correction_enabled:
        match = first_match(compile_rules(config.model_correction_rules), fact.model)
        if match:
            factor *= Decimal(str(match["multiplier"]))
    raw = Decimal(fact.total_cost)
    if not raw.is_finite() or raw < 0:
        raise UnknownControl("invalid_fact")
    if not target:
        return float(raw * factor), None
    component = getattr(fact, "research_components", None)
    if component is None:
        raise UnknownControl("missing_components")
    parts = tuple(Decimal(getattr(component, key)) for key in ("input_cost", "cache_creation_cost", "cache_read_cost", "output_cost"))
    if any(not x.is_finite() or x < 0 for x in parts) or abs(sum(parts) - raw) > max(Decimal("0.000004"), raw * Decimal("0.00001")):
        raise UnknownControl("invalid_fact")
    return 0., tuple(float(x * factor) for x in parts)


def _fingerprint(fact):
    component = getattr(fact, "research_components", None)
    raw = [getattr(component, k, None) for k in ("input_cost", "cache_creation_cost", "cache_read_cost", "output_cost")]
    return hashlib.sha256(canonical([fact.source_log_id, fact.created_at.isoformat(), fact.model,
        fact.service_tier, fact.total_cost, fact.input_tokens, fact.cache_creation_tokens,
        fact.cache_read_tokens, fact.long_context_billing_applied, raw])).hexdigest()


def _coverage(captures, start, end):
    cursor = start
    for left, right in sorted(captures):
        if right <= cursor:
            continue
        if left > cursor:
            break
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return cursor >= end


def _cycle(account, observations, config, gateway_only):
    end = observations[0].upstream_resets_at
    start = end - timedelta(seconds=observations[0].window_seconds)
    quality = dict.fromkeys(QUALITY_KEYS, 0)
    observation_map = {o.pk: o for o in observations}
    captures = ObservationBillingCapture.objects.filter(observation_id__in=observation_map).prefetch_related("facts__research_components")
    facts, fingerprints, coverage, conflicts = {}, {}, [], set()
    for capture in captures:
        rows = list(capture.facts.all())
        try:
            validate_capture(capture, observation_map[capture.observation_id], rows)
            coverage.append((capture.started_at, capture.ended_at))
        except ValueError:
            quality["invalid_fact"] += 1
        for fact in rows:
            if not start <= fact.created_at < end:
                continue
            key, digest = str(fact.source_log_id), _fingerprint(fact)
            if key in fingerprints and fingerprints[key] != digest:
                conflicts.add(key)
            else:
                facts[key], fingerprints[key] = fact, digest
    ordered = sorted(facts.values(), key=lambda f: (f.created_at, f.source_log_id))
    normalized = {}
    total, target_total, target_count = Decimal(0), Decimal(0), 0
    for key, fact in facts.items():
        try:
            raw = Decimal(fact.total_cost)
            if not raw.is_finite() or raw < 0:
                raise ValueError
            total += raw
            if TARGET.match(fact.model):
                target_count += 1
                target_total += raw
            if key in conflicts:
                raise UnknownControl("invalid_fact")
            normalized[key] = normalized_cost(fact, config)
        except UnknownControl as exc:
            normalized[key] = str(exc)
            quality[str(exc)] += 1
        except (ValueError, ArithmeticError):
            normalized[key] = "invalid_fact"
            quality["invalid_fact"] += 1
    points = {}
    for observation in observations:
        if observation.exclusion_source == "manual" and observation.excluded_at:
            quality["missing_snapshot"] += 1
            continue
        try:
            at = quota_time(observation)
            pct = float(observation.upstream_used_percent)
            if not np.isfinite(pct) or not 0 <= pct < 100:
                quality["reset_or_saturation"] += 1
                points[at] = None
            elif at in points and points[at] != pct:
                points[at] = None
                quality["missing_snapshot"] += 1
            else:
                points[at] = pct
        except Ineligible:
            quality["missing_snapshot"] += 1
    intervals = []
    previous = None
    for at, percent in sorted(points.items()):
        if percent is None:
            previous = None
            continue
        if previous is not None:
            left, before = previous
            delta = percent - before
            if delta < 0:
                quality["reset_or_saturation"] += 1
            elif not _coverage(coverage, left, at):
                quality["capture_gap"] += 1
            else:
                rows = [f for f in ordered if left <= f.created_at < at]
                costs = [normalized[str(f.source_log_id)] for f in rows]
                if all(not isinstance(c, str) for c in costs):
                    known = sum(c[0] for c in costs)
                    target = tuple(sum((c[1] or (0.,)*4)[j] for c in costs) for j in range(4))
                    if known + sum(target) > 0:
                        intervals.append(Interval(left.timestamp()/3600, at.timestamp()/3600, delta, known, target))
                        if delta == 0:
                            quality["zero_progress"] += 1
                    elif delta > 0:
                        quality["capture_gap"] += 1
        previous = at, percent
    summary = summarize(intervals, requests=len(facts), gpt6_requests=target_count,
        raw_usd=float(total), gpt6_raw_usd=float(target_total), quality=quality, gateway_only=gateway_only)
    return end, fingerprints, summary


def collect_batches(now, *, gateway_only):
    """All retained cycles are eligible; there is no 90-day scientific cut-off.

    Existing batch evidence is never overwritten with a smaller/mutated raw
    history after local pruning. Every batch remains independently retryable.
    """
    config = AppSettings.load()
    account_ids = Observation.objects.filter(account_id__gte=0).order_by().values_list("account_id", flat=True).distinct()
    for account in account_ids:
        cycles = []
        for observation in Observation.objects.filter(account_id=account, observed_at__lte=now).only(*RAW_OBSERVATION_FIELDS).order_by("observed_at", "pk").iterator(chunk_size=500):
            if not cycles or not same_official_reset(cycles[-1][0].upstream_resets_at, observation.upstream_resets_at):
                cycles.append([])
            cycles[-1].append(observation)
        for observations in cycles:
            end, fingerprints, summary = _cycle(account, observations, config, gateway_only)
            with transaction.atomic():
                batch = ResearchEvidenceBatch.objects.filter(account_id=account, resets_at__gte=end-timedelta(minutes=10), resets_at__lte=end+timedelta(minutes=10)).order_by("created_at").first()
                if batch is None:
                    batch = ResearchEvidenceBatch.objects.create(account_id=account, resets_at=end)
                old = batch.source_fingerprints
                if any(fingerprints.get(k) != digest for k, digest in old.items()):
                    # Retain the last reproducible summary when raw sources were
                    # deleted/changed, rather than silently reducing remote data.
                    batch.archived_source = True
                    batch.save(update_fields=["archived_source"])
                    continue
                batch.summary = summary
                batch.source_fingerprints = fingerprints
                batch.computed_at = now
                batch.archived_source = False
                batch.save(update_fields=["summary", "source_fingerprints", "computed_at", "archived_source"])
    return list(ResearchEvidenceBatch.objects.order_by("created_at", "pk"))
