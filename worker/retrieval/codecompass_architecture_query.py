"""Deterministic architecture query engine on top of CodeCompassGraphStore.

CCAQE: answers typed architecture questions (dto-impact, controller-test-coverage,
field-policy-impact, service-dependency-chain) with evidence paths instead of bare
hits. FTS acts as seed resolver, graph traversal as proof; heuristic edges are
flagged, never sold as hard truth. Query types are whitelisted — no free-form
graph query language is executed (CCAQE-DD-003).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

RESULT_SCHEMA = "codecompass_architecture_query_result.v1"

HEURISTIC_EDGE_TYPES = {
    "calls_probable_target",
    "test_calls_endpoint",
}

_EDGE_TYPE_WEIGHTS: dict[str, float] = {
    "field_type_uses": 1.0,
    "method_param_type_uses": 1.0,
    "method_return_type_uses": 1.0,
    "generic_type_uses": 0.95,
    "mapper_maps_type": 0.95,
    "extends": 1.0,
    "implements": 1.0,
    "injects_dependency": 0.95,
    "constructor_injection": 0.95,
    "declares_bean": 0.9,
    "service_uses_repository": 0.95,
    "repository_handles_entity": 0.95,
    "transactional_boundary": 0.9,
    "test_targets_type": 1.0,
    "test_uses_controller": 0.95,
    "test_calls_endpoint": 0.8,
    "controller_endpoint_declares": 0.95,
    "mock_injects_dependency": 0.9,
    "policy_applies_to_field": 1.0,
    "permission_checks_field": 1.0,
    "interceptor_guards_method": 0.95,
    "frontend_guard_refs_field": 0.9,
    "role_allows_operation": 0.95,
    "calls_probable_target": 0.5,
}
_DEFAULT_EDGE_WEIGHT = 0.7
_DEPTH_DECAY = 0.85

_QUERY_CONFIG: dict[str, dict[str, Any]] = {
    "dto-impact": {
        "direction": "incoming",
        "default_depth": 3,
        "primary_edge_types": {
            "field_type_uses",
            "method_param_type_uses",
            "method_return_type_uses",
            "generic_type_uses",
            "mapper_maps_type",
        },
        "secondary_edge_types": {
            "injects_dependency",
            "constructor_injection",
            "calls_probable_target",
            "service_uses_repository",
            "repository_handles_entity",
            "child_of_type",
        },
    },
    "controller-test-coverage": {
        "direction": "incoming",
        "default_depth": 3,
        "primary_edge_types": {
            "test_targets_type",
            "test_uses_controller",
            "test_calls_endpoint",
        },
        "secondary_edge_types": {
            "controller_endpoint_declares",
            "calls_probable_target",
            "mock_injects_dependency",
            "injects_dependency",
            "child_of_type",
        },
        "result_role_filter": {"test"},
    },
    "field-policy-impact": {
        "direction": "incoming",
        "default_depth": 3,
        "primary_edge_types": {
            "policy_applies_to_field",
            "permission_checks_field",
            "interceptor_guards_method",
            "frontend_guard_refs_field",
            "role_allows_operation",
        },
        "secondary_edge_types": {
            "child_of_type",
            "calls_probable_target",
        },
    },
    "service-dependency-chain": {
        "direction": "outgoing",
        "default_depth": 3,
        "primary_edge_types": {
            "injects_dependency",
            "constructor_injection",
            "declares_bean",
            "service_uses_repository",
            "transactional_boundary",
        },
        "secondary_edge_types": {
            "calls_probable_target",
            "child_of_type",
        },
    },
}

VALID_QUERY_TYPES = sorted(_QUERY_CONFIG)


@dataclass(frozen=True)
class QueryLimits:
    """CCAQE-008: hard execution bounds; all query types are clamped to these."""

    max_depth: int = 4
    max_nodes: int = 200
    max_results: int = 25
    max_paths_per_result: int = 3


def classify_result_role(node: dict[str, Any]) -> str:
    """CCAQE-016: role classification — explicit role_labels win over heuristics.

    Test detection runs first because role_labels never carry a 'test' label.
    """
    record = dict(node.get("source_record") or {})
    name = str(node.get("name") or "").strip()
    file = str(node.get("file") or "").lower()
    kind = str(node.get("kind") or "").strip().lower()
    annotations = [str(item) for item in list(record.get("annotations") or [])]

    if (
        name.endswith(("Test", "Tests", "IT"))
        or "/test/" in file
        or "/tests/" in file
        or file.startswith(("test/", "tests/"))
        or kind.startswith("test")
    ):
        return "test"

    labels = {str(item).strip().lower() for item in list(record.get("role_labels") or node.get("role_labels") or []) if str(item).strip()}
    for role in ("controller", "service", "repository", "mapper", "entity", "config", "dto"):
        if role in labels:
            return role

    def has_annotation(*prefixes: str) -> bool:
        return any(annotation.startswith(prefix) for annotation in annotations for prefix in prefixes)

    if has_annotation("@Controller", "@RestController") or name.endswith("Controller"):
        return "controller"
    if has_annotation("@Repository") or name.endswith(("Repository", "Dao")):
        return "repository"
    if has_annotation("@Mapper") or name.endswith("Mapper"):
        return "mapper"
    if has_annotation("@Service") or name.endswith(("Service", "ServiceImpl")):
        return "service"
    if name.endswith(("Policy", "Permission", "Guard", "Interceptor")) or kind in {"policy", "permission"}:
        return "policy"
    if has_annotation("@Configuration") or name.endswith(("Config", "Configuration")) or kind in {"config", "xml_tag", "xml_file"}:
        return "config"
    if has_annotation("@Entity", "@Embeddable", "@MappedSuperclass"):
        return "entity"
    if name.endswith(("Dto", "DTO", "Request", "Response")):
        return "dto"
    return kind or "other"


def resolve_seed(
    *,
    store: CodeCompassGraphStore,
    seed: str,
    field: str | None = None,
    fts_search: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """CCAQE-005: resolve user input (node id, record id, name, path, FTS) to graph nodes.

    Resolution order: node_id exact, record_id exact, name exact, name case-insensitive,
    file/path fragment, FTS fallback. Never hallucinates: unresolved seeds return an
    empty candidate list plus a seed_not_resolved warning.
    """
    query = str(seed or "").strip()
    field_name = str(field or "").strip() or None
    resolution: dict[str, Any] = {
        "input": query,
        "field": field_name,
        "resolved_node_ids": [],
        "candidates": [],
        "warnings": [],
    }
    if not query:
        resolution["warnings"].append("seed_not_resolved")
        return resolution

    payload = store.load()
    node_index = dict(payload.get("node_index") or {})
    by_id = dict(node_index.get("by_id") or {})
    by_record_id = dict(node_index.get("by_record_id") or {})

    candidates: list[dict[str, Any]] = []

    if query in by_id:
        candidates.append({"node_id": query, "score": 1.0, "reason": "node_id_exact"})
    if not candidates:
        for node_id in sorted(set(by_record_id.get(query) or [])):
            if node_id in by_id:
                candidates.append({"node_id": node_id, "score": 0.98, "reason": "record_id_exact"})
    if not candidates and field_name:
        for node in store.find_nodes_by_name(name=f"{query}.{field_name}"):
            candidates.append({"node_id": str(node.get("id")), "score": 0.97, "reason": "field_node_exact"})
    if not candidates:
        by_name = dict(node_index.get("by_name") or {})
        exact_ids = sorted(set(by_name.get(query) or []))
        if exact_ids:
            for node_id in exact_ids:
                if node_id in by_id:
                    candidates.append({"node_id": node_id, "score": 0.95, "reason": "name_exact"})
        else:
            for node in store.find_nodes_by_name(name=query):
                candidates.append({"node_id": str(node.get("id")), "score": 0.9, "reason": "name_case_insensitive"})
    if not candidates:
        for node in store.find_nodes_by_file(file=query):
            candidates.append({"node_id": str(node.get("id")), "score": 0.8, "reason": "file_fragment"})
    if not candidates and fts_search is not None:
        try:
            fts_rows = list(fts_search(query) or [])
        except Exception:
            fts_rows = []
        for row in fts_rows[:5]:
            record_id = str((row or {}).get("record_id") or "").strip()
            for node_id in sorted(set(by_record_id.get(record_id) or [])):
                if node_id in by_id:
                    candidates.append({"node_id": node_id, "score": 0.6, "reason": "fts_fallback"})

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        node_id = candidate["node_id"]
        if node_id in seen:
            continue
        seen.add(node_id)
        deduped.append(candidate)

    resolution["candidates"] = deduped
    resolution["resolved_node_ids"] = [candidate["node_id"] for candidate in deduped]
    if not deduped:
        resolution["warnings"].append("seed_not_resolved")
    elif len(deduped) > 1:
        resolution["warnings"].append("ambiguous_seed")
    return resolution


def _edge_weight(edge_type: str) -> float:
    return _EDGE_TYPE_WEIGHTS.get(str(edge_type or "").strip().lower(), _DEFAULT_EDGE_WEIGHT)


def score_evidence_path(edges: list[dict[str, Any]]) -> float:
    """CCAQE-006: shorter paths and hard edges beat long heuristic chains."""
    if not edges:
        return 0.0
    score = 1.0
    for edge in edges:
        confidence = float(edge.get("confidence") or 1.0)
        score *= max(0.0, min(1.0, confidence)) * _edge_weight(str(edge.get("edge_type") or ""))
    score *= _DEPTH_DECAY ** max(0, len(edges) - 1)
    return round(score, 6)


def _path_uses_only_heuristics(edges: list[dict[str, Any]]) -> bool:
    return bool(edges) and all(str(edge.get("edge_type") or "") in HEURISTIC_EDGE_TYPES for edge in edges)


def _path_matches_field(edges: list[dict[str, Any]], field_name: str | None) -> bool:
    if not field_name:
        return True
    field_edges = [edge for edge in edges if edge.get("field") is not None]
    if not field_edges:
        return True
    return any(str(edge.get("field") or "").strip().lower() == field_name.lower() for edge in field_edges)


def _classify_coverage_kind(edges: list[dict[str, Any]]) -> str:
    edge_types = {str(edge.get("edge_type") or "") for edge in edges}
    if len(edges) == 1 and edge_types & {"test_targets_type", "test_uses_controller"}:
        return "direct_controller_test"
    if "test_calls_endpoint" in edge_types:
        return "endpoint_test"
    if _path_uses_only_heuristics(edges):
        return "suspected_coverage"
    return "indirect_evidence"


def _classify_enforcement(edges: list[dict[str, Any]], seed_reasons: set[str]) -> str:
    edge_types = {str(edge.get("edge_type") or "") for edge in edges}
    if "frontend_guard_refs_field" in edge_types:
        return "frontend_reference"
    if edge_types & {"interceptor_guards_method", "permission_checks_field", "policy_applies_to_field"}:
        return "enforced_backend_guard"
    if "role_allows_operation" in edge_types:
        return "enforced_backend_guard"
    if seed_reasons & {"fts_fallback", "name_case_insensitive"}:
        return "weak_reference"
    return "weak_reference"


def _collect_operations(edges: list[dict[str, Any]]) -> list[str]:
    operations = sorted({
        str(edge.get("operation") or "").strip().lower()
        for edge in edges
        if str(edge.get("operation") or "").strip()
    })
    return operations


def run_architecture_query(
    *,
    store: CodeCompassGraphStore,
    query_type: str,
    seed: str,
    field: str | None = None,
    depth: int | None = None,
    direction: str | None = None,
    limits: QueryLimits | None = None,
    fts_search: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    bounds = limits or QueryLimits()
    query_name = str(query_type or "").strip().lower()
    base_result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "query_type": query_name,
        "seed": {"input": str(seed or "").strip(), "field": str(field or "").strip() or None, "resolved_node_ids": []},
        "results": [],
        "diagnostics": {
            "bounded": True,
            "applied_limits": {
                "max_depth": bounds.max_depth,
                "max_nodes": bounds.max_nodes,
                "max_results": bounds.max_results,
                "max_paths_per_result": bounds.max_paths_per_result,
            },
        },
        "warnings": [],
    }
    if query_name not in _QUERY_CONFIG:
        base_result["error"] = "invalid_query_type"
        base_result["valid_query_types"] = VALID_QUERY_TYPES
        base_result["warnings"].append("invalid_query_type")
        return base_result

    config = _QUERY_CONFIG[query_name]
    requested_depth = int(depth) if depth is not None else int(config["default_depth"])
    effective_depth = max(1, min(requested_depth, bounds.max_depth))
    if requested_depth > bounds.max_depth:
        base_result["warnings"].append("depth_clamped_to_max")
    direction_name = str(direction or config["direction"]).strip().lower()
    if direction_name not in {"outgoing", "incoming", "both"}:
        direction_name = str(config["direction"])

    resolution = resolve_seed(store=store, seed=seed, field=field, fts_search=fts_search)
    base_result["seed"] = {
        "input": resolution["input"],
        "field": resolution["field"],
        "resolved_node_ids": resolution["resolved_node_ids"],
        "candidates": resolution["candidates"],
    }
    base_result["warnings"].extend(resolution["warnings"])
    base_result["diagnostics"].update({
        "query_direction": direction_name,
        "depth_used": effective_depth,
    })
    if not resolution["resolved_node_ids"]:
        return base_result

    seed_reasons = {str(candidate.get("reason") or "") for candidate in resolution["candidates"]}
    allowed_edge_types = set(config["primary_edge_types"]) | set(config["secondary_edge_types"])
    traversal = store.traverse_paths(
        seed_ids=list(resolution["resolved_node_ids"]),
        max_depth=effective_depth,
        max_nodes=bounds.max_nodes,
        allowed_edge_types=allowed_edge_types,
        direction=direction_name,
        max_paths_per_node=bounds.max_paths_per_result,
    )
    base_result["diagnostics"].update({
        "traversed_nodes": len(list(traversal.get("nodes") or [])),
        "expansions": int(traversal.get("expansions") or 0),
        "truncated": bool(traversal.get("truncated")),
        "cycle_count": int(traversal.get("cycle_count") or 0),
    })
    if traversal.get("truncated"):
        base_result["warnings"].append("traversal_truncated_by_limits")
    if query_name == "service-dependency-chain" and int(traversal.get("cycle_count") or 0) > 0:
        base_result["diagnostics"]["service_dependency_cycles_detected"] = int(traversal.get("cycle_count") or 0)

    field_name = resolution["field"]
    node_lookup = {str(node.get("id") or ""): node for node in list(traversal.get("nodes") or [])}
    heuristic_edge_used = False
    results: list[dict[str, Any]] = []
    role_filter = set(config.get("result_role_filter") or set())

    for row in list(traversal.get("paths") or []):
        node_id = str(row.get("node_id") or "")
        node = node_lookup.get(node_id)
        if not node:
            continue
        scored_paths = []
        for entry in list(row.get("evidence_paths") or []):
            edges = [dict(edge) for edge in list(entry.get("edges") or [])]
            if not _path_matches_field(edges, field_name):
                continue
            scored_paths.append({
                "path_score": score_evidence_path(edges),
                "edges": edges,
            })
        if not scored_paths:
            continue
        scored_paths.sort(key=lambda item: (-float(item["path_score"]), str(item["edges"][0].get("edge_type") or "") if item["edges"] else ""))
        scored_paths = scored_paths[: bounds.max_paths_per_result]
        best_path = scored_paths[0]
        best_edges = list(best_path["edges"])
        result_role = classify_result_role(node)
        if role_filter and result_role not in role_filter:
            continue

        result_warnings: list[str] = []
        only_heuristics = all(_path_uses_only_heuristics(list(entry["edges"])) for entry in scored_paths)
        if only_heuristics:
            result_warnings.append("heuristic_evidence_only")
        if any(str(edge.get("edge_type") or "") in HEURISTIC_EDGE_TYPES for entry in scored_paths for edge in entry["edges"]):
            heuristic_edge_used = True

        result_entry: dict[str, Any] = {
            "result_node_id": node_id,
            "result_kind": str(node.get("kind") or "unknown"),
            "result_role": result_role,
            "score": float(best_path["path_score"]),
            "depth": int(row.get("depth") or len(best_edges)),
            "evidence_paths": scored_paths,
            "warnings": result_warnings,
        }
        if query_name == "controller-test-coverage":
            coverage_kind = _classify_coverage_kind(best_edges)
            if only_heuristics:
                coverage_kind = "suspected_coverage"
            result_entry["coverage_kind"] = coverage_kind
            if coverage_kind in {"suspected_coverage", "indirect_evidence"}:
                result_entry["warnings"].append("no_direct_test_evidence")
        elif query_name == "field-policy-impact":
            result_entry["enforcement"] = _classify_enforcement(best_edges, seed_reasons)
            operations = _collect_operations([edge for entry in scored_paths for edge in entry["edges"]])
            if operations:
                result_entry["operations"] = operations
        elif query_name == "service-dependency-chain":
            direct = len(best_edges) == 1 and str(best_edges[0].get("edge_type") or "") in {
                "injects_dependency",
                "constructor_injection",
                "declares_bean",
                "service_uses_repository",
            }
            result_entry["dependency_kind"] = "direct_dependency" if direct else "indirect_dependency"
            boundary_edges = [
                str(edge.get("edge_type") or "")
                for entry in scored_paths
                for edge in entry["edges"]
                if str(edge.get("edge_type") or "") == "transactional_boundary"
            ]
            if boundary_edges:
                result_entry["transactional_boundary"] = True
        results.append(result_entry)

    results.sort(key=lambda item: (-float(item["score"]), str(item["result_node_id"])))
    if len(results) > bounds.max_results:
        results = results[: bounds.max_results]
        base_result["warnings"].append("results_truncated_by_max_results")
    base_result["results"] = results
    if heuristic_edge_used:
        base_result["warnings"].append("calls_probable_target edges are heuristic")
    if not results and "seed_not_resolved" not in base_result["warnings"]:
        base_result["warnings"].append("no_evidence_found")
    return base_result


def render_query_result_markdown(result: dict[str, Any], *, max_results: int = 10) -> str:
    """CCAQE-019: compact markdown handoff for agents.

    Evidence and warnings are always included; empty results are rendered as
    'not found / not proven', never as success. Security warnings are never
    filtered out.
    """
    query_type = str(result.get("query_type") or "unknown")
    seed = dict(result.get("seed") or {})
    seed_input = str(seed.get("input") or "")
    field_name = seed.get("field")
    warnings = [str(item) for item in list(result.get("warnings") or [])]
    results = list(result.get("results") or [])

    lines: list[str] = []
    title = f"## CodeCompass Architecture Query: {query_type}"
    lines.append(title)
    seed_line = f"**Seed:** `{seed_input}`"
    if field_name:
        seed_line += f" (Feld: `{field_name}`)"
    resolved = list(seed.get("resolved_node_ids") or [])
    seed_line += f" — aufgeloest auf {len(resolved)} Knoten" if resolved else " — **nicht aufgeloest**"
    lines.append(seed_line)
    lines.append("")

    if not results:
        lines.append("**Ergebnis: nicht gefunden / nicht belegt.** Es liegen keine Evidence-Pfade vor; das ist kein Beleg fuer Abwesenheit.")
    else:
        lines.append(f"**{len(results)} Ergebnis(se)** (max. {max_results} gezeigt):")
        lines.append("")
        for entry in results[: max(1, int(max_results))]:
            header = (
                f"- `{entry.get('result_node_id')}` — Rolle: {entry.get('result_role')}, "
                f"Score: {entry.get('score')}, Tiefe: {entry.get('depth')}"
            )
            for extra_key in ("coverage_kind", "enforcement", "dependency_kind"):
                if entry.get(extra_key):
                    header += f", {extra_key}: {entry[extra_key]}"
            if entry.get("operations"):
                header += f", Operationen: {', '.join(entry['operations'])}"
            lines.append(header)
            for path in list(entry.get("evidence_paths") or [])[:2]:
                chain = " -> ".join(
                    f"{edge.get('edge_type')}({edge.get('direction_used')}, c={edge.get('confidence')})"
                    for edge in list(path.get("edges") or [])
                )
                lines.append(f"  - Evidence (Score {path.get('path_score')}): {chain}")
            for warning in list(entry.get("warnings") or []):
                lines.append(f"  - Warnung: {warning}")
    if warnings:
        lines.append("")
        lines.append("**Warnungen:** " + "; ".join(warnings))
    lines.append("")
    lines.append(
        "_Hinweis: Evidence-Pfade sind indexierte Hinweise mit Confidence, keine Compiler-Wahrheit. "
        "Heuristische Kanten nicht als harte Architekturaussage uebernehmen._"
    )
    return "\n".join(lines)
