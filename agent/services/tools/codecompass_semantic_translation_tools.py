"""CodeCompass semantic translation tools (equivalents, plans, verification)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.tools._evidence import (
    EVIDENCE_KIND_GRAPH_PATH,
    build_evidence_entry,
    build_tool_result,
)
from agent.services.tools.codecompass_graph_store_access import (
    GraphStoreResolver,
    resolve_graph_store,
)


def _semantic_feature_enabled() -> bool:
    from agent.codecompass.semantic_translation.config import load_semantic_translation_config

    return load_semantic_translation_config().enabled


def codecompass_semantic_equivalents(
    *,
    workspace_dir: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    resolve_graph_store: GraphStoreResolver = resolve_graph_store,
) -> dict[str, Any]:
    args = arguments or {}
    target_languages = [str(item).strip().lower() for item in list(args.get("target_languages") or ["typescript", "kotlin"]) if str(item).strip()]
    symbol = str(args.get("symbol") or "").strip()
    file = str(args.get("file") or "").strip()
    language = str(args.get("language") or "java").strip().lower()
    semantic_kind = str(args.get("semantic_kind") or "").strip().lower()
    try:
        store, index_id = resolve_graph_store(args)
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
        status="ok" if not warnings and (semantic_nodes or rules) else "degraded",
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
    from agent.codecompass.semantic_translation.registry import get_semantic_adapter_registry
    from agent.codecompass.semantic_translation.transform import DeterministicTransformEngine, TransformRequest

    semantic_executor = get_semantic_adapter_registry()
    graph = semantic_executor.emit_graph_records_for_language(
        "java",
        source_path,
        source_code,
    )
    artifact = DeterministicTransformEngine(semantic_executor=semantic_executor).transform(
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


def codecompass_python_translation_plan(*, workspace_dir: str, arguments: dict[str, Any], tool_call_id: str) -> dict[str, Any]:
    """PYJR-024: Python → Java/Rust translation plan tool."""
    args = arguments or {}
    if not _semantic_feature_enabled():
        return build_tool_result(
            tool_name="codecompass.python_translation_plan",
            tool_call_id=tool_call_id,
            status="error",
            error="semantic_translation_disabled",
            warnings=["ANANTA_CODECOMPASS_SEMANTIC_TRANSLATION_ENABLED=false"],
        )

    source_code = str(args.get("source_code") or "").strip()
    source_path = str(args.get("source_path") or "<stdin>").strip()
    target = str(args.get("target") or "both").strip().lower()
    symbol_filter = str(args.get("symbol") or "").strip()

    if not source_code and source_path and source_path != "<stdin>":
        try:
            full_path = Path(workspace_dir) / source_path if not Path(source_path).is_absolute() else Path(source_path)
            source_code = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return build_tool_result(
                tool_name="codecompass.python_translation_plan",
                tool_call_id=tool_call_id,
                status="error",
                error=f"source_file_read_failed: {exc}",
            )

    if not source_code:
        return build_tool_result(
            tool_name="codecompass.python_translation_plan",
            tool_call_id=tool_call_id,
            status="error",
            error="source_code_or_path_required",
        )

    if target not in ("java", "rust", "both"):
        return build_tool_result(
            tool_name="codecompass.python_translation_plan",
            tool_call_id=tool_call_id,
            status="error",
            error="invalid_target: must be java, rust, or both",
        )

    from agent.codecompass.semantic_translation.python_transform import PythonTranslationPlanService

    plan = PythonTranslationPlanService().create_plan(source_code, source_path, target)

    # Optionally filter by symbol
    entries = plan.entries
    if symbol_filter:
        entries = [e for e in entries if symbol_filter in e.symbol]

    plan_dict = plan.as_dict()
    plan_dict["entries"] = [e.as_dict() for e in entries]

    evidence = []
    entry, _ = build_evidence_entry(
        kind="python_translation_plan",
        path=source_path,
        excerpt=f"target={target} entries={len(entries)} blockers={len(plan.dynamic_blockers)} safe={plan.is_fully_safe}",
        source="codecompass.python_translation_plan",
        max_excerpt_chars=400,
    )
    evidence.append(entry)

    warnings = list(plan.warnings)
    has_blockers = bool(plan.dynamic_blockers)
    status = "ok" if not has_blockers else "degraded"

    return build_tool_result(
        tool_name="codecompass.python_translation_plan",
        tool_call_id=tool_call_id,
        status=status,
        evidence=evidence,
        data={"plan": plan_dict},
        warnings=warnings,
        error="dynamic_runtime_blockers_detected" if has_blockers else None,
        max_total_chars=14000,
    )
