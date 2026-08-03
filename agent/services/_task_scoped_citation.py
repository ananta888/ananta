"""Citation / grounded-answer cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
citation_grounded cluster of SPLIT-001 (sub-split 001j). The module owns
source-catalog construction, the citation-contract prompt, grounded-answer
payload extraction, flow-metrics payloads, planner observability payloads,
retrieval-trace links, and task-flow-metrics persistence.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import json
import time
from typing import Any

from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile


def build_flow_metrics_payload(
    *,
    run_id: str | None,
    phase: str,
    propose_ok: bool | None,
    execute_ok: bool | None,
    artifact_created: bool | None,
    worker_profile: str | None = None,
    profile_source: str | None = None,
    policy_classification: str | None = None,
    retrieval_cache_hit: bool | None = None,
    retrieval_latency_ms: int | None = None,
    retrieval_quality_score: float | None = None,
) -> dict:
    return {
        "run_id": str(run_id or "").strip() or None,
        "phase": str(phase or "").strip() or None,
        "propose_ok": None if propose_ok is None else bool(propose_ok),
        "execute_ok": None if execute_ok is None else bool(execute_ok),
        "artifact_created": None if artifact_created is None else bool(artifact_created),
        "worker_profile": normalize_worker_execution_profile(worker_profile),
        "profile_source": str(profile_source or "agent_default").strip().lower() or "agent_default",
        "policy_classification": str(policy_classification or "").strip().lower() or None,
        "retrieval_cache_hit": None if retrieval_cache_hit is None else bool(retrieval_cache_hit),
        "retrieval_latency_ms": None if retrieval_latency_ms is None else int(retrieval_latency_ms),
        "retrieval_quality_score": None if retrieval_quality_score is None else float(retrieval_quality_score),
    }


def build_planner_observability_payload(
    *,
    trigger: str | None,
    policy_decision_ref: str | None,
    plan_diff: dict | None,
) -> dict:
    return {
        "trigger": str(trigger or "").strip().lower() or "unknown",
        "policy_decision_ref": str(policy_decision_ref or "").strip() or None,
        "plan_diff": dict(plan_diff or {}),
    }


def extract_retrieval_trace_link(context_payload: dict | None) -> dict[str, str | None]:
    payload = dict(context_payload or {})
    metadata = dict(payload.get("bundle_metadata") or {})
    retrieval_trace = dict(metadata.get("retrieval_trace") or {})
    selection_trace = dict(metadata.get("selection_trace") or {})
    trace_id = str(
        retrieval_trace.get("trace_id")
        or selection_trace.get("retrieval_trace_id")
        or selection_trace.get("trace_id")
        or ""
    ).strip() or None
    context_hash = str(
        retrieval_trace.get("context_hash")
        or metadata.get("context_hash")
        or ""
    ).strip() or None
    manifest_hash = str(
        retrieval_trace.get("manifest_hash")
        or metadata.get("manifest_hash")
        or ""
    ).strip() or None
    return {
        "retrieval_trace_id": trace_id,
        "retrieval_context_hash": context_hash,
        "retrieval_manifest_hash": manifest_hash,
    }


def build_source_catalog_from_execution_context(
    *,
    tid: str,
    task: dict,
    llm_scope: str = "local_only",
) -> dict | None:
    execution_context = dict((task or {}).get("worker_execution_context") or {})
    context_payload = dict(execution_context.get("context") or {})
    chunks = [dict(item) for item in list(context_payload.get("chunks") or []) if isinstance(item, dict)]
    if not chunks:
        return None
    selected: list[dict[str, Any]] = []
    for chunk in chunks:
        metadata = dict(chunk.get("metadata") or {})
        identity_verification = dict(
            metadata.get("source_id_verification") or {}
        )
        identity_verified = (
            metadata.get("source_id_verified") is True
            and identity_verification.get("verified") is True
            and str(identity_verification.get("status") or "")
            == "verified"
            and str(identity_verification.get("reason_code") or "")
            == "source_id_verified"
        )
        if not identity_verified:
            # The normalized retrieval contract is the Hub verification
            # boundary.  Keep the candidate observable, but remove any
            # unverified identity value before SourceCatalogService sees it.
            metadata.pop("source_id", None)
            metadata.pop("registry_source_id", None)
        source_provenance = metadata.get("source_provenance")
        if not isinstance(source_provenance, dict):
            source_provenance = metadata.get("provenance")
        selected.append(
            {
                # One candidate carries the complete provider-issued identity
                # and its location/provenance projection.  A second,
                # identity-less provenance row would turn an otherwise valid
                # catalog into a degraded one.
                "source_id": str(
                    (
                        chunk.get("source_id")
                        or metadata.get("source_id")
                        or metadata.get("registry_source_id")
                        or ""
                    )
                    if identity_verified
                    else ""
                ),
                "source_version": str(
                    chunk.get("source_version")
                    or metadata.get("source_version")
                    or ""
                ),
                "tenant_id": str(
                    chunk.get("tenant_id")
                    or metadata.get("tenant_id")
                    or ""
                ),
                "scope": str(
                    chunk.get("scope")
                    or metadata.get("scope")
                    or metadata.get("source_scope")
                    or ""
                ),
                "provenance_digest": str(
                    chunk.get("provenance_digest")
                    or metadata.get("provenance_digest")
                    or ""
                ),
                "provenance": (
                    dict(source_provenance)
                    if isinstance(source_provenance, dict)
                    else {}
                ),
                "path": str(chunk.get("source") or metadata.get("file") or ""),
                "record_id": str(metadata.get("record_id") or chunk.get("record_id") or ""),
                # Never substitute a record ID or path for a content digest.
                # Missing provider content hashes remain a fail-closed
                # rejection in SourceCatalogService.
                "content_hash": str(
                    chunk.get("content_hash")
                    or metadata.get("content_hash")
                    or ""
                ),
                "channel": str(metadata.get("channel") or metadata.get("engine") or ""),
                "kind": str(metadata.get("record_kind") or metadata.get("kind") or ""),
                "manifest_hash": str(metadata.get("source_manifest_hash") or ""),
                "line_start": metadata.get("line_start", metadata.get("start_line")),
                "line_end": metadata.get("line_end", metadata.get("end_line")),
                "sensitivity": str(metadata.get("sensitivity") or "internal"),
                "metadata": metadata,
            }
        )
    bundle_metadata = dict(context_payload.get("bundle_metadata") or {})
    retrieval_trace = dict(bundle_metadata.get("retrieval_trace") or {})
    if not retrieval_trace:
        selection_trace = dict(bundle_metadata.get("selection_trace") or {})
        if selection_trace:
            retrieval_trace = {
                "trace_id": selection_trace.get("retrieval_trace_id")
                or selection_trace.get("trace_id"),
                "context_hash": selection_trace.get("context_hash"),
                "manifest_hash": selection_trace.get("manifest_hash"),
                "tenant_id": selection_trace.get("tenant_id"),
                "scope": selection_trace.get("scope"),
            }
    if not retrieval_trace:
        retrieval_trace = dict(context_payload.get("retrieval_trace") or {})
    from agent.services.source_catalog_service import get_source_catalog_service

    return get_source_catalog_service().build_catalog(
        task_id=str(tid),
        retrieval_payload={
            "selected": selected,
            "retrieval_trace": retrieval_trace,
        },
        llm_scope=llm_scope,
    )


def render_citation_contract_prompt(
    source_catalog: dict | None,
    *,
    run_evidence_context: dict | None = None,
) -> str:
    catalog = (
        dict(source_catalog)
        if isinstance(source_catalog, dict)
        else {}
    )
    sources = [
        dict(item)
        for item in list(catalog.get("sources") or [])
        if isinstance(item, dict)
    ]
    run_records: list[dict[str, Any]] = []
    if run_evidence_context is not None:
        from ananta_contracts.recovery_run_evidence import (
            validate_recovery_tool_run_context,
        )

        validated_context = validate_recovery_tool_run_context(
            run_evidence_context,
            task_id=str(
                run_evidence_context.get("task_id") or ""
            ),
        )
        run_records = [
            dict(value)
            for value in validated_context["records"]
        ]
    if not sources and not run_records:
        return ""
    preview = []
    for item in sources[:12]:
        preview.append(
            {
                "source_id": item.get("source_id"),
                "source_type": item.get("source_type"),
                "path": item.get("path"),
                "record_id": item.get("record_id"),
                "allowed_for_llm_scope": bool(item.get("allowed_for_llm_scope", True)),
            }
        )
    run_preview = [
        {
            "source_id": item["source_id"],
            "source_type": item["source_type"],
            "record_id": item["record_id"],
            "allowed_for_llm_scope": True,
            "state": "reserved_for_this_task_execution",
        }
        for item in run_records
    ]
    return (
        "Citation Contract (grounded_answer.v1):\n"
        "- Use only provided source IDs (SRC_* or RUN_*).\n"
        "- Do not invent source IDs, paths, line ranges, or tool result IDs.\n"
        "- Every factual claim must include citation_refs.\n"
        "- Tool execution claims must cite RUN_* evidence.\n"
        "- Reserved RUN_* IDs may be cited only for the matching tool execution result.\n"
        "- Uncertain statements must be marked with confidence=unverified and empty citation_refs.\n"
        f"- source_catalog_id: {catalog.get('catalog_id')}\n"
        f"- source_catalog_hash: {catalog.get('catalog_hash')}\n"
        "Allowed sources excerpt:\n"
        + json.dumps(
            [*preview, *run_preview],
            ensure_ascii=False,
        )
    )


def extract_grounded_answer_payload(output: str | None) -> dict | None:
    raw = str(output or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if isinstance(parsed, dict) and str(parsed.get("schema") or "").strip() == "grounded_answer.v1":
        return parsed
    return None


def update_task_flow_metrics(
    *,
    tid: str,
    task: dict,
    flow_metrics: dict,
) -> None:
    current_task = get_local_task_status(tid) or dict(task or {})
    verification_status = dict(current_task.get("verification_status") or {})
    merged = dict(verification_status.get("task_flow_metrics") or {})
    merged.update({**dict(flow_metrics or {}), "updated_at": time.time()})
    verification_status["task_flow_metrics"] = merged
    update_local_task_status(
        tid,
        str(current_task.get("status") or task.get("status") or "assigned"),
        verification_status=verification_status,
    )
