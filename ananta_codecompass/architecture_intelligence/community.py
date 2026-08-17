"""Deterministic label-propagation communities (Leiden-compatible port)."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any


ALGORITHM = {"name": "label_propagation", "version": "1", "parameters": {"iterations": 8}}


def detect_communities(projection: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [str(item["id"]) for item in projection.get("nodes") or []]
    adjacency = {key: list(value) for key, value in dict(projection.get("adjacency") or {}).items()}
    incoming = {key: list(value) for key, value in dict(projection.get("incoming") or {}).items()}
    labels = {node: node for node in nodes}
    for _ in range(8):
        changed = False
        for node in nodes:
            neighbors = adjacency.get(node, []) + incoming.get(node, [])
            if not neighbors:
                continue
            counts = Counter(labels[neighbor] for neighbor in neighbors if neighbor in labels)
            best = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break
    groups: dict[str, list[str]] = {}
    for node, label in labels.items():
        groups.setdefault(label, []).append(node)
    communities = []
    for members in groups.values():
        members = sorted(members)
        fingerprint = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()[:16]
        seed = members[0]
        communities.append(
            {
                "id": f"c-{fingerprint}",
                "fingerprint": fingerprint,
                "label": seed,
                "members": members,
                "size": len(members),
            }
        )
    communities.sort(key=lambda item: (-item["size"], item["id"]))
    return communities
