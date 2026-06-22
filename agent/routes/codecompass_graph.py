"""REST endpoints for CodeCompass graph visualization.

GET /api/codecompass/graph?knowledge_index_id=<id>
GET /api/codecompass/graph/node/<node_id>?knowledge_index_id=<id>
GET /api/codecompass/graph/expand?knowledge_index_id=<id>&seed=<node_id>&profile=<name>
GET /api/codecompass/query?knowledge_index_id=<id>&type=<query_type>&seed=<symbol-or-node-id>&field=<optional>&depth=<optional>&direction=<optional>
GET /api/codecompass/self-graph?limit=<n>&kind=<filter>  — Ananta self-graph from rag-helper/out JSONL
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.services.repository_registry import get_repository_registry

codecompass_graph_bp = Blueprint("codecompass_graph", __name__)

_GRAPH_INDEX_FILENAME = "cc_graph_index.json"

# APRL-015: graph expansion profiles mapped to agent profile IDs where applicable.
# Keys are the graph-expansion profile names; values are the corresponding agent profile_id.
_PROFILE_TO_AGENT_PROFILE: dict[str, str] = {
    "bugfix_local": "bug_fix",
    "refactor_navigation": "refactor",
    "architecture_review": "architecture_review",
    "config_integration": "feature",
}
_VALID_PROFILES = set(_PROFILE_TO_AGENT_PROFILE.keys())


def _knowledge_index_repo():
    return get_repository_registry().knowledge_index_repo


def _resolve_index_path(knowledge_index_id: str) -> Path:
    if not knowledge_index_id:
        raise BadRequestError("knowledge_index_id_required")
    index = _knowledge_index_repo().get_by_id(knowledge_index_id)
    if index is None:
        raise NotFoundError("knowledge_index_not_found")
    output_dir = str(index.output_dir or "").strip()
    if not output_dir:
        raise NotFoundError("graph_output_dir_not_set")
    return Path(output_dir) / _GRAPH_INDEX_FILENAME


def _load_store(index_path: Path):
    from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
    return CodeCompassGraphStore(index_path=index_path)


def _nodes_to_artifact_format(nodes: list[dict]) -> list[dict]:
    result = []
    for node in nodes:
        result.append({
            "node_id": str(node.get("id") or ""),
            "node_type": str(node.get("kind") or "unknown"),
            "attributes": {
                "file": str(node.get("file") or ""),
                "name": str(node.get("name") or ""),
                "content": str(node.get("content") or ""),
                "record_id": str(node.get("record_id") or ""),
            },
        })
    return result


def _edges_to_artifact_format(edges: list[dict]) -> list[dict]:
    result = []
    for edge in edges:
        result.append({
            "source_id": str(edge.get("source_id") or ""),
            "target_id": str(edge.get("target_id") or ""),
            "relation": str(edge.get("edge_type") or "related"),
            "attributes": {
                "confidence": float(edge.get("confidence") or 1.0),
            },
        })
    return result


@codecompass_graph_bp.route("/api/codecompass/graph", methods=["GET"])
@check_auth
def get_graph():
    knowledge_index_id = str(request.args.get("knowledge_index_id") or "").strip()
    index_path = _resolve_index_path(knowledge_index_id)
    store = _load_store(index_path)
    payload = store.load()
    diagnostics = dict(payload.get("diagnostics") or {})
    nodes = list(payload.get("nodes") or [])
    edges = list(payload.get("edges") or [])
    return api_response(data={
        "schema": "domain_graph_artifact.v1",
        "source_kind": "codecompass_graph",
        "source_ref": knowledge_index_id,
        "nodes": _nodes_to_artifact_format(nodes),
        "edges": _edges_to_artifact_format(edges),
        "metadata": {
            "knowledge_index_id": knowledge_index_id,
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
        "diagnostics": diagnostics,
        "warnings": [diagnostics.get("reason")] if diagnostics.get("status") == "degraded" else [],
    })


@codecompass_graph_bp.route("/api/codecompass/graph/node/<node_id>", methods=["GET"])
@check_auth
def get_node(node_id: str):
    knowledge_index_id = str(request.args.get("knowledge_index_id") or "").strip()
    index_path = _resolve_index_path(knowledge_index_id)
    store = _load_store(index_path)
    payload = store.load()
    by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
    node = by_id.get(node_id)
    if node is None:
        raise NotFoundError("node_not_found")
    return api_response(data={
        "node_id": str(node.get("id") or ""),
        "node_type": str(node.get("kind") or "unknown"),
        "attributes": {
            "file": str(node.get("file") or ""),
            "name": str(node.get("name") or ""),
            "content": str(node.get("content") or ""),
            "record_id": str(node.get("record_id") or ""),
        },
    })


@codecompass_graph_bp.route("/api/codecompass/graph/expand", methods=["GET"])
@check_auth
def expand_graph():
    knowledge_index_id = str(request.args.get("knowledge_index_id") or "").strip()
    seed_id = str(request.args.get("seed") or "").strip()
    profile = str(request.args.get("profile") or "bugfix_local").strip().lower()
    if not seed_id:
        raise BadRequestError("seed_required")
    if profile not in _VALID_PROFILES:
        raise BadRequestError(f"invalid_profile — valid: {', '.join(sorted(_VALID_PROFILES))}")
    index_path = _resolve_index_path(knowledge_index_id)
    store = _load_store(index_path)
    from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph
    expansion = expand_codecompass_graph(store=store, seed_node_ids=[seed_id], profile=profile)
    nodes = list(expansion.get("nodes") or [])
    paths = list(expansion.get("paths") or [])
    # APRL-015: resolve agent profile metadata for this graph profile
    agent_profile_meta: dict = {}
    _agent_profile_id = _PROFILE_TO_AGENT_PROFILE.get(profile)
    if _agent_profile_id:
        try:
            from agent.services.agent_profile_service import get_agent_profile_service
            _ap = get_agent_profile_service().resolve_by_profile_id(_agent_profile_id)
            agent_profile_meta = _ap.to_metadata()
        except Exception:
            pass
    return api_response(data={
        "schema": "domain_graph_artifact.v1",
        "source_kind": "codecompass_graph_expansion",
        "source_ref": knowledge_index_id,
        "nodes": _nodes_to_artifact_format(nodes),
        "edges": _edges_from_paths(paths),
        "metadata": {
            "knowledge_index_id": knowledge_index_id,
            "seed_node_id": seed_id,
            "profile": profile,
            "node_count": len(nodes),
            "active_agent_profile": agent_profile_meta or None,
        },
        "warnings": [],
    })


@codecompass_graph_bp.route("/api/codecompass/query", methods=["GET"])
@check_auth
def architecture_query():
    """CCAQE-017: typed architecture queries with evidence paths."""
    from worker.retrieval.codecompass_architecture_query import (
        VALID_QUERY_TYPES,
        QueryLimits,
        run_architecture_query,
    )

    knowledge_index_id = str(request.args.get("knowledge_index_id") or "").strip()
    query_type = str(request.args.get("type") or "").strip().lower()
    seed = str(request.args.get("seed") or "").strip()
    field = str(request.args.get("field") or "").strip() or None
    direction = str(request.args.get("direction") or "").strip().lower() or None
    raw_depth = str(request.args.get("depth") or "").strip()
    if query_type not in VALID_QUERY_TYPES:
        raise BadRequestError(f"invalid_query_type — valid: {', '.join(VALID_QUERY_TYPES)}")
    if not seed:
        raise BadRequestError("seed_required")
    depth: int | None = None
    if raw_depth:
        try:
            depth = int(raw_depth)
        except ValueError:
            raise BadRequestError("invalid_depth")
    if direction is not None and direction not in {"outgoing", "incoming", "both"}:
        raise BadRequestError("invalid_direction — valid: outgoing, incoming, both")
    index_path = _resolve_index_path(knowledge_index_id)
    store = _load_store(index_path)

    from agent.config import settings
    limits = QueryLimits(
        max_depth=int(settings.codecompass_query_max_depth),
        max_nodes=int(settings.codecompass_query_max_nodes),
        max_results=int(settings.codecompass_query_max_results),
        max_paths_per_result=int(settings.codecompass_query_max_paths_per_result),
    )
    result = run_architecture_query(
        store=store,
        query_type=query_type,
        seed=seed,
        field=field,
        depth=depth,
        direction=direction,
        limits=limits,
    )
    result.setdefault("metadata", {})["knowledge_index_id"] = knowledge_index_id
    return api_response(data=result)


def _rag_out_paths(settings):
    repo_root = Path(getattr(settings, "rag_repo_root", ".")).resolve()
    return (
        repo_root / "rag-helper" / "out" / "graph_nodes.jsonl",
        repo_root / "rag-helper" / "out" / "graph_edges.jsonl",
    )


_name_index_cache: dict[str, str] | None = None


def _get_name_index(out_dir: Path) -> dict[str, str]:
    """Build node_id → human-readable name from index_by_kind files. Cached after first load."""
    global _name_index_cache
    if _name_index_cache is not None:
        return _name_index_cache

    names: dict[str, str] = {}
    ik = out_dir / "index_by_kind"

    def _read_jsonl(path: Path):
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    # ── Python: functions and classes/methods in python_file.jsonl ───────────
    for node in _read_jsonl(ik / "python_file.jsonl"):
        raw_id = str(node.get("id") or "")
        parts = raw_id.split(":")
        if len(parts) < 2:
            continue
        fhash = parts[-1]
        for fn in node.get("functions") or []:
            if fn.get("name") and fn.get("line") is not None:
                names[f"python_function:{fhash}:{fn['line']}"] = fn["name"]
        for cls in node.get("classes") or []:
            if cls.get("name") and cls.get("line") is not None:
                names[f"python_class:{fhash}:{cls['line']}"] = cls["name"]
            for method in cls.get("methods") or []:
                if method.get("name") and method.get("line") is not None:
                    names[f"python_method:{fhash}:{method['line']}"] = method["name"]

    # ── TypeScript: symbols in typescript_file.jsonl ─────────────────────────
    _TS_KIND_MAP = {
        "function": "typescript_function", "class": "typescript_class",
        "interface": "typescript_interface", "method": "typescript_method",
        "const": "typescript_const", "type": "typescript_type",
        "enum": "typescript_enum", "constructor": "typescript_constructor",
    }
    for node in _read_jsonl(ik / "typescript_file.jsonl"):
        raw_id = str(node.get("id") or "")
        parts = raw_id.split(":")
        if len(parts) < 2:
            continue
        fhash = parts[-1]
        for sym in node.get("symbols") or []:
            ts_kind = _TS_KIND_MAP.get(str(sym.get("kind") or ""))
            if ts_kind and sym.get("name") and sym.get("line") is not None:
                names[f"{ts_kind}:{fhash}:{sym['line']}"] = sym["name"]

    # ── Java: type names and method signatures in java_type.jsonl ────────────
    for node in _read_jsonl(ik / "java_type.jsonl"):
        raw_id = str(node.get("id") or "")
        if node.get("name"):
            names[raw_id] = node["name"]

    _name_index_cache = names
    return names


def _load_nodes_jsonl(nodes_path: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    with nodes_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                node = json.loads(line)
            except json.JSONDecodeError:
                continue
            nid = str(node.get("id") or "")
            if nid:
                result[nid] = node
    return result


# Tier 0: structural skeleton (files + summaries)
# Tier 1: type definitions (classes, interfaces, enums)
# Tier 2: implementations (functions, methods, constructors)
# Tier 3: details (imports, tags, entries, sections)
_NODE_KIND_TIER: dict[str, int] = {
    "python_module_summary": 0, "typescript_folder_summary": 0, "java_module_summary": 0,
    "python_file": 0, "typescript_file": 0, "java_file": 0,
    "md_file": 0, "yaml_file": 0, "xml_file": 0, "properties_file": 0,
    "python_class": 1, "typescript_class": 1, "typescript_interface": 1,
    "typescript_type": 1, "typescript_enum": 1, "java_type": 1,
    "python_function": 2, "python_method": 2,
    "typescript_function": 2, "typescript_method": 2,
    "typescript_const": 2, "typescript_constructor": 2,
    "java_method": 2, "java_constructor": 2,
    "java_method_detail": 2, "java_constructor_detail": 2,
    "python_import": 3, "typescript_import": 3,
    "md_section": 3, "xml_tag": 3, "xml_node_detail": 3,
    "yaml_entry": 3, "properties_entry": 3,
}
_DEFAULT_TIER = 3
_DEFAULT_MAX_NODES = 0
_DEFAULT_MAX_EDGES = 0


def _domain_parts_from_file(file_path: str) -> list[str]:
    normalized = file_path.replace("\\", "/").strip("/")
    if not normalized:
        return []
    parts = normalized.split("/")
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    elif "." in parts[-1]:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return [p for p in parts if p]


def _domain_id_from_parts(parts: list[str], source_kind: str) -> str:
    if source_kind == "typescript":
        return f"ts:{'/'.join(parts)}"
    return ".".join(parts)


def _domain_metadata_for_node(file_path: str, selected_domain: str) -> dict[str, object]:
    parts = _domain_parts_from_file(file_path)
    if not parts:
        return {"domain_path": "", "domain_level": 0, "domain_parent": "", "domain_leaf": ""}

    source_kind = "typescript" if selected_domain.startswith("ts:") or file_path.startswith("frontend-angular/") else "python"
    selected_prefix = _domain_to_file_prefix(selected_domain)
    selected_parts = _domain_parts_from_file(selected_prefix)
    rel_parts = parts[len(selected_parts):] if selected_parts and parts[:len(selected_parts)] == selected_parts else parts
    rel_parts = rel_parts or parts[-1:]
    domain_parts = parts[:len(parts) - len(rel_parts) + min(len(rel_parts), 3)]
    domain_path = _domain_id_from_parts(domain_parts, source_kind)
    parent_path = _domain_id_from_parts(domain_parts[:-1], source_kind) if len(domain_parts) > 1 else ""
    return {
        "domain_path": domain_path,
        "domain_level": max(0, len(domain_parts) - len(selected_parts)),
        "domain_parent": parent_path,
        "domain_leaf": domain_parts[-1],
    }


def _domain_to_file_prefix(domain: str) -> str:
    """Convert a domain key to the file-path prefix used for filtering.

    Python module areas use dots (e.g. 'agent.routes') → 'agent/routes'.
    TypeScript folders are prefixed with 'ts:' (e.g. 'ts:src/app') → 'src/app'.
    'all' → '' (no filtering).
    """
    if domain == "all":
        return ""
    if domain.startswith("ts:"):
        return domain[3:]
    return domain.replace(".", "/")


@codecompass_graph_bp.route("/api/codecompass/self-graph/domains", methods=["GET"])
@check_auth
def get_self_graph_domains():
    """Return semantic module domains derived from *_module_summary and folder_summary nodes."""
    from agent.config import settings
    nodes_path, _ = _rag_out_paths(settings)
    if not nodes_path.exists():
        return api_response(data={"domains": [], "warnings": ["graph_nodes.jsonl not found"]})

    domains_by_id: dict[str, dict] = {}
    out_dir = nodes_path.parent

    def _upsert_domain(domain: str, display_name: str, kind: str, file_count: int, depth: int):
        if not domain:
            return
        existing = domains_by_id.get(domain)
        if existing:
            existing["file_count"] = max(int(existing.get("file_count") or 0), file_count)
            existing["depth"] = min(int(existing.get("depth") or depth), depth)
            return
        parent = ""
        if domain.startswith("ts:"):
            path = domain[3:]
            parent_path = "/".join(path.split("/")[:-1])
            parent = f"ts:{parent_path}" if parent_path else ""
        elif "." in domain:
            parent = domain.rsplit(".", 1)[0]
        domains_by_id[domain] = {
            "domain": domain,
            "display_name": display_name,
            "file_count": file_count,
            "kind": kind,
            "depth": depth,
            "parent_domain": parent,
        }

    # Detail files have full metadata (module_area, files[], summary{})
    py_detail = out_dir / "index_by_kind" / "python_module_summary.jsonl"
    ts_detail = out_dir / "index_by_kind" / "typescript_folder_summary.jsonl"

    def _read_detail(path: Path, extract):
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    node = json.loads(line)
                except json.JSONDecodeError:
                    continue
                extract(node)

    def _extract_python(node):
        area = str(node.get("module_area") or node.get("file") or "")
        if not area:
            return
        fc = (node.get("summary") or {}).get("file_count") or len(node.get("files") or [])
        _upsert_domain(area, area, "python_module", fc, area.count("."))
        for file_name in node.get("files") or []:
            parts = _domain_parts_from_file(str(file_name))
            for idx in range(2, min(len(parts), 5) + 1):
                child = ".".join(parts[:idx])
                _upsert_domain(child, child, "python_module", 1, child.count("."))

    def _extract_ts(node):
        folder = str(node.get("folder") or node.get("file") or "")
        if not folder:
            return
        fc = (node.get("summary") or {}).get("file_count") or len(node.get("files") or [])
        domain = f"ts:{folder}"
        _upsert_domain(domain, f"ts: {folder}", "typescript_folder", fc, folder.count("/"))
        for file_name in node.get("files") or []:
            parts = _domain_parts_from_file(str(file_name))
            for idx in range(2, min(len(parts), 5) + 1):
                child_path = "/".join(parts[:idx])
                _upsert_domain(f"ts:{child_path}", f"ts: {child_path}", "typescript_folder", 1, child_path.count("/"))

    _read_detail(py_detail, _extract_python)
    _read_detail(ts_detail, _extract_ts)

    domains = sorted(domains_by_id.values(), key=lambda x: (x["kind"], x["depth"], -x["file_count"], x["domain"]))
    return api_response(data={"domains": domains})


@codecompass_graph_bp.route("/api/codecompass/self-graph", methods=["GET"])
@check_auth
def get_self_graph():
    """Serve Ananta's own rag-helper/out JSONL graph with kind detail levels + optional caps.

    ?domain=agent.routes  — module area key (default: agent.routes). 'all' for everything.
    ?detail_level=1       — node detail level (default: 1).
                           0 = files+summaries only (~fast)
                           1 = + classes/types
                           2 = + functions/methods
                           3 = + imports/details (all)
                           Legacy alias: depth.
    ?max_nodes=0          — optional cap on output nodes (default: 0 = no cap).
    ?max_edges=0          — optional cap on output edges (default: 0 = no cap).
    ?kind=python_file     — optional extra single-kind filter.
    """
    from agent.config import settings

    domain_filter = str(request.args.get("domain") or "agent.routes").strip()
    raw_detail_level = str(request.args.get("detail_level") or request.args.get("depth") or "1").strip()
    kind_filter = str(request.args.get("kind") or "").strip().lower() or None
    raw_max_nodes = str(request.args.get("max_nodes") or str(_DEFAULT_MAX_NODES)).strip()
    raw_max_edges = str(request.args.get("max_edges") or str(_DEFAULT_MAX_EDGES)).strip()
    try:
        detail_level = max(0, min(int(raw_detail_level), 3))
    except ValueError:
        detail_level = 1
    try:
        max_nodes = int(raw_max_nodes)
    except ValueError:
        max_nodes = _DEFAULT_MAX_NODES
    try:
        max_edges = int(raw_max_edges)
    except ValueError:
        max_edges = _DEFAULT_MAX_EDGES

    nodes_path, edges_path = _rag_out_paths(settings)
    name_index = _get_name_index(nodes_path.parent)
    if not nodes_path.exists():
        return api_response(data={
            "schema": "domain_graph_artifact.v1",
            "source_kind": "ananta_self_graph",
            "source_ref": "ananta",
            "nodes": [], "edges": [],
            "metadata": {"node_count": 0, "edge_count": 0},
            "warnings": ["rag-helper/out/graph_nodes.jsonl not found"],
        })

    # ── 1. Load all nodes ────────────────────────────────────────────────────
    all_nodes_by_id = _load_nodes_jsonl(nodes_path)

    # ── 2. Filter to domain (file-path prefix) ───────────────────────────────
    file_prefix = _domain_to_file_prefix(domain_filter)
    if not file_prefix:
        scoped = list(all_nodes_by_id.values())
    else:
        scoped = [
            n for n in all_nodes_by_id.values()
            if str(n.get("file") or "").replace("\\", "/").startswith(file_prefix)
        ]

    domain_total = len(scoped)

    # ── 3. Detail-level filter (kind tiers, not graph traversal hops) ────────
    scoped = [
        n for n in scoped
        if _NODE_KIND_TIER.get(str(n.get("kind") or ""), _DEFAULT_TIER) <= detail_level
    ]
    if kind_filter:
        scoped = [n for n in scoped if str(n.get("kind") or "").lower() == kind_filter]

    tier_total = len(scoped)

    # ── 4. Load edges between scoped nodes; compute degree for cap ordering ────
    scoped_ids_full: set[str] = {str(n["id"]) for n in scoped}
    node_degree: dict[str, int] = {nid: 0 for nid in scoped_ids_full}
    all_internal_edges: list[dict] = []
    if edges_path.exists():
        with edges_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    edge = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = str(edge.get("source") or "")
                tgt = str(edge.get("target") or "")
                if src in scoped_ids_full and tgt in scoped_ids_full:
                    node_degree[src] = node_degree.get(src, 0) + 1
                    node_degree[tgt] = node_degree.get(tgt, 0) + 1
                    all_internal_edges.append({
                        "source_id": src,
                        "target_id": tgt,
                        "relation": str(edge.get("type") or edge.get("kind") or "related"),
                        "attributes": {"confidence": 1.0},
                    })

    # ── 5. Optional node cap — priority: lower tier, then degree DESC, then importance ─
    warnings: list[str] = []
    capped = False
    if max_nodes > 0 and len(scoped) > max_nodes:
        scoped.sort(key=lambda n: (
            _NODE_KIND_TIER.get(str(n.get("kind") or ""), _DEFAULT_TIER),
            -node_degree.get(str(n["id"]), 0),
            -float(n.get("importance_score") or 0.0),
        ))
        scoped = scoped[:max_nodes]
        capped = True
        warnings.append(
            f"cap_applied: showing {max_nodes} of {tier_total} nodes "
            f"(domain has {domain_total} total — raise detail_level or max_nodes to see more)"
        )

    # ── 6. Filter edges to final selected node set ────────────────────────────
    selected_ids: set[str] = {str(n["id"]) for n in scoped}
    raw_edges = [
        e for e in all_internal_edges
        if e["source_id"] in selected_ids and e["target_id"] in selected_ids
    ]
    pre_edge_cap_count = len(raw_edges)
    edge_capped = False
    if max_edges > 0 and len(raw_edges) > max_edges:
        raw_edges = raw_edges[:max_edges]
        edge_capped = True
        warnings.append(
            f"edge_cap_applied: showing {max_edges} of {pre_edge_cap_count} edges "
            f"(raise max_edges to see more)"
        )

    # ── 7. Format output ─────────────────────────────────────────────────────
    raw_nodes = [
        {
            "node_id": str(n["id"]),
            "node_type": str(n.get("kind") or "unknown"),
            "attributes": {
                "file": str(n.get("file") or ""),
                "name": name_index.get(str(n["id"])) or str(n.get("name") or ""),
                "content": "",
                "record_id": str(n["id"]),
                "importance_score": float(n.get("importance_score") or 0.0),
                **_domain_metadata_for_node(str(n.get("file") or ""), domain_filter),
            },
        }
        for n in scoped
    ]

    return api_response(data={
        "schema": "domain_graph_artifact.v1",
        "source_kind": "ananta_self_graph",
        "source_ref": f"ananta:{domain_filter}",
        "nodes": raw_nodes,
        "edges": raw_edges,
        "metadata": {
            "node_count": len(raw_nodes),
            "edge_count": len(raw_edges),
            "domain": domain_filter,
            "detail_level": detail_level,
            "depth": detail_level,
            "domain_total_nodes": domain_total,
            "tier_total_nodes": tier_total,
            "capped": capped,
            "edge_capped": edge_capped,
            "max_nodes": max_nodes if max_nodes > 0 else None,
            "max_edges": max_edges if max_edges > 0 else None,
            "total_nodes_available": len(all_nodes_by_id),
            "pre_cap_edge_count": len(all_internal_edges),
            "pre_edge_cap_edge_count": pre_edge_cap_count,
        },
        "warnings": warnings,
    })


def _edges_from_paths(paths: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for path_entry in paths:
        for edge in list(path_entry.get("path") or []):
            src = str(edge.get("source_id") or "")
            tgt = str(edge.get("target_id") or "")
            etype = str(edge.get("edge_type") or "related")
            key = f"{src}|{tgt}|{etype}"
            if key in seen or not src or not tgt:
                continue
            seen.add(key)
            result.append({
                "source_id": src,
                "target_id": tgt,
                "relation": etype,
                "attributes": {"confidence": float(edge.get("confidence") or 1.0)},
            })
    return result
