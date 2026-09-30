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


def _resolve_seed(seed: str, rig_nodes_by_id: dict[str, Any]) -> tuple[list[str], str | None]:
    """Match a seed by node id, then by name, then by source-file substring.

    Seed resolution is intentionally *not* scope-filtered: if the user asks
    about "ep:fmt" we want to find it regardless of whether the seed node
    itself has repository_id / module_id annotations.
    """
    if seed in rig_nodes_by_id:
        return [seed], "id"
    matches = [
        nid
        for nid, n in rig_nodes_by_id.items()
        if str((n.get("attrs") or {}).get("name") or "").strip() == seed
    ]
    if matches:
        return matches, "name"
    matches = [
        nid
        for nid, n in rig_nodes_by_id.items()
        if any(seed in str(f) for f in ((n.get("attrs") or {}).get("source_files") or []))
    ]
    return matches, ("source_file" if matches else None)


_EdgeMap = dict[str, dict[str, list[dict[str, Any]]]]


def _edge_maps(rig_edges_list: list[dict[str, Any]]) -> tuple[_EdgeMap, _EdgeMap]:
    """Build the RIG outgoing/incoming maps once (in-memory; small)."""

    rig_out: _EdgeMap = {}
    rig_in: _EdgeMap = {}
    for edge in rig_edges_list:
        from_id = str(edge.get("from_id") or "")
        to_id = str(edge.get("to_id") or "")
        kind = str(edge.get("kind") or "").strip()
        if not from_id or not to_id or not kind:
            continue
        rig_out.setdefault(from_id, {}).setdefault(kind, []).append(edge)
        rig_in.setdefault(to_id, {}).setdefault(kind, []).append(edge)
    return rig_out, rig_in


@dataclass
class _RigQueryContext:
    """Scoped graph view plus the result/evidence accumulators of one query."""

    rig_nodes_by_id: dict[str, Any]
    rig_out: _EdgeMap
    rig_in: _EdgeMap
    repository_id: str | None
    module_id: str | None
    cross_scope: bool
    max_results: int
    results: list[dict[str, Any]] = field(default_factory=list)
    evidence: set[str] = field(default_factory=set)

    def in_scope(self, node_id: str) -> bool:
        if self.cross_scope:
            return True
        node = self.rig_nodes_by_id.get(node_id) or {}
        return _scope_filter(node, None, repository_id=self.repository_id, module_id=self.module_id)

    def outgoing(self, node_id: str, kind: str) -> list[dict[str, Any]]:
        return _neighbours_by_kind(self.rig_out, node_id, kind)

    def incoming(self, node_id: str, kind: str) -> list[dict[str, Any]]:
        return _neighbours_by_kind(self.rig_in, node_id, kind)

    def add(self, result: dict[str, Any], edge: dict[str, Any]) -> None:
        self.results.append(result)
        src = (edge.get("evidence") or {}).get("source_file")
        if src:
            self.evidence.add(src)


def _add_runner_tests(ctx: _RigQueryContext, runner_id: str, *, require_test_id: bool) -> None:
    for e in ctx.outgoing(runner_id, "runs"):
        test_id = str(e.get("to_id") or "")
        if (require_test_id and not test_id) or not ctx.in_scope(test_id):
            continue
        ctx.add({"runner": runner_id, "test": test_id, "edge_kind": "runs"}, e)


def _query_component_tests(ctx: _RigQueryContext, matches: list[str]) -> None:
    # Walk tested_by -> runner -> runs -> test. Also accept direct
    # covers edges in either direction.
    for mid in matches:
        if not ctx.in_scope(mid):
            continue
        for e in ctx.outgoing(mid, "covers"):
            if ctx.in_scope(str(e.get("to_id") or "")):
                ctx.add({"from": mid, "to": e.get("to_id"), "edge_kind": "covers"}, e)
        for e in ctx.incoming(mid, "covers"):
            if ctx.in_scope(str(e.get("from_id") or "")):
                ctx.add({"from": e.get("from_id"), "to": mid, "edge_kind": "covers"}, e)
        for e in ctx.outgoing(mid, "tested_by"):
            runner_id = str(e.get("to_id") or "")
            if not runner_id or not ctx.in_scope(runner_id):
                continue
            ctx.add({"component": mid, "runner": runner_id, "edge_kind": "tested_by"}, e)
            _add_runner_tests(ctx, runner_id, require_test_id=True)


def _query_package_dependents(ctx: _RigQueryContext, matches: list[str]) -> None:
    for mid in matches:
        if not ctx.in_scope(mid):
            continue
        for e in ctx.incoming(mid, "depends_on"):
            comp = str(e.get("from_id") or "")
            if ctx.in_scope(comp):
                ctx.add({"from": comp, "to": mid, "edge_kind": "depends_on"}, e)


def _query_runner_coverage(ctx: _RigQueryContext, matches: list[str]) -> None:
    for mid in matches:
        if not ctx.in_scope(mid):
            continue
        _add_runner_tests(ctx, mid, require_test_id=False)
        for e in ctx.incoming(mid, "tested_by"):
            comp = str(e.get("from_id") or "")
            if ctx.in_scope(comp):
                ctx.add({"component": comp, "runner": mid, "edge_kind": "tested_by"}, e)


def _walk_built_by(ctx: _RigQueryContext, start: str) -> None:
    stack = [(start, 0)]
    visited: set[str] = set()
    while stack and len(ctx.results) < ctx.max_results:
        cur, depth = stack.pop(0)
        if cur in visited or depth > 5:
            continue
        visited.add(cur)
        for e in ctx.outgoing(cur, "built_by"):
            nxt = str(e.get("to_id") or "")
            if nxt and nxt not in visited and ctx.in_scope(nxt):
                ctx.add({"from": cur, "to": nxt, "edge_kind": "built_by", "depth": depth + 1}, e)
                stack.append((nxt, depth + 1))


def _query_build_target_chain(ctx: _RigQueryContext, matches: list[str]) -> None:
    for mid in matches:
        if ctx.in_scope(mid):
            _walk_built_by(ctx, mid)


def _query_external_package_impact(ctx: _RigQueryContext, matches: list[str]) -> None:
    seen_packages: set[str] = set()
    for mid in matches:
        if not ctx.in_scope(mid):
            continue
        for e in ctx.incoming(mid, "depends_on"):
            comp = str(e.get("from_id") or "")
            if not ctx.in_scope(comp):
                continue
            # External-package nodes are deduplicated but evidence
            # per module is preserved (RIG-010 acceptance).
            if mid in seen_packages:
                continue
            seen_packages.add(mid)
            ctx.add({"component": comp, "package": mid, "edge_kind": "depends_on"}, e)


# One strategy per whitelisted query type (OCP: a new type adds an entry).
_QUERY_STRATEGIES = {
    "component-tests": _query_component_tests,
    "package-dependents": _query_package_dependents,
    "runner-coverage": _query_runner_coverage,
    "build-target-chain": _query_build_target_chain,
    "external-package-impact": _query_external_package_impact,
}


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

    matches, matched_via = _resolve_seed(seed, rig_nodes_by_id)
    seed_resolution["matched_via"] = matched_via
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

    rig_out, rig_in = _edge_maps(rig_edges_list)
    ctx = _RigQueryContext(
        rig_nodes_by_id=rig_nodes_by_id,
        rig_out=rig_out,
        rig_in=rig_in,
        repository_id=repository_id,
        module_id=module_id,
        cross_scope=cross_scope,
        max_results=max_results,
    )
    _QUERY_STRATEGIES[query_type](ctx, matches)

    results = ctx.results
    if len(results) > max_results:
        results = results[:max_results]
        warnings.append("max_results_truncated")

    return QueryResult(
        query_type=query_type,
        seed_resolution=seed_resolution,
        results=tuple(results),
        evidence_paths=tuple(sorted(ctx.evidence)),
        warnings=tuple(warnings),
        confidence=0.9 if not warnings else 0.5,
    )


__all__ = [
    "QUERY_ENGINE_VERSION",
    "ALLOWED_QUERY_TYPES",
    "QueryResult",
    "run_query",
]
