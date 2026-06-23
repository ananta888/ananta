"""Wiki Article Graph API — lightweight neighborhood exploration for wiki knowledge indices.

GET  /api/wiki-graph/status?index_id=<knowledge_index_id>
POST /api/wiki-graph/build        body: {"index_id": "...", "force": false}
GET  /api/wiki-graph/search?index_id=...&q=...&limit=20
GET  /api/wiki-graph/expand?index_id=...&slug=...&max_neighbors=40
"""
from __future__ import annotations

import threading
from pathlib import Path

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.services.repository_registry import get_repository_registry
from agent.services import wiki_article_graph_service as _svc

wiki_graph_bp = Blueprint("wiki_graph", __name__)


def _resolve_output_dir(index_id: str) -> Path:
    if not index_id:
        raise BadRequestError("index_id_required")
    repo = get_repository_registry().knowledge_index_repo
    idx = repo.get_by_id(index_id)
    if idx is None:
        raise NotFoundError("knowledge_index_not_found")
    output_dir = str(idx.output_dir or "").strip()
    if not output_dir:
        raise NotFoundError("output_dir_not_set")
    return Path(output_dir)


@wiki_graph_bp.route("/api/wiki-graph/status", methods=["GET"])
@check_auth
def wiki_graph_status():
    index_id = str(request.args.get("index_id") or "").strip()
    output_dir = _resolve_output_dir(index_id)
    status = _svc.get_build_status(output_dir)
    return api_response(data=status)


@wiki_graph_bp.route("/api/wiki-graph/build", methods=["POST"])
@check_auth
def wiki_graph_build():
    body = request.get_json(silent=True) or {}
    index_id = str(body.get("index_id") or "").strip()
    force = bool(body.get("force", False))
    output_dir = _resolve_output_dir(index_id)
    current = _svc.get_build_status(output_dir)
    if current.get("status") == "building" and not force:
        return api_response(data={"started": False, "status": current})
    t = threading.Thread(
        target=_svc.build_index,
        args=(output_dir,),
        kwargs={"force": force},
        daemon=True,
        name="wiki-article-graph-build",
    )
    t.start()
    return api_response(data={"started": True, "status": "building"})


@wiki_graph_bp.route("/api/wiki-graph/search", methods=["GET"])
@check_auth
def wiki_graph_search():
    index_id = str(request.args.get("index_id") or "").strip()
    q = str(request.args.get("q") or "").strip()
    limit = max(1, min(int(request.args.get("limit") or 20), 100))
    if not q:
        raise BadRequestError("q_required")
    output_dir = _resolve_output_dir(index_id)
    results = _svc.search_articles(output_dir, q, limit=limit)
    return api_response(data={"results": results, "query": q, "count": len(results)})


@wiki_graph_bp.route("/api/wiki-graph/expand", methods=["GET"])
@check_auth
def wiki_graph_expand():
    index_id = str(request.args.get("index_id") or "").strip()
    slug = str(request.args.get("slug") or "").strip()
    max_neighbors = max(5, min(int(request.args.get("max_neighbors") or 40), 150))
    if not slug:
        raise BadRequestError("slug_required")
    output_dir = _resolve_output_dir(index_id)
    graph = _svc.expand_article(output_dir, slug, max_neighbors=max_neighbors)
    return api_response(data=graph)
