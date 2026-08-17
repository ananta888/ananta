"""Compose a versioned architecture-intelligence result."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ananta_codecompass.architecture_intelligence.centrality import compute_centrality
from ananta_codecompass.architecture_intelligence.community import ALGORITHM, detect_communities
from ananta_codecompass.architecture_intelligence.graph_projection import project_graph
from ananta_codecompass.architecture_intelligence.smells import detect_smells, health_summary


def analyze_architecture(
    records: Mapping[str, Any],
    *,
    snapshot_ref: str = "",
    revision: str = "",
    coverage: str = "partial",
) -> dict[str, Any]:
    projection = project_graph(records)
    communities = detect_communities(projection)
    centrality = compute_centrality(projection, communities)
    smells = detect_smells(projection, centrality)
    fingerprint = hashlib.sha256(
        json.dumps({"alg": ALGORITHM, "nodes": [item["id"] for item in projection["nodes"]], "edges": projection["edges"]}, sort_keys=True).encode()
    ).hexdigest()[:16]
    return {
        "schema": "codecompass.architecture-intelligence.v1",
        "snapshot_ref": snapshot_ref,
        "revision": revision,
        "coverage": coverage if projection["nodes"] else "unknown",
        "algorithm": {**ALGORITHM, "fingerprint": fingerprint},
        "communities": communities,
        "centrality": centrality["centrality"],
        "bridges": centrality["bridges"],
        "smells": smells,
        "health": health_summary(smells, len(projection["nodes"])),
        "diagnostics": {
            "node_count": len(projection["nodes"]),
            "edge_count": len(projection["edges"]),
            "community_count": len(communities),
        },
    }
