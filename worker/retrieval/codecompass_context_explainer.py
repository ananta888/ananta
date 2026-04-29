from __future__ import annotations

from typing import Any


def build_relation_explanation(
    *,
    chunk: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(chunk.get("metadata") or {})
    relation_path = str(metadata.get("relation_path") or "seed")
    source = str(chunk.get("source") or metadata.get("file") or "").strip()
    record_kind = str(metadata.get("record_kind") or "unknown").strip().lower() or "unknown"
    expanded_from = str(metadata.get("expanded_from") or "").strip() or None
    reason = str(metadata.get("expansion_reason") or "").strip() or "profile:unknown"
    relation_steps = [item.strip() for item in relation_path.split("->") if item.strip()]
    if relation_steps and relation_steps != ["seed"]:
        human = f"Selected because relation path {relation_path} links from seed {expanded_from or 'unknown'}."
    elif expanded_from:
        human = f"Selected because it is a seed or direct local context for {expanded_from}."
    else:
        human = "Selected as direct seed context."
    if record_kind == "java_method" and "calls_probable_target" in relation_path:
        human = f"Selected because this method likely calls the target path ({relation_path})."
    if record_kind == "java_type" and "injects_dependency" in relation_path:
        human = f"Selected because this class injects a dependency ({relation_path})."
    return {
        "human": human,
        "machine": {
            "record_id": str(metadata.get("record_id") or ""),
            "file": source,
            "record_kind": record_kind,
            "expanded_from": expanded_from,
            "relation_path": relation_path,
            "relation_steps": relation_steps,
            "expansion_reason": reason,
        },
    }

