"""REST endpoints for CodeCompass graph visualization.

GET /api/codecompass/graph?knowledge_index_id=<id>
GET /api/codecompass/graph/node/<node_id>?knowledge_index_id=<id>
GET /api/codecompass/graph/expand?knowledge_index_id=<id>&seed=<node_id>&profile=<name>
"""
from __future__ import annotations

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
