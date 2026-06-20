"""signatures — evidence + change signatures for workspace-mutation loop.

Extracted from agent.common.sgpt_workspace_mutation as part of the
SGDEC Welle-2 4-split (T04). Owns the small hashing helpers that
identify unique evidence blocks and change sets.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any


def evidence_signature(entry: dict[str, Any]) -> str:
    """Stable hash of an evidence entry (deterministic, JSON-canonical)."""
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


def changes_signature(workspace: pathlib.Path, changed: list[str]) -> str:
    """Stable hash of a set of file changes within the workspace."""
    rows: list[str] = []
    for rel in sorted(changed):
        path = workspace / rel
        digest = ""
        if path.is_file():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = "unreadable"
        rows.append(f"{rel}:{digest}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
