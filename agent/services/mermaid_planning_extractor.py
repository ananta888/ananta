from __future__ import annotations

import re
from typing import Any


def extract_mermaid_task_candidates(text: str) -> dict[str, Any]:
    raw = str(text or "")
    if "graph" not in raw.lower():
        return {"ok": False, "error": "not_mermaid"}
    edges = re.findall(r"([A-Za-z0-9_]+)\s*--?>\s*([A-Za-z0-9_]+)", raw)
    if not edges:
        return {"ok": False, "error": "repair_failed_mermaid_parse"}
    nodes = sorted({n for e in edges for n in e})
    idx = {n: i + 1 for i, n in enumerate(nodes)}
    subtasks = []
    for n in nodes:
        deps = [str(idx[a]) for a, b in edges if b == n]
        subtasks.append({
            "title": f"Node {n}",
            "description": f"Implement/verify Mermaid node {n}",
            "priority": "Medium",
            "depends_on": deps,
            "dependency_mode": "explicit" if deps else "parallel",
        })
    return {"ok": True, "subtasks": subtasks}
