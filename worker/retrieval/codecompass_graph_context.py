from __future__ import annotations

from typing import Any

from worker.core.redaction import redact_text


def _path_to_relation_path(path: list[dict[str, Any]]) -> str:
    if not path:
        return "seed"
    return " -> ".join(str((edge or {}).get("edge_type") or "related") for edge in path)


def build_graph_context_chunks(
    *,
    expansion: dict[str, Any],
    max_content_chars: int = 280,
) -> list[dict[str, Any]]:
    by_node_path = {
        str((row or {}).get("node_id") or ""): list((row or {}).get("path") or [])
        for row in list(expansion.get("paths") or [])
        if isinstance(row, dict)
    }
    seeds = sorted({str(item).strip() for item in list(expansion.get("seed_node_ids") or []) if str(item).strip()})
    chunks: list[dict[str, Any]] = []
    limit = max(80, int(max_content_chars))
    for node in list(expansion.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        path = by_node_path.get(node_id, [])
        relation_path = _path_to_relation_path(path)
        expanded_from = None
        if path:
            expanded_from = str((path[0] or {}).get("source_id") or "") or None
        elif seeds:
            expanded_from = seeds[0]
        content = str(node.get("content") or node.get("name") or node.get("file") or "").strip()
        clipped = redact_text(content)[:limit]
        chunks.append(
            {
                "engine": "codecompass_graph",
                "source": str(node.get("file") or ""),
                "content": clipped,
                "score": 0.0,
                "metadata": {
                    "record_id": str(node.get("record_id") or node_id),
                    "record_kind": str(node.get("kind") or "unknown"),
                    "file": str(node.get("file") or ""),
                    "expanded_from": expanded_from,
                    "relation_path": relation_path,
                    "expansion_reason": f"profile:{str(expansion.get('profile') or 'bugfix_local')}",
                    "group": "seed" if node_id in seeds else "expanded_neighbor",
                },
            }
        )
    chunks.sort(
        key=lambda item: (
            0 if str((item.get("metadata") or {}).get("group") or "") == "seed" else 1,
            str(item.get("source") or ""),
            str((item.get("metadata") or {}).get("record_id") or ""),
        )
    )
    return chunks

