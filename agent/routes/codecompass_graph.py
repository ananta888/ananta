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

    python_modules: list[dict] = []
    ts_folders: list[dict] = []
    out_dir = nodes_path.parent

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
        python_modules.append({"domain": area, "display_name": area, "file_count": fc, "kind": "python_module"})

    def _extract_ts(node):
        folder = str(node.get("folder") or node.get("file") or "")
        if not folder:
            return
        fc = (node.get("summary") or {}).get("file_count") or len(node.get("files") or [])
        ts_folders.append({"domain": f"ts:{folder}", "display_name": f"ts: {folder}", "file_count": fc, "kind": "typescript_folder"})

    _read_detail(py_detail, _extract_python)
    _read_detail(ts_detail, _extract_ts)

    python_modules.sort(key=lambda x: (-x["file_count"], x["domain"]))
    ts_folders.sort(key=lambda x: (-x["file_count"], x["domain"]))
    domains = python_modules + ts_folders
    return api_response(data={"domains": domains})


@codecompass_graph_bp.route("/api/codecompass/self-graph", methods=["GET"])
@check_auth
def get_self_graph():
    """Serve Ananta's own rag-helper/out JSONL graph scoped to a domain and BFS depth.

    ?domain=agent     — top-level dir prefix (default: agent). Use 'all' for full graph.
    ?depth=2          — BFS hops from anchor nodes (default: 2, 0 = anchors only).
    ?kind=python_file — optional extra kind filter.
    """
    from agent.config import settings

    domain_filter = str(request.args.get("domain") or "agent").strip()
    raw_depth = str(request.args.get("depth") or "2").strip()
    kind_filter = str(request.args.get("kind") or "").strip().lower() or None
    try:
        bfs_depth = max(0, min(int(raw_depth), 6))
    except ValueError:
        bfs_depth = 2

    nodes_path, edges_path = _rag_out_paths(settings)
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

    # ── 2. Filter to domain (file-path prefix match) ─────────────────────────
    file_prefix = _domain_to_file_prefix(domain_filter)
    if not file_prefix:
        scoped = list(all_nodes_by_id.values())
    else:
        scoped = [
            n for n in all_nodes_by_id.values()
            if str(n.get("file") or "").replace("\\", "/").startswith(file_prefix)
        ]

    if kind_filter:
        scoped = [n for n in scoped if str(n.get("kind") or "").lower() == kind_filter]

    # ── 3. Load edges for the whole graph (needed for BFS) ───────────────────
    adj: dict[str, list[tuple[str, dict]]] = {}  # node_id → [(neighbour_id, edge)]
    all_edges_raw: list[dict] = []
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
                if not src or not tgt:
                    continue
                all_edges_raw.append(edge)
                adj.setdefault(src, []).append((tgt, edge))
                adj.setdefault(tgt, []).append((src, edge))

    # ── 4. BFS expansion from scoped seeds ───────────────────────────────────
    selected_ids: set[str] = {str(n["id"]) for n in scoped}
    frontier = set(selected_ids)
    for _ in range(bfs_depth):
        next_frontier: set[str] = set()
        for nid in frontier:
            for neighbour_id, _ in adj.get(nid, []):
                if neighbour_id not in selected_ids:
                    selected_ids.add(neighbour_id)
                    next_frontier.add(neighbour_id)
        frontier = next_frontier
        if not frontier:
            break

    # ── 5. Build output ──────────────────────────────────────────────────────
    raw_nodes = []
    for nid in selected_ids:
        n = all_nodes_by_id.get(nid)
        if not n:
            continue
        raw_nodes.append({
            "node_id": nid,
            "node_type": str(n.get("kind") or "unknown"),
            "attributes": {
                "file": str(n.get("file") or ""),
                "name": nid.split(":")[-1] if ":" in nid else nid,
                "content": "",
                "record_id": nid,
                "importance_score": float(n.get("importance_score") or 0.0),
            },
        })

    raw_edges = []
    for edge in all_edges_raw:
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if src in selected_ids and tgt in selected_ids:
            raw_edges.append({
                "source_id": src,
                "target_id": tgt,
                "relation": str(edge.get("type") or edge.get("kind") or "related"),
                "attributes": {"confidence": 1.0},
            })

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
            "depth": bfs_depth,
            "total_nodes_available": len(all_nodes_by_id),
        },
        "warnings": [],
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
