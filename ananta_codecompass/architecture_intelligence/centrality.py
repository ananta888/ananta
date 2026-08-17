"""Degree centrality, god-nodes and community bridges."""

from __future__ import annotations

from typing import Any


def compute_centrality(projection: dict[str, Any], communities: list[dict[str, Any]]) -> dict[str, Any]:
    adjacency = {key: list(value) for key, value in dict(projection.get("adjacency") or {}).items()}
    incoming = {key: list(value) for key, value in dict(projection.get("incoming") or {}).items()}
    nodes = [str(item["id"]) for item in projection.get("nodes") or []]
    n = max(1, len(nodes) - 1)
    rows = []
    for node in nodes:
        out_deg = len(adjacency.get(node, []))
        in_deg = len(incoming.get(node, []))
        degree = out_deg + in_deg
        rows.append(
            {
                "node_id": node,
                "in_degree": in_deg,
                "out_degree": out_deg,
                "degree": degree,
                "degree_centrality": round(degree / n, 4),
            }
        )
    rows.sort(key=lambda item: (-item["degree"], item["node_id"]))
    threshold = max(4, int(0.25 * max((item["degree"] for item in rows), default=0)))
    gods = [item["node_id"] for item in rows if item["degree"] >= threshold and item["degree"] >= 3]
    member_of = {}
    for community in communities:
        for member in community["members"]:
            member_of[member] = community["id"]
    bridges = []
    for edge in projection.get("edges") or []:
        left = member_of.get(edge["source"])
        right = member_of.get(edge["target"])
        if left and right and left != right:
            bridges.append(
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "from_community": left,
                    "to_community": right,
                    "relation": edge.get("relation"),
                }
            )
    bridges.sort(key=lambda item: (item["source"], item["target"]))
    return {"centrality": rows, "god_nodes": gods, "bridges": bridges}
