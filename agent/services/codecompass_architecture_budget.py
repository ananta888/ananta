"""Hard budgets for hierarchical architecture slices.

Parent CodeCompass context token limits remain the outer ceiling. This
policy only subdivides that budget across hierarchy levels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

LEVELS = ("system", "subsystem", "component", "file", "symbol")

_PROFILES = {
    "overview": {
        "max_nodes": 12,
        "max_edges": 16,
        "max_depth": 2,
        "max_tokens": 1200,
        "level_caps": {"system": 1, "subsystem": 4, "component": 5, "file": 2, "symbol": 0},
    },
    "subsystem": {
        "max_nodes": 18,
        "max_edges": 24,
        "max_depth": 3,
        "max_tokens": 2000,
        "level_caps": {"system": 1, "subsystem": 3, "component": 8, "file": 4, "symbol": 2},
    },
    "component": {
        "max_nodes": 20,
        "max_edges": 28,
        "max_depth": 3,
        "max_tokens": 2400,
        "level_caps": {"system": 1, "subsystem": 2, "component": 6, "file": 7, "symbol": 4},
    },
    "evidence": {
        "max_nodes": 16,
        "max_edges": 20,
        "max_depth": 4,
        "max_tokens": 1800,
        "level_caps": {"system": 1, "subsystem": 2, "component": 3, "file": 5, "symbol": 5},
    },
}


@dataclass(frozen=True)
class ArchitectureBudget:
    profile: str
    max_nodes: int
    max_edges: int
    max_depth: int
    max_tokens: int
    level_caps: dict[str, int]
    parent_max_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "max_nodes": self.max_nodes,
            "max_edges": self.max_edges,
            "max_depth": self.max_depth,
            "max_tokens": self.max_tokens,
            "level_caps": dict(self.level_caps),
            "parent_max_tokens": self.parent_max_tokens,
        }


def resolve_architecture_budget(
    *,
    profile: str = "overview",
    parent_max_tokens: int | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> ArchitectureBudget:
    base = dict(_PROFILES.get(str(profile or "overview"), _PROFILES["overview"]))
    raw = dict(overrides or {})
    parent = int(parent_max_tokens or 12_000)
    max_tokens = min(int(raw.get("max_tokens") or base["max_tokens"]), max(64, parent))
    return ArchitectureBudget(
        profile=str(profile or "overview"),
        max_nodes=max(1, min(int(raw.get("max_nodes") or base["max_nodes"]), 40)),
        max_edges=max(0, min(int(raw.get("max_edges") or base["max_edges"]), 60)),
        max_depth=max(0, min(int(raw.get("max_depth") or base["max_depth"]), 4)),
        max_tokens=max_tokens,
        level_caps={
            level: max(0, int((raw.get("level_caps") or base["level_caps"]).get(level, 0)))
            for level in LEVELS
        },
        parent_max_tokens=parent,
    )


def apply_architecture_budget(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    budget: ArchitectureBudget,
    estimate_tokens=None,
) -> dict[str, Any]:
    estimator = estimate_tokens or (lambda text: max(1, len(str(text or "")) // 4))
    selected: list[dict[str, Any]] = []
    level_counts: dict[str, int] = {level: 0 for level in LEVELS}
    used_tokens = 0
    reason = ""
    for node in nodes:
        level = str(node.get("level") or "unknown")
        if level in level_counts and level_counts[level] >= int(budget.level_caps.get(level, 0)):
            reason = reason or "node_budget"
            continue
        if len(selected) >= budget.max_nodes:
            reason = "node_budget"
            break
        cost = int(estimator(json_text(node)))
        if used_tokens + cost > budget.max_tokens:
            reason = "token_budget"
            break
        selected.append(node)
        used_tokens += cost
        if level in level_counts:
            level_counts[level] += 1
    selected_ids = {str(node.get("id") or "") for node in selected}
    kept_edges: list[dict[str, Any]] = []
    for edge in edges:
        if len(kept_edges) >= budget.max_edges:
            reason = reason or "edge_budget"
            break
        if str(edge.get("source") or "") in selected_ids and str(edge.get("target") or "") in selected_ids:
            kept_edges.append(edge)
    return {
        "nodes": selected,
        "edges": kept_edges,
        "truncated": bool(reason) or len(nodes) > len(selected) or len(edges) > len(kept_edges),
        "truncation_reason": reason,
        "budget": {
            **budget.as_dict(),
            "used_nodes": len(selected),
            "used_edges": len(kept_edges),
            "used_tokens": used_tokens,
            "level_counts": level_counts,
        },
    }


def json_text(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False)
