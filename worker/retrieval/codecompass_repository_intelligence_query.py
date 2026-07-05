"""RIG-005: Repository Intelligence Graph query engine.

Whitelisted query types (CCRIG-DD-004): no free-form graph query
language in the productive tool.

* ``component-tests``: which tests cover a buildable_component?
* ``package-dependents``: which components depend on an external_package?
* ``runner-coverage``: which buildable_components does a runner cover?
* ``build-target-chain``: from a source file, walk internal ``built_by``
* ``external-package-impact``: components impacted by a package upgrade

Each response carries ``seed_resolution``, ``results``, ``evidence_paths``,
``warnings`` and ``confidence``. Failed/ambiguous evidence is never
silently rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


QUERY_ENGINE_VERSION = "repository_intelligence_query.v1"

ALLOWED_QUERY_TYPES = frozenset({
    "component-tests",
    "package-dependents",
    "runner-coverage",
    "build-target-chain",
    "external-package-impact",
})


@dataclass(frozen=True)
class QueryResult:
    query_type: str
    seed_resolution: dict[str, Any]
    results: tuple[dict[str, Any], ...]
    evidence_paths: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_type": self.query_type,
            "seed_resolution": dict(self.seed_resolution),
            "results": list(self.results),
            "evidence_paths": list(self.evidence_paths),
            "warnings": list(self.warnings),
            "confidence": self.confidence,
        }


def _warnings_for_coverage(graph_store: CodeCompassGraphStore) -> tuple[str, ...]:
    diag = graph_store.load().get("diagnostics") or {}
    ri = diag.get("repository_intelligence") or {}
    if ri.get("status") != "ready":
        return ("repository_intelligence_unavailable",)
    status = ri.get("coverage_status") or "unknown"
    if status == "partial":
        return ("repository_intelligence_partial_coverage",)
    if status == "unknown":
        return ("repository_intelligence_unknown_coverage",)
    return ()


def _neighbours_by_kind(bucket: dict[str, dict[str, list[dict[str, Any]]]],
                        node_id: str, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge_type, edges in (bucket.get(node_id) or {}).items():
        if edge_type != kind:
            continue
        out.extend(edges)
    return out


def run_query(
    *,
    graph_store: CodeCompassGraphStore,
    query_type: str,
    seed: str,
    max_results: int = 100,
) -> QueryResult:
    """Dispatch one whitelisted query."""
    if query_type not in ALLOWED_QUERY_TYPES:
        raise ValueError(
            f"unsupported query_type {query_type!r}; allowed={sorted(ALLOWED_QUERY_TYPES)}"
        )

    payload = graph_store.load()
    rig_nodes_by_id: dict[str, Any] = (payload.get("rig_index") or {}).get("nodes_by_id") or {}
    rig_nodes_list: list[dict[str, Any]] = list(payload.get("rig_nodes") or [])
    rig_edges_list: list[dict[str, Any]] = list(payload.get("rig_edges") or [])

    seed_resolution: dict[str, Any] = {
        "seed": seed,
        "matched_node_ids": [],
        "matched_via": None,
    }
    warnings = list(_warnings_for_coverage(graph_store))

    if not rig_nodes_list and not rig_edges_list:
        return QueryResult(
            query_type=query_type,
            seed_resolution=seed_resolution,
            results=(),
            evidence_paths=(),
            warnings=("repository_intelligence_unavailable",),
            confidence=0.0,
        )

    # Seed resolution: match by id, name, or source_file substring.
    matches: list[str] = []
    if seed in rig_nodes_by_id:
        matches = [seed]
        seed_resolution["matched_via"] = "id"
    else:
        for nid, n in rig_nodes_by_id.items():
            attrs = n.get("attrs") or {}
            name = str(attrs.get("name") or "").strip()
            if name == seed:
                matches.append(nid)
        if matches:
            seed_resolution["matched_via"] = "name"
        else:
            for nid, n in rig_nodes_by_id.items():
                attrs = n.get("attrs") or {}
                files = attrs.get("source_files") or []
                if any(seed in str(f) for f in files):
                    matches.append(nid)
            if matches:
                seed_resolution["matched_via"] = "source_file"
    seed_resolution["matched_node_ids"] = matches[:max_results]

    if not matches:
        return QueryResult(
            query_type=query_type,
            seed_resolution=seed_resolution,
            results=(),
            evidence_paths=(),
            warnings=(*warnings, "seed_not_found"),
            confidence=0.0,
        )

    # Build RIG outgoing/incoming maps once (in-memory; small).
    rig_out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    rig_in: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for edge in rig_edges_list:
        from_id = str(edge.get("from_id") or "")
        to_id = str(edge.get("to_id") or "")
        kind = str(edge.get("kind") or "").strip()
        if not from_id or not to_id or not kind:
            continue
        rig_out.setdefault(from_id, {}).setdefault(kind, []).append(edge)
        rig_in.setdefault(to_id, {}).setdefault(kind, []).append(edge)

    results: list[dict[str, Any]] = []
    evidence: set[str] = set()

    if query_type == "component-tests":
        # Walk tested_by -> runner -> runs -> test. Also accept direct
        # covers edges in either direction.
        for mid in matches:
            for e in _neighbours_by_kind(rig_out, mid, "covers"):
                results.append({"from": mid, "to": e.get("to_id"),
                                "edge_kind": "covers"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)
            for e in _neighbours_by_kind(rig_in, mid, "covers"):
                results.append({"from": e.get("from_id"), "to": mid,
                                "edge_kind": "covers"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)
            for e in _neighbours_by_kind(rig_out, mid, "tested_by"):
                runner_id = str(e.get("to_id") or "")
                if not runner_id:
                    continue
                results.append({"component": mid, "runner": runner_id,
                                "edge_kind": "tested_by"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)
                for e2 in _neighbours_by_kind(rig_out, runner_id, "runs"):
                    test_id = str(e2.get("to_id") or "")
                    if not test_id:
                        continue
                    results.append({"runner": runner_id, "test": test_id,
                                    "edge_kind": "runs"})
                    src2 = (e2.get("evidence") or {}).get("source_file")
                    if src2:
                        evidence.add(src2)

    elif query_type == "package-dependents":
        for mid in matches:
            for e in _neighbours_by_kind(rig_in, mid, "depends_on"):
                results.append({"from": e.get("from_id"), "to": mid,
                                "edge_kind": "depends_on"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)

    elif query_type == "runner-coverage":
        for mid in matches:
            for e in _neighbours_by_kind(rig_out, mid, "runs"):
                results.append({"runner": mid, "test": e.get("to_id"),
                                "edge_kind": "runs"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)
            for e in _neighbours_by_kind(rig_in, mid, "tested_by"):
                results.append({"component": e.get("from_id"), "runner": mid,
                                "edge_kind": "tested_by"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)

    elif query_type == "build-target-chain":
        for mid in matches:
            stack = [(mid, 0)]
            visited: set[str] = set()
            while stack and len(results) < max_results:
                cur, depth = stack.pop(0)
                if cur in visited or depth > 5:
                    continue
                visited.add(cur)
                for e in _neighbours_by_kind(rig_out, cur, "built_by"):
                    nxt = str(e.get("to_id") or "")
                    if nxt and nxt not in visited:
                        results.append({"from": cur, "to": nxt,
                                        "edge_kind": "built_by",
                                        "depth": depth + 1})
                        stack.append((nxt, depth + 1))
                        src = (e.get("evidence") or {}).get("source_file")
                        if src:
                            evidence.add(src)

    elif query_type == "external-package-impact":
        for mid in matches:
            for e in _neighbours_by_kind(rig_in, mid, "depends_on"):
                results.append({"component": e.get("from_id"),
                                "package": mid, "edge_kind": "depends_on"})
                src = (e.get("evidence") or {}).get("source_file")
                if src:
                    evidence.add(src)

    truncated = False
    if len(results) > max_results:
        results = results[:max_results]
        truncated = True
        warnings.append("max_results_truncated")

    return QueryResult(
        query_type=query_type,
        seed_resolution=seed_resolution,
        results=tuple(results),
        evidence_paths=tuple(sorted(evidence)),
        warnings=tuple(warnings) if not truncated else tuple(warnings),
        confidence=0.9 if not warnings else 0.5,
    )


__all__ = [
    "QUERY_ENGINE_VERSION",
    "ALLOWED_QUERY_TYPES",
    "QueryResult",
    "run_query",
]