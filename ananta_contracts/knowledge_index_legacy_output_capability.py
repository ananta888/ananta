"""Narrow HMAC proof for assignment-bound legacy index output transport."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER = (
    "X-Ananta-Knowledge-Index-Legacy-Output-Capability"
)


def encode_legacy_output_capability(
    binding: Mapping[str, object], *, secret: str
) -> str:
    payload = json.dumps(
        dict(binding), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_legacy_output_capability(
    binding: Mapping[str, object], *, secret: str, encoded: str
) -> bool:
    expected = encode_legacy_output_capability(binding, secret=secret)
    return hmac.compare_digest(expected, str(encoded or "").strip().lower())


__all__ = [
    "KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER",
    "encode_legacy_output_capability",
    "verify_legacy_output_capability",
]
