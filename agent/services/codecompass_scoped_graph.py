"""Read a graph only through a current sealed index/path capability."""

from agent.services.codecompass_retrieval_capability_service import verify_retrieval_capability
from ananta_codecompass.architecture_intelligence.graph_projection import project_graph


def path_allowed(path, prefixes):
    candidate = str(path or "").replace("\\", "/")
    if not candidate or candidate.startswith("/") or any(part in {"..", ""} for part in candidate.split("/")):
        return False
    return any(candidate == prefix.rstrip("/") or candidate.startswith(prefix.rstrip("/") + "/")
               for prefix in prefixes if prefix.rstrip("/"))


def load_scoped_graph(capability, *, snapshot_ref="", revision="", resolve=None):
    from agent.services.codecompass_graph_store_resolution_service import resolve_codecompass_graph_store

    scope = verify_retrieval_capability(capability)
    allowed = set(scope["allowed_index_ids"])
    if not allowed or (snapshot_ref and snapshot_ref not in allowed) or (revision and revision != scope["revision"]):
        raise ValueError("codecompass_graph_scope_invalid")
    resolver = resolve or resolve_codecompass_graph_store
    arguments = {"knowledge_index_id": snapshot_ref} if snapshot_ref else {}
    store, index_id, _diagnostics = resolver(arguments, allowed_index_ids=allowed)
    if store is None or index_id not in allowed:
        raise ValueError("codecompass_graph_scope_unavailable")
    payload = store.load()
    nodes = [node for node in (payload or {}).get("nodes", [])
             if isinstance(node, dict) and path_allowed(node.get("path") or node.get("file"), scope["allowed_paths"])]
    projection = project_graph(nodes=nodes, edges=(payload or {}).get("edges", []))
    return {"nodes": projection["nodes"], "edges": projection["edges"]}, index_id, scope["revision"]
