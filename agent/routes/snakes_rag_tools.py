"""Read-only tool definitions and dispatch for the RAG Snake chat loop."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.services.rag_context_packer import should_skip_initial_pack

_log = logging.getLogger(__name__)


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_CHAT_TOOLS = [
    _tool(
        "read_file",
        "Read the full content of a file from the project repository.",
        {"path": {"type": "string", "description": "Repo-relative path to the file"}},
        ["path"],
    ),
    _tool(
        "search_codebase",
        "Search the codebase by keyword and return matching file paths.",
        {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Maximum results", "default": 8},
        },
        ["query"],
    ),
    _tool(
        "codecompass_retrieve",
        "Retrieve grounded, hybrid CodeCompass evidence for a repository question.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "codecompass_architecture_overview",
        "Load a hierarchical System/Subsystem/Component architecture overview.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "codecompass_architecture_expand",
        "Expand an architecture handle returned by an overview.",
        {"handle": {"type": "string"}, "query": {"type": "string"}},
        ["handle"],
    ),
    _tool(
        "codecompass_architecture_dependencies",
        "Inspect bounded dependencies for an architecture handle.",
        {"handle": {"type": "string"}, "query": {"type": "string"}},
        ["handle"],
    ),
    _tool(
        "codecompass_symbol_context",
        "Load grounded symbol-level CodeCompass evidence for a query.",
        {"query": {"type": "string"}},
        ["query"],
    ),
]

_CODECOMPASS_CHAT_TOOL_MAP = {
    "codecompass_retrieve": "codecompass.retrieve",
    "codecompass_architecture_overview": "codecompass.architecture_overview",
    "codecompass_architecture_expand": "codecompass.architecture_expand",
    "codecompass_architecture_dependencies": "codecompass.architecture_dependencies",
    "codecompass_symbol_context": "codecompass.symbol_context",
}


def _snake_codecompass_capability(repo_root: Path) -> dict[str, Any] | None:
    try:
        from agent.services.codecompass_retrieval_capability_service import bind_retrieval_capability
        from agent.services.repository_registry import get_repository_registry
        from agent.services.tools.codecompass_tools import _resolve_graph_store

        _store, index_id = _resolve_graph_store({})
        if not index_id:
            return None
        index = get_repository_registry().knowledge_index_repo.get_by_id(index_id)
        metadata = dict(getattr(index, "index_metadata", None) or {})
        graph_binding = dict(metadata.get("graph_artifacts") or {})
        revision = str(
            graph_binding.get("graph_revision") or metadata.get("codecompass_snapshot_revision") or ""
        ).strip()
        source_id = str(getattr(index, "source_path", None) or metadata.get("source_id") or repo_root.name).strip()
        if not revision or not source_id:
            return None
        return bind_retrieval_capability(
            {
                "workspace_id": f"snake:{repo_root.name}",
                "repository_id": source_id,
                "source_scope": "repo_path",
                "revision": revision,
                "allowed_paths": [
                    "agent", "worker", "ananta_codecompass", "rag-helper", "frontend-angular",
                    "config", "docs", "scripts", "tests",
                ],
                "allowed_index_ids": [index_id],
                "allowed_signals": ["exact", "graph", "vector"],
            },
            subject_id="ai-snake",
            tenant_id="local",
            ttl_seconds=300,
        )
    except Exception:
        return None


def _resolve_file(path: str, repo_root: Path) -> Path | None:
    candidate = Path(path) if path.startswith("/") else repo_root / path
    if candidate.exists() and candidate.is_file():
        return candidate
    if path.startswith("/app/"):
        candidate = repo_root / path[5:]
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _tool_read_file(path: str, repo_root: Path, max_chars: int) -> str:
    resolved = _resolve_file(path.strip(), repo_root)
    if resolved is None:
        filename = Path(path.strip()).name
        candidates: list[str] = []
        skipped = {"__pycache__", ".git", ".claude", "node_modules", "dist", ".venv", "venv"}
        try:
            for candidate in repo_root.rglob(filename):
                if candidate.is_file() and not any(part in skipped for part in candidate.parts):
                    candidates.append(str(candidate.relative_to(repo_root)))
                    if len(candidates) >= 3:
                        break
        except Exception:
            pass
        if len(candidates) == 1:
            resolved = _resolve_file(candidates[0], repo_root)
            if resolved is not None:
                try:
                    content = _bounded_file_content(resolved, max_chars)
                    return f"[Pfad automatisch korrigiert: {path} -> {candidates[0]}]\n{content}"
                except OSError as exc:
                    return f"[Fehler beim Lesen: {exc}]"
        hint = (
            f"\n[Korrekter Pfad: nutze read_file('{candidates[0]}') — Datei gefunden unter: {', '.join(candidates)}]"
            if candidates else ""
        )
        return f"[Fehler: Datei nicht gefunden: {path}]{hint}"
    try:
        return _bounded_file_content(resolved, max_chars)
    except OSError as exc:
        return f"[Fehler beim Lesen: {exc}]"


def _bounded_file_content(path: Path, max_chars: int) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        return content[:max_chars] + f"\n... [abgeschnitten nach {max_chars} Zeichen]"
    return content


def _tool_search_codebase(query: str, max_results: int, repo_root: Path) -> str:
    try:
        from agent.hybrid_orchestrator import RepositoryMapEngine

        engine = RepositoryMapEngine(repo_root)
        engine.build()
        chunks = [
            chunk
            for chunk in engine.search(query, top_k=max(1, min(max_results * 3, 40)))
            if not should_skip_initial_pack(str(chunk.source or ""))
        ][:max(1, min(max_results, 20))]
        rendered = "\n".join(f"- {chunk.source}  (score: {chunk.score:.1f})" for chunk in chunks)
        return rendered or "[Keine Treffer für diese Suche]"
    except Exception as exc:
        _log.debug("search_codebase tool failed: %s", exc)
        return f"[Suche fehlgeschlagen: {exc}]"


def _dispatch_tool(name: str, args: dict, *, repo_root: Path, max_chars_per_file: int) -> str:
    if name == "read_file":
        path = str(args.get("path") or "").strip()
        return _tool_read_file(path, repo_root, max_chars_per_file) if path else "[Fehler: kein Pfad angegeben]"
    if name == "search_codebase":
        query = str(args.get("query") or "").strip()
        max_results = max(1, min(int(args.get("max_results") or 8), 20))
        return _tool_search_codebase(query, max_results, repo_root) if query else "[Fehler: kein Suchbegriff angegeben]"
    mapped_name = _CODECOMPASS_CHAT_TOOL_MAP.get(name)
    if not mapped_name:
        return f"[Unbekanntes Tool: {name}]"

    from agent.services.ananta_tool_policy_service import get_ananta_tool_policy_service
    from agent.services.tools import execute_ananta_tool

    decision = get_ananta_tool_policy_service().evaluate(
        tool_name=mapped_name,
        arguments=dict(args or {}),
        allowed_tools=list(_CODECOMPASS_CHAT_TOOL_MAP.values()),
        mutation_mode="read_only",
    )
    if not decision.allowed:
        return json.dumps({"status": "blocked", "policy": decision.as_dict()}, ensure_ascii=False, sort_keys=True)
    result = execute_ananta_tool(
        tool_name=mapped_name,
        arguments=dict(args or {}),
        workspace_dir=str(repo_root),
        tool_call_id=f"snake-{name}",
        config={"codecompass_capability": _snake_codecompass_capability(repo_root)}
        if mapped_name == "codecompass.retrieve" else None,
    )
    return json.dumps(result, ensure_ascii=False, sort_keys=True)[:20_000]
