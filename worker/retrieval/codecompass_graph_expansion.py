from __future__ import annotations

from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

_PROFILE_CONFIG = {
    "bugfix_local": {
        "allowed_edge_types": {
            "calls_probable_target",
            "injects_dependency",
            "field_type_uses",
            "extends",
            "implements",
            "child_of_type",
            "child_of_file",
        },
        "max_depth": 2,
        "max_nodes": 20,
        "per_kind_budget": {"java_method": 10, "java_type": 8, "config": 6, "xml_tag": 6},
    },
    "refactor_navigation": {
        "allowed_edge_types": {
            "declares_method",
            "calls_probable_target",
            "extends",
            "implements",
            "child_of_type",
            "child_of_file",
        },
        "max_depth": 3,
        "max_nodes": 30,
        "per_kind_budget": {"java_method": 14, "java_type": 12, "config": 6, "xml_tag": 6},
    },
    "architecture_review": {
        "allowed_edge_types": {
            "injects_dependency",
            "declares_bean",
            "transactional_boundary",
            "extends",
            "implements",
            "jpa_relation",
            "field_type_uses",
            "calls_probable_target",
        },
        "max_depth": 3,
        "max_nodes": 40,
        "per_kind_budget": {"java_method": 12, "java_type": 16, "config": 10, "xml_tag": 8},
    },
    "config_integration": {
        "allowed_edge_types": {
            "declares_bean",
            "transactional_boundary",
            "child_of_file",
            "child_of_type",
            "injects_dependency",
            "jpa_relation",
        },
        "max_depth": 2,
        "max_nodes": 24,
        "per_kind_budget": {"config": 14, "xml_tag": 10, "java_type": 8, "java_method": 6},
    },
}


def expand_codecompass_graph(
    *,
    store: CodeCompassGraphStore,
    seed_node_ids: list[str],
    profile: str,
) -> dict[str, Any]:
    profile_name = str(profile or "bugfix_local").strip().lower() or "bugfix_local"
    cfg = dict(_PROFILE_CONFIG.get(profile_name) or _PROFILE_CONFIG["bugfix_local"])
    traversal = store.traverse(
        seed_ids=[str(item).strip() for item in list(seed_node_ids or []) if str(item).strip()],
        max_depth=int(cfg["max_depth"]),
        max_nodes=int(cfg["max_nodes"]),
        allowed_edge_types={str(item) for item in set(cfg["allowed_edge_types"])},
    )
    per_kind_budget = dict(cfg["per_kind_budget"] or {})
    selected_nodes: list[dict[str, Any]] = []
    selected_node_ids: set[str] = set()
    by_kind_counts: dict[str, int] = {}
    for node in list(traversal.get("nodes") or []):
        node_id = str(node.get("id") or "").strip()
        if not node_id or node_id in selected_node_ids:
            continue
        kind = str(node.get("kind") or "unknown").strip().lower() or "unknown"
        count = int(by_kind_counts.get(kind) or 0)
        budget = int(per_kind_budget.get(kind) or max(1, int(cfg["max_nodes"])))
        if count >= budget:
            continue
        by_kind_counts[kind] = count + 1
        selected_node_ids.add(node_id)
        selected_nodes.append(dict(node))

    selected_paths = []
    for row in list(traversal.get("paths") or []):
        node_id = str((row or {}).get("node_id") or "").strip()
        if node_id in selected_node_ids:
            selected_paths.append(dict(row))
    selected_paths.sort(key=lambda item: str(item.get("node_id") or ""))
    return {
        "profile": profile_name,
        "seed_node_ids": sorted({str(item).strip() for item in list(seed_node_ids or []) if str(item).strip()}),
        "nodes": selected_nodes,
        "paths": selected_paths,
        "allowed_edge_types": sorted(str(item) for item in set(cfg["allowed_edge_types"])),
        "max_depth": int(cfg["max_depth"]),
        "max_nodes": int(cfg["max_nodes"]),
        "per_kind_budget": per_kind_budget,
        "by_kind_counts": by_kind_counts,
        "deterministic": True,
        "bounded": bool(traversal.get("bounded")),
    }

