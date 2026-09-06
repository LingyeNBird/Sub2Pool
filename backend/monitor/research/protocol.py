"""Predeclared research contract, shared with CodexSubscribeStudy.

Changing the estimator, eligibility rules or candidate grid requires a new
METHOD. Results from different methods must not be pooled by the receiver.
"""
import hashlib
import itertools
import json

PROTOCOL = "codex-cost-study/1"
STUDY = "gpt6-components"
METHOD = "log-capacity-loco/1"
POLICY = "research-consent/1"
DEFAULT_ENDPOINT = "https://study.example.invalid"
COMPONENTS = ("input", "cache_creation", "cache_read", "output")
FAMILIES = ("unchanged", "global", "cache_read", "cache_creation", "output", "input", "mixed")
LABELS = ("无需额外倍率", "整体倍率", "缓存读倍率", "缓存创建倍率", "输出倍率", "输入倍率", "混合倍率")
GRID = (0.5, 1.0, 1.5, 1.75, 2.0, 3.0)
DRIFT = (0.03, 0.10, 0.30)  # log-capacity SD / sqrt(hour); common across candidates
WINDOW_DAYS = 90
MIN_REQUESTS = 200
MIN_FAMILY_REQUESTS = 50
MIN_CYCLES = 2
MIN_BLOCKS = 24
EXCLUSION_KEYS = (
    "missing_snapshot_time", "snapshot_conflict", "excluded_observation", "reset_or_saturation",
    "insufficient_progress", "capture_gap", "invalid_capture", "missing_components",
    "nonstandard_request", "other_model", "cost_mismatch", "insufficient_cycle", "resource_limit",
)
STATUSES = ("insufficient_data", "unidentifiable", "model_mismatch", "drift_sensitive", "exploratory", "external_usage_uncontrolled")


def candidates():
    values = set(itertools.product(GRID, repeat=4))
    for multiplier in (1.25, 1.8, 2.5):
        values.add((multiplier,) * 4)
        for component in range(4):
            point = [1.0] * 4
            point[component] = multiplier
            values.add(tuple(point))
    result = []
    for point in sorted(values):
        changed = [i for i, value in enumerate(point) if value != 1]
        family = "unchanged" if not changed else (
            "global" if len(set(point)) == 1 else
            COMPONENTS[changed[0]] if len(changed) == 1 else "mixed"
        )
        result.append((family, point))
    return result


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def consent_digest(endpoint, projects, gateway_only):
    return hashlib.sha256(canonical([POLICY, METHOD, method_digest(), endpoint, sorted(projects), gateway_only])).hexdigest()


def descriptor():
    return {
        "protocol": PROTOCOL, "study_id": STUDY, "method": METHOD,
        "components": list(COMPONENTS), "families": list(FAMILIES), "labels": list(LABELS),
        "grid": list(GRID), "extra_single_and_global": [1.25, 1.8, 2.5],
        "candidate_count": len(candidates()), "drift_sd_per_sqrt_hour": list(DRIFT),
        "window_days": WINDOW_DAYS, "minimum_requests": MIN_REQUESTS,
        "minimum_family_requests": MIN_FAMILY_REQUESTS, "minimum_cycles": MIN_CYCLES,
        "minimum_blocks": MIN_BLOCKS, "minimum_blocks_per_cycle": 8, "minimum_quota_points_per_block": 3,
        "maximum_block_hours": 6, "rounding_sd_floor": .03, "score_clip": 4,
        "minimum_component_design_norm": .05, "maximum_top3_design_energy_fraction": .6,
        "minimum_target_share_sd": .05, "exclusion_keys": list(EXCLUSION_KEYS),
        "scope": "same-account-cycle; standard tier; non-long-context; GPT-5.6/GPT-6 only",
        "confidence": "blocked-bootstrap predictive winner share, not probability of a true billing mechanism",
    }


def method_digest():
    return hashlib.sha256(canonical(descriptor())).hexdigest()
