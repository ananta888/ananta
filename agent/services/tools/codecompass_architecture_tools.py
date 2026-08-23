"""Hierarchical architecture tools for the worker loop and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.tools._evidence import build_evidence_entry, build_tool_result


def _capability(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = (arguments or {}).get("capability")
    return dict(raw) if isinstance(raw, dict) else None


def _load_architecture_graph(
    arguments: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records, edges, _diagnostics = _load_architecture_graph_diagnostics(arguments)
    return records, edges


def _load_architecture_graph_diagnostics(
    arguments: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    from agent.services.tools.codecompass_tools import (
        _resolve_graph_store_diagnostics,
    )

    store, _index_id, diagnostics = _resolve_graph_store_diagnostics(
        arguments or {}
    )
    if store is None:
        return [], [], diagnostics
    payload = store.load() if hasattr(store, "load") else {}
    nodes = [dict(item) for item in list((payload or {}).get("nodes") or []) if isinstance(item, dict)]
    edges = [dict(item) for item in list((payload or {}).get("edges") or []) if isinstance(item, dict)]
    return nodes, edges, diagnostics


def _slice_result(
    *,
    tool_name: str,
    tool_call_id: str,
    query: str,
    profile: str,
    focus_handle: str | None = None,
    arguments: dict[str, Any] | None = None,
    include_diagram: bool = False,
) -> dict[str, Any]:
    from agent.services.codecompass_architecture_slice_service import (
        get_codecompass_architecture_slice_service,
        decode_handle,
    )

    records, edges = _load_architecture_graph(arguments)
    if not records:
        _records, _edges, resolution_diagnostics = (
            _load_architecture_graph_diagnostics(arguments)
        )
        return build_tool_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status="degraded",
            error="architecture_graph_unavailable",
            warnings=["architecture_graph_unavailable"],
            data={"resolution_diagnostics": resolution_diagnostics},
        )
    capability = _capability(arguments) or {}
    revision = str(capability.get("revision") or (arguments or {}).get("revision") or "local")
    capability.setdefault("revision", revision)
    capability.setdefault("workspace_id", str((arguments or {}).get("workspace_id") or "local"))
    focus_id = None
    if focus_handle:
        if str(focus_handle).startswith("hac:"):
            try:
                focus_id = decode_handle(focus_handle, revision=revision)
            except ValueError as exc:
                return build_tool_result(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status="error",
                    error=str(exc),
                )
        else:
            focus_id = str(focus_handle)
    slice_payload = get_codecompass_architecture_slice_service().build_slice(
        query=query,
        records=records,
        edges=edges,
        capability=capability,
        profile=profile,
        focus_node_id=focus_id,
    )
    evidence = []
    for node in list(slice_payload.get("nodes") or [])[:8]:
        entry, _ = build_evidence_entry(
            kind="architecture_node",
            path=str(node.get("path") or ""),
            excerpt=str(node.get("short_summary") or node.get("title") or ""),
            source="codecompass.architecture",
            max_excerpt_chars=400,
        )
        evidence.append(entry)
    data: dict[str, Any] = {"architecture": slice_payload}
    if include_diagram:
        from agent.services.codecompass_architecture_diagram_service import (
            get_codecompass_architecture_diagram_service,
        )

        data["diagram"] = get_codecompass_architecture_diagram_service().render(
            slice_payload,
            kind=str((arguments or {}).get("diagram_kind") or "component"),
        )
    return build_tool_result(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data=data,
        warnings=list(slice_payload.get("warnings") or []),
        max_total_chars=8000,
    )


def codecompass_architecture_overview(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    query = str((arguments or {}).get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.architecture_overview",
            tool_call_id=tool_call_id,
            status="error",
            error="query_required",
        )
    return _slice_result(
        tool_name="codecompass.architecture_overview",
        tool_call_id=tool_call_id,
        query=query,
        profile=str((arguments or {}).get("profile") or "overview"),
        arguments=arguments,
    )


def codecompass_architecture_expand(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    handle = str((arguments or {}).get("handle") or (arguments or {}).get("node") or "").strip()
    if not handle:
        return build_tool_result(
            tool_name="codecompass.architecture_expand",
            tool_call_id=tool_call_id,
            status="error",
            error="handle_required",
        )
    return _slice_result(
        tool_name="codecompass.architecture_expand",
        tool_call_id=tool_call_id,
        query=str((arguments or {}).get("query") or handle),
        profile="component",
        focus_handle=handle,
        arguments=arguments,
    )


def codecompass_component_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    return codecompass_architecture_expand(
        workspace_dir=workspace_dir, arguments=arguments, tool_call_id=tool_call_id
    )


def codecompass_architecture_dependencies(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    handle = str((arguments or {}).get("handle") or "").strip()
    if not handle:
        return build_tool_result(
            tool_name="codecompass.architecture_dependencies",
            tool_call_id=tool_call_id,
            status="error",
            error="handle_required",
        )
    return _slice_result(
        tool_name="codecompass.architecture_dependencies",
        tool_call_id=tool_call_id,
        query=str((arguments or {}).get("query") or handle),
        profile="component",
        focus_handle=handle,
        arguments=arguments,
    )


def codecompass_symbol_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    from agent.services.codecompass_symbol_context_service import (
        build_codecompass_symbol_context,
        format_symbol_context_section,
    )

    query = str((arguments or {}).get("query") or (arguments or {}).get("symbol") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.symbol_context",
            tool_call_id=tool_call_id,
            status="error",
            error="query_required",
        )
    records, _edges = _load_architecture_graph(arguments)
    provided_sources = list((arguments or {}).get("ranked_sources") or [])
    ranked_sources = [
        {
            "source": str(item.get("source") or item.get("path") or ""),
            "score": float(item.get("score") or (100 - index)),
        }
        for index, item in enumerate(provided_sources)
        if isinstance(item, dict)
        and str(item.get("source") or item.get("path") or "").endswith(
            (".py", ".ts", ".tsx", ".js", ".java")
        )
    ][:20]
    if not ranked_sources:
        ranked_sources = [
            {
                "source": str(node.get("path") or ""),
                "score": float(node.get("score") or (100 - index)),
            }
            for index, node in enumerate(records)
            if str(node.get("path") or "").endswith(
                (".py", ".ts", ".tsx", ".js", ".java")
            )
        ][:20]
    snippets = build_codecompass_symbol_context(
        repo_root=Path(workspace_dir),
        query=query,
        ranked_sources=ranked_sources,
        max_snippets=8,
    )
    evidence = []
    for snippet in snippets:
        entry, _ = build_evidence_entry(
            kind="symbol",
            path=snippet.path,
            line_start=snippet.line_start,
            line_end=snippet.line_end,
            excerpt=snippet.content,
            source=snippet.source,
            max_excerpt_chars=1200,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.symbol_context",
        tool_call_id=tool_call_id,
        status="ok" if snippets else "degraded",
        warnings=[] if snippets else ["symbol_context_unavailable"],
        evidence=evidence,
        data={
            "query": query,
            "symbol_count": len(snippets),
            "formatted_context": format_symbol_context_section(snippets),
        },
        max_total_chars=10000,
    )


def codecompass_architecture_evidence(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    return _slice_result(
        tool_name="codecompass.architecture_evidence",
        tool_call_id=tool_call_id,
        query=str((arguments or {}).get("query") or ""),
        profile="evidence",
        arguments=arguments,
    )


def codecompass_architecture_diagram(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    query = str((arguments or {}).get("query") or "architecture").strip()
    return _slice_result(
        tool_name="codecompass.architecture_diagram",
        tool_call_id=tool_call_id,
        query=query,
        profile=str((arguments or {}).get("profile") or "overview"),
        arguments=arguments,
        include_diagram=True,
    )
