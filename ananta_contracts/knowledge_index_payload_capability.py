"""Transport encoding for assignment-bound knowledge-index payload access."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any


KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER = (
    "X-Ananta-Source-Access-Manifest"
)
_MAX_CAPABILITY_BYTES = 32 * 1024


def encode_knowledge_index_payload_capability(
    manifest: Mapping[str, Any],
) -> str:
    raw = json.dumps(
        dict(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if not raw or len(raw) > _MAX_CAPABILITY_BYTES:
        raise ValueError("knowledge_index_payload_capability_invalid")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_knowledge_index_payload_capability(
    encoded: str,
) -> dict[str, Any]:
    value = str(encoded or "").strip()
    if not value or len(value) > (_MAX_CAPABILITY_BYTES * 2):
        raise ValueError("knowledge_index_payload_capability_invalid")
    try:
        raw = base64.b64decode(
            value + ("=" * (-len(value) % 4)),
            altchars=b"-_",
            validate=True,
        )
        decoded = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "knowledge_index_payload_capability_invalid"
        ) from exc
    if (
        not isinstance(decoded, dict)
        or not decoded
        or len(raw) > _MAX_CAPABILITY_BYTES
    ):
        raise ValueError("knowledge_index_payload_capability_invalid")
    return dict(decoded)


__all__ = [
    "KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER",
    "decode_knowledge_index_payload_capability",
    "encode_knowledge_index_payload_capability",
]
