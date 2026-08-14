"""Canonical hashing helpers for persisted source facts."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping, Any


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def canonical_rows_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash an ordered row stream without materializing the full collection."""

    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for row in rows:
        if not first:
            digest.update(b",")
        digest.update(_canonical_bytes(row))
        first = False
    digest.update(b"]")
    return digest.hexdigest()


def canonical_object_digest(
    *,
    scalars: Mapping[str, Any] | None = None,
    row_sections: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
) -> str:
    """Hash one canonical JSON object while streaming ordered row arrays."""

    scalar_values = dict(scalars or {})
    sections = dict(row_sections or {})
    overlap = set(scalar_values) & set(sections)
    if overlap:
        raise ValueError(f"canonical object has duplicate keys: {sorted(overlap)}")
    digest = hashlib.sha256()
    digest.update(b"{")
    first_key = True
    for key in sorted(set(scalar_values) | set(sections)):
        if not first_key:
            digest.update(b",")
        digest.update(_canonical_bytes(key))
        digest.update(b":")
        if key in scalar_values:
            digest.update(_canonical_bytes(scalar_values[key]))
        else:
            digest.update(b"[")
            first_row = True
            for row in sections[key]:
                if not first_row:
                    digest.update(b",")
                digest.update(_canonical_bytes(row))
                first_row = False
            digest.update(b"]")
        first_key = False
    digest.update(b"}")
    return digest.hexdigest()




def expected_user_digest(user_ids: Iterable[int]) -> str:
    return canonical_digest(sorted({int(user_id) for user_id in user_ids}))
