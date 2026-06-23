"""Wiki Article Graph API — lightweight neighborhood exploration for wiki knowledge indices.

GET  /api/wiki-graph/status?index_id=<knowledge_index_id>
POST /api/wiki-graph/build        body: {"index_id": "...", "force": false}
GET  /api/wiki-graph/search?index_id=...&q=...&limit=20
GET  /api/wiki-graph/expand?index_id=...&slug=...&max_neighbors=40
GET  /api/wiki-graph/domain-status?index_id=...
POST /api/wiki-graph/build-domains  body: {index_id, mode, corpus_path?}
GET  /api/wiki-graph/domains?index_id=...&mode=...&limit=100
GET  /api/wiki-graph/domain-articles?index_id=...&mode=...&domain=...&limit=50
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


@wiki_graph_bp.route("/api/wiki-graph/domain-status", methods=["GET"])
@check_auth
def wiki_graph_domain_status():
    index_id = str(request.args.get("index_id") or "").strip()
    output_dir = _resolve_output_dir(index_id)
    status = _svc.get_domain_build_status(output_dir)
    return api_response(data=status)


@wiki_graph_bp.route("/api/wiki-graph/build-domains", methods=["POST"])
@check_auth
def wiki_graph_build_domains():
    from pathlib import Path as _Path
    body = request.get_json(silent=True) or {}
    index_id = str(body.get("index_id") or "").strip()
    mode = str(body.get("mode") or "").strip()
    if mode not in ("hubs", "categories", "clusters"):
        raise BadRequestError("mode must be one of: hubs, categories, clusters")
    output_dir = _resolve_output_dir(index_id)

    corpus_path = None
    if mode == "categories":
        cp = str(body.get("corpus_path") or "").strip()
        if not cp:
            # Try to derive from index metadata
            repo = get_repository_registry().knowledge_index_repo
            idx = repo.get_by_id(index_id)
            links_cache = str((idx.index_metadata or {}).get("links_cache") or "").strip()
            if links_cache and links_cache.endswith(".links.jsonl"):
                cp = links_cache[: -len(".links.jsonl")]
            if not cp:
                raise BadRequestError("corpus_path required for categories build")
        corpus_path = _Path(cp)

    # Check if already building
    import threading as _threading
    current = _svc.get_domain_build_status(output_dir)
    if current.get(mode, {}).get("status") == "building":
        return api_response(data={"started": False, "status": "already_building"})

    t = _threading.Thread(
        target=_svc.build_domains,
        args=(output_dir, mode, corpus_path),
        daemon=True,
        name=f"wiki-domain-build-{mode}",
    )
    t.start()
    return api_response(data={"started": True, "mode": mode, "status": "building"})


@wiki_graph_bp.route("/api/wiki-graph/domains", methods=["GET"])
@check_auth
def wiki_graph_domains():
    index_id = str(request.args.get("index_id") or "").strip()
    mode = str(request.args.get("mode") or "").strip()
    if mode not in ("hubs", "categories", "clusters"):
        raise BadRequestError("mode must be one of: hubs, categories, clusters")
    limit = max(1, min(int(request.args.get("limit") or 100), 500))
    output_dir = _resolve_output_dir(index_id)
    domains = _svc.get_domains(output_dir, mode, limit=limit)
    return api_response(data={"domains": domains, "mode": mode, "count": len(domains)})


@wiki_graph_bp.route("/api/wiki-graph/content-status", methods=["GET"])
@check_auth
def wiki_graph_content_status():
    index_id = str(request.args.get("index_id") or "").strip()
    output_dir = _resolve_output_dir(index_id)
    return api_response(data=_svc.get_content_status(output_dir))


@wiki_graph_bp.route("/api/wiki-graph/build-content", methods=["POST"])
@check_auth
def wiki_graph_build_content():
    import threading as _threading
    body = request.get_json(silent=True) or {}
    index_id = str(body.get("index_id") or "").strip()
    force = bool(body.get("force", False))
    output_dir = _resolve_output_dir(index_id)
    current = _svc.get_content_status(output_dir)
    if current.get("status") == "building" and not force:
        return api_response(data={"started": False, "status": current})
    t = _threading.Thread(
        target=_svc.build_content_index,
        args=(output_dir,),
        kwargs={"force": force},
        daemon=True,
        name="wiki-content-index-build",
    )
    t.start()
    return api_response(data={"started": True, "status": "building"})


@wiki_graph_bp.route("/api/wiki-graph/article-content", methods=["GET"])
@check_auth
def wiki_graph_article_content():
    index_id = str(request.args.get("index_id") or "").strip()
    slug = str(request.args.get("slug") or "").strip()
    if not slug:
        raise BadRequestError("slug required")
    output_dir = _resolve_output_dir(index_id)
    result = _svc.get_article_content(output_dir, slug)
    return api_response(data=result)


@wiki_graph_bp.route("/api/wiki-graph/domain-graph", methods=["GET"])
@check_auth
def wiki_graph_domain_graph():
    index_id = str(request.args.get("index_id") or "").strip()
    mode = str(request.args.get("mode") or "").strip()
    domain = str(request.args.get("domain") or "").strip()
    limit = max(10, min(int(request.args.get("limit") or 100), 500))
    if mode not in ("hubs", "categories", "clusters"):
        raise BadRequestError("mode must be one of: hubs, categories, clusters")
    if not domain:
        raise BadRequestError("domain required")
    output_dir = _resolve_output_dir(index_id)
    graph = _svc.get_domain_graph(output_dir, mode, domain, limit=limit)
    return api_response(data=graph)


@wiki_graph_bp.route("/api/wiki-graph/domain-articles", methods=["GET"])
@check_auth
def wiki_graph_domain_articles():
    index_id = str(request.args.get("index_id") or "").strip()
    mode = str(request.args.get("mode") or "").strip()
    domain = str(request.args.get("domain") or "").strip()
    if mode not in ("hubs", "categories", "clusters"):
        raise BadRequestError("mode must be one of: hubs, categories, clusters")
    if not domain:
        raise BadRequestError("domain required")
    limit = max(1, min(int(request.args.get("limit") or 50), 200))
    output_dir = _resolve_output_dir(index_id)
    articles = _svc.get_domain_articles(output_dir, mode, domain, limit=limit)
    return api_response(data={"articles": articles, "domain": domain, "mode": mode, "count": len(articles)})
