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

from dataclasses import dataclass
from typing import Any

from ananta_codecompass.graph_store import CodeCompassGraphStore


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


def _scope_filter(
    node: dict[str, Any], edge: dict[str, Any] | None,
    *,
    repository_id: str | None,
    module_id: str | None,
) -> bool:
    """Decide whether a node/edge belongs to the requested scope.

    RIG-010: scopes can be limited by repository_id / module_id. When
    both are None the function is a no-op. A node without the relevant
    attribute is treated as *scope-agnostic* (shared across modules);
    only nodes with the attribute set must match.

    The scope fields are looked up at the node top level *and* under
    ``attrs`` because RIG-001 / DD-014 store them in ``attrs``.
    """
    if repository_id is None and module_id is None:
        return True

    def _attr(node: dict[str, Any], key: str) -> str:
        v = str(node.get(key) or "").strip()
        if v:
            return v
        attrs = node.get("attrs") or {}
        if isinstance(attrs, dict):
            return str(attrs.get(key) or "").strip()
        return ""

    node_repo = _attr(node, "repository_id")
    node_mod = _attr(node, "module_id")
    if repository_id is not None and node_repo and node_repo != repository_id:
        return False
    if module_id is not None and node_mod and node_mod != module_id:
        return False
    return True


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
    repository_id: str | None = None,
    module_id: str | None = None,
    cross_scope: bool = False,
) -> QueryResult:
    """Dispatch one whitelisted query.

    RIG-010: ``repository_id`` / ``module_id`` limit the query to one
    scope. ``cross_scope=True`` overrides that filter (explicit opt-in).
    External-package nodes are deduplicated across modules, but evidence
    per module is preserved.
    """
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
        "scope": {"repository_id": repository_id, "module_id": module_id,
                  "cross_scope": cross_scope},
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
    # Seed resolution is intentionally *not* scope-filtered: if the user
    # asks about "ep:fmt" we want to find it regardless of whether the
    # seed node itself has repository_id / module_id annotations.
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

    def _in_scope(node_id: str) -> bool:
        if cross_scope:
            return True
        node = rig_nodes_by_id.get(node_id) or {}
        return _scope_filter(node, None,
                             repository_id=repository_id,
                             module_id=module_id)

    results: list[dict[str, Any]] = []
    evidence: set[str] = set()

    def _push_evidence(edge: dict[str, Any]) -> None:
        src = (edge.get("evidence") or {}).get("source_file")
        if src:
            evidence.add(src)

    if query_type == "component-tests":
        # Walk tested_by -> runner -> runs -> test. Also accept direct
        # covers edges in either direction.
        for mid in matches:
            if not _in_scope(mid):
                continue
            for e in _neighbours_by_kind(rig_out, mid, "covers"):
                if not _in_scope(str(e.get("to_id") or "")):
                    continue
                results.append({"from": mid, "to": e.get("to_id"),
                                "edge_kind": "covers"})
                _push_evidence(e)
            for e in _neighbours_by_kind(rig_in, mid, "covers"):
                if not _in_scope(str(e.get("from_id") or "")):
                    continue
                results.append({"from": e.get("from_id"), "to": mid,
                                "edge_kind": "covers"})
                _push_evidence(e)
            for e in _neighbours_by_kind(rig_out, mid, "tested_by"):
                runner_id = str(e.get("to_id") or "")
                if not runner_id or not _in_scope(runner_id):
                    continue
                results.append({"component": mid, "runner": runner_id,
                                "edge_kind": "tested_by"})
                _push_evidence(e)
                for e2 in _neighbours_by_kind(rig_out, runner_id, "runs"):
                    test_id = str(e2.get("to_id") or "")
                    if not test_id or not _in_scope(test_id):
                        continue
                    results.append({"runner": runner_id, "test": test_id,
                                    "edge_kind": "runs"})
                    _push_evidence(e2)

    elif query_type == "package-dependents":
        for mid in matches:
            if not _in_scope(mid):
                continue
            for e in _neighbours_by_kind(rig_in, mid, "depends_on"):
                comp = str(e.get("from_id") or "")
                if not _in_scope(comp):
                    continue
                results.append({"from": comp, "to": mid,
                                "edge_kind": "depends_on"})
                _push_evidence(e)

    elif query_type == "runner-coverage":
        for mid in matches:
            if not _in_scope(mid):
                continue
            for e in _neighbours_by_kind(rig_out, mid, "runs"):
                test_id = str(e.get("to_id") or "")
                if not _in_scope(test_id):
                    continue
                results.append({"runner": mid, "test": test_id,
                                "edge_kind": "runs"})
                _push_evidence(e)
            for e in _neighbours_by_kind(rig_in, mid, "tested_by"):
                comp = str(e.get("from_id") or "")
                if not _in_scope(comp):
                    continue
                results.append({"component": comp, "runner": mid,
                                "edge_kind": "tested_by"})
                _push_evidence(e)

    elif query_type == "build-target-chain":
        for mid in matches:
            if not _in_scope(mid):
                continue
            stack = [(mid, 0)]
            visited: set[str] = set()
            while stack and len(results) < max_results:
                cur, depth = stack.pop(0)
                if cur in visited or depth > 5:
                    continue
                visited.add(cur)
                for e in _neighbours_by_kind(rig_out, cur, "built_by"):
                    nxt = str(e.get("to_id") or "")
                    if nxt and nxt not in visited and _in_scope(nxt):
                        results.append({"from": cur, "to": nxt,
                                        "edge_kind": "built_by",
                                        "depth": depth + 1})
                        stack.append((nxt, depth + 1))
                        _push_evidence(e)

    elif query_type == "external-package-impact":
        seen_packages: set[str] = set()
        for mid in matches:
            if not _in_scope(mid):
                continue
            for e in _neighbours_by_kind(rig_in, mid, "depends_on"):
                comp = str(e.get("from_id") or "")
                if not _in_scope(comp):
                    continue
                # External-package nodes are deduplicated but evidence
                # per module is preserved (RIG-010 acceptance).
                if mid in seen_packages:
                    continue
                seen_packages.add(mid)
                results.append({"component": comp,
                                "package": mid, "edge_kind": "depends_on"})
                _push_evidence(e)

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
