"""Secret-free index diagnostics for the agentic retrieval contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agent.services.codecompass_agentic_retrieval_contract import REASON_VECTOR_STALE


def load_agentic_index_state(
    manifest: Mapping[str, Any] | None = None,
    *,
    expected: Mapping[str, Any] | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    data = dict(manifest or {})
    if not data and manifest_path:
        path = Path(manifest_path)
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                data = loaded
    embedding = dict(data.get("embedding") or {}) if isinstance(data.get("embedding"), dict) else {}
    current = {
        "manifest_hash": str(data.get("manifest_hash") or ""),
        "model": str(embedding.get("model") or data.get("model") or data.get("provider") or ""),
        "dimensions": _as_int(embedding.get("dimensions") or data.get("dimensions")),
        "embedding_text_profile": str(
            data.get("embedding_text_profile") or embedding.get("embedding_text_profile") or ""
        ),
    }
    expected_state = dict(expected or {})
    stale_fields = [
        field
        for field in ("manifest_hash", "model", "dimensions", "embedding_text_profile")
        if expected_state.get(field) not in (None, "", 0)
        and current.get(field) not in (None, "", 0)
        and str(expected_state.get(field)) != str(current.get(field))
    ]
    if stale_fields:
        current["status"] = "stale"
        current["reason"] = REASON_VECTOR_STALE
        return current
    current["status"] = "ready" if current["manifest_hash"] else "unknown"
    current["reason"] = ""
    return current


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
