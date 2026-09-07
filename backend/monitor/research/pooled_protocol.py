"""Raw-only shared evidence contract; no fitted quota/capacity estimate fields."""
import hashlib
import json
import struct
from pathlib import Path
from .protocol import STUDY, FAMILIES, LABELS, candidates, canonical

PROTOCOL = "codex-cost-study"
METHOD = "pooled-profile/raw-only"
POLICY = "research-consent"
DEFAULT_ENDPOINT = "https://codex.nightunderfly.online"
MAX_BODY = 262144
QUALITY_KEYS = (
    "missing_snapshot", "capture_gap", "missing_components", "unknown_control",
    "invalid_fact", "reset_or_saturation", "zero_progress",
    "external_usage_uncontrolled", "archived_source", "resource_limit",
)


def descriptor():
    return json.loads(Path(__file__).with_name("pooled_descriptor.json").read_text())


def method_digest():
    return hashlib.sha256(canonical(descriptor())).hexdigest()


def consent_digest(endpoint, projects, gateway_only):
    return hashlib.sha256(canonical([POLICY, METHOD, method_digest(), endpoint, sorted(projects), gateway_only])).hexdigest()


def grid_digest():
    return hashlib.sha256(b"".join(struct.pack("<4dB", *p, FAMILIES.index(f)) for f, p in candidates())).hexdigest()
