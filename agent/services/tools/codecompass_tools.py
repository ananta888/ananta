"""AWTCL-014: CodeCompass tools for the ananta-worker tool loop.

``codecompass.search`` reuses the same RagHelper retrieval path as
``ContextDeliveryService``; ``codecompass.expand_graph`` and
``codecompass.architecture_query`` go through the CodeCompass graph
store of the latest completed knowledge index (or an explicitly
requested one). All results are bounded; missing indexes degrade to an
error ToolResult with a warning instead of raising.

This module stays the public tool facade; semantic translation tools live in
``codecompass_semantic_translation_tools``, x86 tools in
``codecompass_x86_tools`` and graph store resolution in
``codecompass_graph_store_access`` (all re-exported here).
"""
from __future__ import annotations

from typing import Any, Mapping

from agent.services.tools._evidence import (
    EVIDENCE_KIND_GRAPH_PATH,
    EVIDENCE_KIND_RETRIEVAL_CHUNK,
    build_evidence_entry,
    build_tool_result,
)
from agent.services.tools.codecompass_graph_store_access import (
    resolve_graph_store as _resolve_graph_store,
)
from agent.services.tools.codecompass_graph_store_access import (
    resolve_graph_store_diagnostics as _resolve_graph_store_diagnostics,  # noqa: F401 - compatibility re-export
)
from agent.services.tools.codecompass_repository_tools import (
    codecompass_build_test_map as codecompass_build_test_map,
)
from agent.services.tools.codecompass_repository_tools import (
    codecompass_repository_query as codecompass_repository_query,
)
from agent.services.tools.codecompass_semantic_translation_tools import (
    _semantic_feature_enabled as _semantic_feature_enabled,
)
from agent.services.tools.codecompass_semantic_translation_tools import (
    codecompass_python_translation_plan as codecompass_python_translation_plan,
)
from agent.services.tools.codecompass_semantic_translation_tools import (
    codecompass_semantic_equivalents as codecompass_semantic_equivalents,
)
from agent.services.tools.codecompass_semantic_translation_tools import (
    codecompass_translation_plan as codecompass_translation_plan,
)
from agent.services.tools.codecompass_semantic_translation_tools import (
    codecompass_verify_translation as codecompass_verify_translation,
)
from agent.services.tools.codecompass_x86_tools import (  # noqa: F401 - compatibility re-exports
    _X86_EVIDENCE_KIND_BASIC_BLOCK,
    _X86_EVIDENCE_KIND_CALLSITE,
    _X86_EVIDENCE_KIND_CFG_EDGE,
    _X86_EVIDENCE_KIND_FUNCTION,
    _X86_EVIDENCE_KIND_INSTRUCTION,
    _x86_kind_evidence_kind,
)
from agent.services.tools.codecompass_x86_tools import (
    codecompass_x86_address_lookup as codecompass_x86_address_lookup,
)
from agent.services.tools.codecompass_x86_tools import (
    codecompass_x86_call_graph as codecompass_x86_call_graph,
)
from agent.services.tools.codecompass_x86_tools import (
    codecompass_x86_cfg as codecompass_x86_cfg,
)
from agent.services.tools.codecompass_x86_tools import (
    codecompass_x86_find as codecompass_x86_find,
)
from agent.services.tools.codecompass_x86_tools import (
    codecompass_x86_overview as codecompass_x86_overview,
)

_MAX_SEARCH_LIMIT = 20
_MAX_GRAPH_NODES = 40


def codecompass_resolve_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.resolve_context", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    from agent.services.codecompass_context_service import get_codecompass_context_service

    package = get_codecompass_context_service().resolve_context(
        query=query,
        task_kind=str(args.get("task_kind") or "").strip() or None,
        mode=str(args.get("mode") or "").strip() or None,
        working_files=[str(item) for item in list(args.get("working_files") or [])],
        domain_hint=str(args.get("domain_hint") or "").strip() or None,
        domain_scope=str(args.get("domain_scope") or "").strip() or None,
        max_tokens=args.get("max_tokens"),
        max_files=args.get("max_files"),
        include_original_files=bool(args.get("include_original_files", False)),
        include_jsonl_records=bool(args.get("include_jsonl_records", False)),
        include_graph=bool(args.get("include_graph", False)),
        llm_scope=str(args.get("llm_scope") or "").strip() or None,
        workspace_dir=workspace_dir,
    )
    return build_tool_result(
        tool_name="codecompass.resolve_context",
        tool_call_id=tool_call_id,
        status="ok" if not package.get("reason_code") else "error",
        data={"context_package": package},
        warnings=list(package.get("warnings") or []),
        error=str(package.get("reason_code") or "") or None,
        max_total_chars=12000,
    )


def codecompass_search_symbols(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.search_symbols", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    from agent.services.codecompass_context_service import get_codecompass_context_service

    result = get_codecompass_context_service().search_symbols(
        query=query,
        record_kinds=[str(item) for item in list(args.get("record_kinds") or [])],
        path_globs=[str(item) for item in list(args.get("path_globs") or [])],
        domain_hint=str(args.get("domain_hint") or "").strip() or None,
        limit=int(args.get("limit") or 20),
    )
    evidence = []
    for record in list(result.get("records") or [])[:10]:
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_RETRIEVAL_CHUNK,
            path=str(record.get("path") or ""),
            excerpt=str(record.get("excerpt") or record.get("symbol") or ""),
            source="codecompass.search_symbols",
            score=record.get("score"),
            max_excerpt_chars=500,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.search_symbols",
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") == "ok" else "degraded",
        evidence=evidence,
        data={"search_result": result},
        warnings=list(result.get("warnings") or []),
    )


def codecompass_get_file_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    paths = [str(item) for item in list(args.get("paths") or []) if str(item or "").strip()]
    if not paths:
        return build_tool_result(
            tool_name="codecompass.get_file_context", tool_call_id=tool_call_id, status="error", error="paths_required"
        )
    from agent.services.codecompass_context_service import get_codecompass_context_service

    result = get_codecompass_context_service().get_file_context(
        paths=paths,
        line_ranges=[dict(item) for item in list(args.get("line_ranges") or []) if isinstance(item, dict)],
        max_bytes_per_file=args.get("max_bytes_per_file"),
        max_total_bytes=args.get("max_total_bytes"),
        redaction_mode=str(args.get("redaction_mode") or "auto"),
        reason=str(args.get("reason") or "").strip() or None,
        workspace_dir=workspace_dir,
    )
    evidence = []
    for row in list(result.get("context_files") or [])[:8]:
        entry, _ = build_evidence_entry(
            kind="file_context",
            path=str(row.get("path") or ""),
            excerpt=str(row.get("content") or ""),
            source="codecompass.get_file_context",
            max_excerpt_chars=800,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.get_file_context",
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") == "ok" else "error",
        evidence=evidence,
        data={"file_context_result": result},
        warnings=list(result.get("warnings") or []),
        error=str(result.get("error") or "") or None,
        max_total_chars=12000,
    )


def codecompass_get_domain_map(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    from agent.services.codecompass_context_service import get_codecompass_context_service

    result = get_codecompass_context_service().get_domain_map(
        domain_hint=str(args.get("domain_hint") or "").strip() or None,
        include_files=bool(args.get("include_files", True)),
        include_edges=bool(args.get("include_edges", False)),
        max_entries=int(args.get("max_entries") or 20),
    )
    evidence = []
    for row in list((result.get("domain_map") or {}).get("key_files") or [])[:10]:
        entry, _ = build_evidence_entry(
            kind="domain_file",
            path=str(row.get("path") or ""),
            excerpt=str(row.get("reason") or ""),
            source="codecompass.get_domain_map",
            score=row.get("score"),
            max_excerpt_chars=300,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.get_domain_map",
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") == "ok" else "degraded",
        evidence=evidence,
        data={"domain_map_result": result},
        warnings=list(result.get("warnings") or []),
    )


def _retrieval_status(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "error")
    if status == "ok":
        return "ok"
    if status in {"degraded", "empty"}:
        return "degraded" if result.get("evidence") else ("ok" if status == "empty" else "degraded")
    return "error"


def _trusted_capability(config: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Return authority injected by the hub, never authority from tool arguments."""

    if not isinstance(config, Mapping):
        return None
    capability = config.get("codecompass_capability")
    return capability if isinstance(capability, Mapping) else None


def codecompass_retrieve(
    *,
    workspace_dir: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    args = dict(arguments or {})
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.retrieve",
            tool_call_id=tool_call_id,
            status="error",
            error="query_required",
        )
    from agent.services.codecompass_agentic_retrieval_service import (
        get_codecompass_agentic_retrieval_service,
    )

    result = get_codecompass_agentic_retrieval_service().retrieve_from_tool_args(
        args,
        capability=_trusted_capability(config),
    )
    evidence = []
    for item in list(result.get("evidence") or [])[:10]:
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_RETRIEVAL_CHUNK,
            path=str(item.get("path") or ""),
            line_start=item.get("line_start"),
            line_end=item.get("line_end"),
            excerpt=str(item.get("excerpt") or ""),
            score=item.get("score"),
            source=str(item.get("source") or "codecompass"),
            max_excerpt_chars=1500,
        )
        evidence.append(entry)
    warnings = list(result.get("warnings") or [])
    if not result.get("evidence"):
        warnings.append("no_results")
    return build_tool_result(
        tool_name="codecompass.retrieve",
        tool_call_id=tool_call_id,
        status=_retrieval_status(result),
        evidence=evidence,
        data={"retrieval": result},
        warnings=warnings,
        error=str(result.get("reason_code") or "") or None,
        max_total_chars=12000,
    )


def codecompass_search(
    *,
    workspace_dir: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.search", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    limit = max(1, min(int(args.get("limit") or 8), _MAX_SEARCH_LIMIT))
    search_args = dict(args)
    search_args["limit"] = limit
    from agent.services.codecompass_agentic_retrieval_service import (
        get_codecompass_agentic_retrieval_service,
    )
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner

    result = get_codecompass_agentic_retrieval_service().retrieve_from_tool_args(
        search_args,
        capability=_trusted_capability(config),
    )
    if result.get("status") == "error" and result.get("reason_code") not in {"no_result", ""}:
        return build_tool_result(
            tool_name="codecompass.search",
            tool_call_id=tool_call_id,
            status="error",
            error=str(result.get("reason_code") or "retrieval_unavailable"),
            warnings=list(result.get("warnings") or []),
        )
    planner = get_codecompass_context_planner()
    evidence: list[dict[str, Any]] = []
    location_refs: list[dict[str, Any]] = []
    for item in list(result.get("evidence") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        ref = planner.location_ref_from_hit(
            {
                "path": item.get("path"),
                "score": item.get("score"),
                "symbol": item.get("symbol"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "id": item.get("id"),
            }
        )
        if ref is not None:
            location_refs.append(ref)
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_RETRIEVAL_CHUNK,
            path=str(item.get("path") or ""),
            excerpt=str(item.get("excerpt") or ""),
            score=float(item.get("score") or 0.0),
            source=str(item.get("source") or "codecompass"),
            max_excerpt_chars=1500,
        )
        evidence.append(entry)
    warnings = list(result.get("warnings") or [])
    if not evidence:
        warnings.append("no_results")
    return build_tool_result(
        tool_name="codecompass.search",
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") != "error" else "error",
        evidence=evidence,
        data={
            "hit_count": len(evidence),
            "location_refs": location_refs,
            "retrieval": result,
        },
        warnings=warnings,
    )


def codecompass_plan_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.plan_context", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner

    planner = get_codecompass_context_planner()
    intent = str(args.get("intent") or "").strip()
    try:
        if intent:
            bundle = planner.plan_editor_context(
                query_input={
                    "intent": intent,
                    "detail_level": str(args.get("detail_level") or "conversation"),
                    "registry_version": args.get("registry_version"),
                    "node_kind": args.get("node_kind"),
                    "field_path": args.get("field_path"),
                    "backend_contract": args.get("backend_contract"),
                    "symbols": args.get("symbols") or [],
                    "graph_neighbors": args.get("graph_neighbors") or [],
                    "user_language": query,
                },
                workspace_dir=workspace_dir,
                include_neighbors=bool(args.get("include_neighbors", True)),
            )
        else:
            bundle = planner.plan_context(
                query=query,
                task_kind=str(args.get("task_kind") or "").strip() or None,
                budget={
                    "max_ranges": args.get("max_ranges"),
                    "max_lines_per_range": args.get("max_lines_per_range"),
                    "max_neighbors": args.get("max_neighbors"),
                },
                workspace_dir=workspace_dir,
                include_neighbors=bool(args.get("include_neighbors", True)),
            )
    except (TypeError, ValueError) as exc:
        return build_tool_result(
            tool_name="codecompass.plan_context",
            tool_call_id=tool_call_id,
            status="error",
            error=str(exc),
        )
    evidence: list[dict[str, Any]] = []
    for ref in list(bundle.get("location_refs") or [])[:10]:
        entry, _ = build_evidence_entry(
            kind="location_ref",
            path=str(ref.get("path") or ""),
            line_start=int(ref.get("line_start") or 1),
            line_end=int(ref.get("line_end") or 1),
            excerpt=f"{ref.get('symbol') or ''} {ref.get('reason') or ''}".strip(),
            source=str(ref.get("source") or "codecompass"),
            score=ref.get("score"),
            max_excerpt_chars=300,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.plan_context",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data={"context_bundle": bundle},
        warnings=list(bundle.get("warnings") or []),
        max_total_chars=6000,
    )


def codecompass_expand_graph(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    node = str(args.get("node") or "").strip()
    if not node:
        seeds = [str(seed).strip() for seed in list(args.get("seeds") or []) if str(seed).strip()]
        node = seeds[0] if seeds else ""
    if not node:
        return build_tool_result(
            tool_name="codecompass.expand_graph", tool_call_id=tool_call_id, status="error", error="node_required"
        )
    try:
        store, index_id = _resolve_graph_store(args)
    except Exception as exc:
        store, index_id = None, None
        unavailable_reason = str(exc)
    else:
        unavailable_reason = "no_completed_graph_index"
    if store is None:
        return build_tool_result(
            tool_name="codecompass.expand_graph",
            tool_call_id=tool_call_id,
            status="error",
            error=f"graph_unavailable:{unavailable_reason}",
            warnings=["codecompass_graph_unavailable"],
        )
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner
    from ananta_codecompass.graph_expansion import expand_codecompass_graph

    profile = str(args.get("profile") or "bugfix_local").strip() or "bugfix_local"
    expansion = expand_codecompass_graph(store=store, seed_node_ids=[node], profile=profile)
    nodes = list(expansion.get("nodes") or [])[:_MAX_GRAPH_NODES]
    planner = get_codecompass_context_planner()
    location_refs = []
    evidence: list[dict[str, Any]] = []
    for row in nodes:
        ref = planner.location_ref_from_node(row)
        if ref is not None:
            location_refs.append(ref)
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_GRAPH_PATH,
            path=str(row.get("file") or ""),
            excerpt=f"{row.get('kind')}:{row.get('name') or row.get('id')}",
            source="codecompass_graph",
            max_excerpt_chars=300,
        )
        evidence.append(entry)
    warnings = [str(item) for item in list(expansion.get("warnings") or [])]
    if len(list(expansion.get("nodes") or [])) > _MAX_GRAPH_NODES:
        warnings.append("graph_nodes_truncated")
    return build_tool_result(
        tool_name="codecompass.expand_graph",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data={
            "knowledge_index_id": index_id,
            "node_count": len(nodes),
            "paths": list(expansion.get("paths") or [])[:_MAX_GRAPH_NODES],
            "allowed_edge_types": list(expansion.get("allowed_edge_types") or []),
            "location_refs": location_refs,
        },
        warnings=warnings,
    )


def codecompass_architecture_query(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query_type = str(args.get("query_type") or args.get("question") or "").strip()
    seed = str(args.get("seed") or "").strip()
    if not query_type:
        return build_tool_result(
            tool_name="codecompass.architecture_query",
            tool_call_id=tool_call_id,
            status="error",
            error="query_type_required",
        )
    try:
        store, index_id = _resolve_graph_store(args)
    except Exception as exc:
        store, index_id = None, None
        unavailable_reason = str(exc)
    else:
        unavailable_reason = "no_completed_graph_index"
    if store is None:
        return build_tool_result(
            tool_name="codecompass.architecture_query",
            tool_call_id=tool_call_id,
            status="error",
            error=f"graph_unavailable:{unavailable_reason}",
            warnings=["codecompass_graph_unavailable"],
        )
    from ananta_codecompass.architecture_query import run_architecture_query

    result = run_architecture_query(
        store=store,
        query_type=query_type,
        seed=seed,
        field=str(args.get("field") or "").strip() or None,
        depth=int(args["depth"]) if args.get("depth") is not None else None,
        direction=str(args.get("direction") or "").strip() or None,
    )
    evidence: list[dict[str, Any]] = []
    for row in list(result.get("results") or [])[:10]:
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_GRAPH_PATH,
            path=str((row.get("node") or {}).get("file") or row.get("file") or ""),
            excerpt=str(row.get("summary") or row.get("role") or row)[:500],
            source="codecompass_architecture_query",
            max_excerpt_chars=500,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.architecture_query",
        tool_call_id=tool_call_id,
        status="ok" if not result.get("error") else "error",
        evidence=evidence,
        data={"knowledge_index_id": index_id, "query_result": result},
        warnings=[str(item) for item in list(result.get("warnings") or [])],
        error=str(result.get("error") or "") or None,
    )
