"""Neutral metadata contract binding workflow runs to VisualProcess definitions."""

from __future__ import annotations

import re
from typing import Any

VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY = "ananta.visual_process.definition_hash"
_DEFINITION_HASH_RE = re.compile(r"(?:sha256:)?[a-fA-F0-9]{64}")


def definition_snapshot_hash(metadata: Any) -> str:
    """Return one canonical definition digest or fail closed.

    The mapper and Hub runtime projector share this small contract without
    depending on one another's implementation modules.
    """

    if not isinstance(metadata, dict):
        raise ValueError("visual_process_definition_metadata_invalid")
    raw = metadata.get(VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY)
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str) or raw != raw.strip() or _DEFINITION_HASH_RE.fullmatch(raw) is None:
        raise ValueError("visual_process_definition_hash_invalid")
    return raw.removeprefix("sha256:").lower()


__all__ = [
    "VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY",
    "definition_snapshot_hash",
]
