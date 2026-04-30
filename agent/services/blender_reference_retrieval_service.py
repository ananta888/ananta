from __future__ import annotations

from typing import Any


def retrieve_blender_references(*, intent: str, query: str, max_items: int = 8) -> list[dict[str, Any]]:
    """Bounded retrieval stub over codecompass/rag_helper-produced indexes."""
    q = str(query or '').strip()
    if not q:
        return []
    out = []
    for idx in range(max(1, min(max_items, 8))):
        out.append({
            "source": "blender_api_docs",
            "ref": "blender-docs",
            "path": f"docs/section_{idx}.md",
            "symbol": "bpy.ops.example",
            "reason": str(intent or "general"),
            "snippet": q[:160],
        })
    return out
