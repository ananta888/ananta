"""AWTCL-014: CodeCompass tools for the ananta-worker tool loop.

``codecompass.search`` reuses the same RagHelper retrieval path as
``ContextDeliveryService``; ``codecompass.expand_graph`` and
``codecompass.architecture_query`` go through the CodeCompass graph
store of the latest completed knowledge index (or an explicitly
requested one). All results are bounded; missing indexes degrade to an
error ToolResult with a warning instead of raising.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.tools._evidence import (
    EVIDENCE_KIND_GRAPH_PATH,
    EVIDENCE_KIND_RETRIEVAL_CHUNK,
    build_evidence_entry,
    build_tool_result,
)

_GRAPH_INDEX_FILENAME = "codecompass-graph.jsonl"
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


def codecompass_search(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.search", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    limit = max(1, min(int(args.get("limit") or 8), _MAX_SEARCH_LIMIT))
    try:
        from agent.services.rag_helper_index_service import get_rag_helper_index_service

        chunks = get_rag_helper_index_service().retrieve(profile=None, query=query, limit=limit)
    except Exception as exc:
        return build_tool_result(
            tool_name="codecompass.search",
            tool_call_id=tool_call_id,
            status="error",
            error=f"retrieval_unavailable:{exc}",
            warnings=["codecompass_index_unavailable"],
        )
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner

    planner = get_codecompass_context_planner()
    evidence: list[dict[str, Any]] = []
    location_refs: list[dict[str, Any]] = []
    for chunk in list(chunks or [])[:limit]:
        if not isinstance(chunk, dict):
            continue
        ref = planner.location_ref_from_hit(chunk)
        if ref is not None:
            location_refs.append(ref)
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_RETRIEVAL_CHUNK,
            path=str(chunk.get("path") or chunk.get("source") or ""),
            excerpt=str(chunk.get("content") or chunk.get("text") or chunk.get("snippet") or ""),
            score=float(chunk.get("score") or 0.0),
            source=str(chunk.get("source") or "codecompass"),
            max_excerpt_chars=1500,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.search",
        tool_call_id=tool_call_id,
        status="ok",
        evidence=evidence,
        data={"hit_count": len(evidence), "location_refs": location_refs},
        warnings=([] if evidence else ["no_results"]),
    )


def codecompass_plan_context(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    query = str(args.get("query") or "").strip()
    if not query:
        return build_tool_result(
            tool_name="codecompass.plan_context", tool_call_id=tool_call_id, status="error", error="query_required"
        )
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner

    bundle = get_codecompass_context_planner().plan_context(
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


def _resolve_graph_store(arguments: dict[str, Any]):
    """Open the graph store for the requested or latest completed index."""
    from agent.services.repository_registry import get_repository_registry

    repo = get_repository_registry().knowledge_index_repo
    requested = str((arguments or {}).get("knowledge_index_id") or "").strip()
    candidates = []
    if requested:
        index = repo.get_by_id(requested)
        if index is not None:
            candidates = [index]
    else:
        candidates = list(repo.list_completed() or [])
    for index in candidates:
        output_dir = str(getattr(index, "output_dir", "") or "").strip()
        if not output_dir:
            continue
        index_path = Path(output_dir) / _GRAPH_INDEX_FILENAME
        if not index_path.exists():
            continue
        from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

        return CodeCompassGraphStore(index_path=index_path), str(getattr(index, "id", "") or "")
    return None, None


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
    from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph
    from agent.services.codecompass_context_planner_service import get_codecompass_context_planner

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
    from worker.retrieval.codecompass_architecture_query import run_architecture_query

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


def _semantic_feature_enabled() -> bool:
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config

    return load_semantic_translation_config().enabled


def codecompass_semantic_equivalents(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    target_languages = [str(item).strip().lower() for item in list(args.get("target_languages") or ["typescript", "kotlin"]) if str(item).strip()]
    symbol = str(args.get("symbol") or "").strip()
    file = str(args.get("file") or "").strip()
    language = str(args.get("language") or "java").strip().lower()
    semantic_kind = str(args.get("semantic_kind") or "").strip().lower()
    try:
        store, index_id = _resolve_graph_store(args)
    except Exception as exc:
        store, index_id = None, None
        unavailable_reason = str(exc)
    else:
        unavailable_reason = "semantic_translation_index_unavailable"
    semantic_nodes: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    if store is not None:
        payload = store.load()
        diagnostics = dict((payload.get("diagnostics") or {}).get("semantic_translation") or {})
        semantic_nodes = store.find_semantic_nodes(symbol=symbol or None, file=file or None, language=language or None, semantic_kind=semantic_kind or None, limit=20)
    if store is None or diagnostics.get("status") != "ready":
        warnings = ["semantic_translation_index_unavailable"]
        semantic_nodes = []
    else:
        warnings = []
    from agent.codecompass.semantic_translation.equivalence_registry import EquivalenceRuleRegistry
    from agent.codecompass.semantic_translation.type_registry import TypeMappingRegistry

    rule_registry = EquivalenceRuleRegistry()
    type_registry = TypeMappingRegistry()
    target_constructs = []
    for node in semantic_nodes[:10]:
        attrs = dict(node.get("attributes") or {})
        for prop in attrs.get("properties") or []:
            target_constructs.extend(type_registry.find_by_source(str(prop.get("type") or ""), target_languages=target_languages))
    rules = []
    for target in target_languages:
        rules.extend(rule.as_record() for rule in rule_registry.find(source_language=language, target_language=target, semantic_kind=semantic_kind or "data_record"))
    evidence = []
    for node in semantic_nodes[:8]:
        entry, _ = build_evidence_entry(
            kind=EVIDENCE_KIND_GRAPH_PATH,
            path=str(node.get("file") or ""),
            excerpt=f"{node.get('semantic_kind')}:{node.get('symbol')}",
            source="codecompass.semantic_equivalents",
            max_excerpt_chars=300,
        )
        evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.semantic_equivalents",
        tool_call_id=tool_call_id,
        status="ok" if semantic_nodes or rules else "degraded",
        evidence=evidence,
        data={
            "knowledge_index_id": index_id,
            "semantic_nodes": semantic_nodes[:20],
            "equivalence_rules": rules[:20],
            "target_constructs": target_constructs[:30],
            "diagnostics": diagnostics or {"reason": unavailable_reason},
        },
        warnings=warnings,
        error="semantic_translation_index_unavailable" if warnings else None,
        max_total_chars=10000,
    )


def codecompass_translation_plan(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    if not _semantic_feature_enabled():
        return build_tool_result(
            tool_name="codecompass.translation_plan",
            tool_call_id=tool_call_id,
            status="error",
            error="semantic_translation_disabled",
            warnings=["ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED=false"],
        )
    source_path = str(args.get("source_path") or "").strip()
    source_code = str(args.get("source_code") or "").strip()
    target_language = str(args.get("target_language") or "typescript").strip().lower()
    if not source_path or not source_code:
        return build_tool_result(tool_name="codecompass.translation_plan", tool_call_id=tool_call_id, status="error", error="source_required")
    from agent.codecompass.semantic_translation.adapters import JavaSemanticAdapter
    from agent.codecompass.semantic_translation.transform import DeterministicTransformEngine, TransformRequest

    adapter = JavaSemanticAdapter()
    graph = adapter.emit_graph_records(source_path, source_code)
    artifact = DeterministicTransformEngine().transform(
        TransformRequest(
            source_path=source_path,
            source_code=source_code,
            target_language=target_language,
            allowed_rule_ids=tuple(str(item) for item in list(args.get("allowed_rule_ids") or [])),
        )
    )
    classification = artifact["status"] if artifact["status"] in {"safe_auto_transform", "needs_review", "unsupported"} else "needs_review"
    return build_tool_result(
        tool_name="codecompass.translation_plan",
        tool_call_id=tool_call_id,
        status="ok",
        data={
            "plan": {
                "classification": classification,
                "source_files": [source_path],
                "recognized_language_elements": [node for node in graph["nodes"][:40]],
                "applicable_rules": artifact.get("rule_ids") or [],
                "blocking_uncertainties": artifact.get("warnings") or [],
                "target_artifacts": [{"target_language": target_language, "kind": "code", "preview": artifact.get("target_code", "")[:2000]}],
                "test_strategy": ["run semantic translation golden samples", "run verifier before promotion"],
                "transform_artifact": artifact,
            }
        },
        warnings=list(artifact.get("warnings") or []),
        max_total_chars=12000,
    )


def codecompass_verify_translation(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    args = arguments or {}
    source_path = str(args.get("source_path") or "").strip()
    source_code = str(args.get("source_code") or "")
    target_code = str(args.get("target_code") or "")
    artifact = dict(args.get("transform_artifact") or {})
    if not source_path or not source_code or not target_code or not artifact:
        return build_tool_result(tool_name="codecompass.verify_translation", tool_call_id=tool_call_id, status="error", error="verification_inputs_required")
    from agent.codecompass.semantic_translation.verifier import SemanticTranslationVerifier

    result = SemanticTranslationVerifier().verify(source_path=source_path, source_code=source_code, target_code=target_code, transform_artifact=artifact)
    evidence = []
    entry, _ = build_evidence_entry(
        kind="semantic_translation_verification",
        path=source_path,
        excerpt=f"status={result.get('status')} rules={','.join(result.get('verified_rule_ids') or [])}",
        source="codecompass.verify_translation",
        max_excerpt_chars=500,
    )
    evidence.append(entry)
    return build_tool_result(
        tool_name="codecompass.verify_translation",
        tool_call_id=tool_call_id,
        status="ok" if result.get("status") in {"verified", "verified_with_warnings"} else "error",
        evidence=evidence,
        data={"verification": result},
        warnings=list(result.get("warnings") or []),
        error="translation_verification_failed" if result.get("status") == "failed" else None,
        max_total_chars=8000,
    )
