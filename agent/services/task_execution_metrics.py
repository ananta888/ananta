from __future__ import annotations

import time

from flask import current_app

from agent.llm_benchmarks import estimate_cost_units
from agent.llm_benchmarks import record_benchmark_sample as persist_benchmark_sample
from agent.llm_benchmarks import resolve_benchmark_identity as shared_resolve_benchmark_identity
from agent.runtime_policy import normalize_task_kind
from agent.services.repository_registry import get_repository_registry
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens


def build_execution_cost_summary(
    *,
    task: dict | None,
    proposal_meta: dict | None,
    output: str,
    tool_calls: list[dict] | None,
    execution_duration_ms: int,
) -> dict:
    routing = (proposal_meta or {}).get("routing") or {}
    inference_provider = str(routing.get("inference_provider") or "").strip() or None
    inference_model = str(routing.get("inference_model") or "").strip() or None
    execution_backend = str(routing.get("execution_backend") or (proposal_meta or {}).get("backend") or "").strip() or "shell"
    bench_provider, bench_model = shared_resolve_benchmark_identity(
        proposal_meta,
        current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    bench_task_kind = normalize_task_kind(
        ((proposal_meta or {}).get("routing") or {}).get("task_kind"),
        (task or {}).get("description") or "",
    )
    estimated_tokens = estimate_text_tokens((task or {}).get("description") or "") + estimate_text_tokens(output or "")
    if tool_calls:
        estimated_tokens += estimate_tool_calls_tokens(tool_calls)
    cost_units, pricing_source = estimate_cost_units(
        current_app.config.get("AGENT_CONFIG", {}) or {},
        bench_provider,
        bench_model,
        estimated_tokens,
    )
    return {
        "provider": bench_provider,
        "model": bench_model,
        "inference_provider": inference_provider or bench_provider,
        "inference_model": inference_model or bench_model,
        "execution_backend": execution_backend,
        "task_kind": bench_task_kind,
        "tokens_total": estimated_tokens,
        "cost_units": cost_units,
        "latency_ms": int(execution_duration_ms or 0),
        "pricing_source": pricing_source,
    }


def record_benchmark_sample(
    *,
    cost_summary: dict,
    success: bool,
    quality_gate_passed: bool,
    proposal_meta: dict | None = None,
) -> None:
    selection_meta = (proposal_meta or {}).get("model_selection") if isinstance((proposal_meta or {}).get("model_selection"), dict) else {}
    context_tags = {
        "role_name": str(selection_meta.get("role_name") or "").strip(),
        "template_name": str(selection_meta.get("template_name") or "").strip(),
        "selection_source": str(selection_meta.get("source") or "").strip(),
        "selected_temperature": str(selection_meta.get("selected_temperature") or "").strip(),
        "task_kind": str(cost_summary.get("task_kind") or "").strip().lower(),
    }
    persist_benchmark_sample(
        data_dir=current_app.config.get("DATA_DIR") or "data",
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider=str(cost_summary.get("provider") or ""),
        model=str(cost_summary.get("model") or ""),
        task_kind=str(cost_summary.get("task_kind") or ""),
        success=success,
        quality_gate_passed=quality_gate_passed,
        latency_ms=int(cost_summary.get("latency_ms") or 0),
        tokens_total=int(cost_summary.get("tokens_total") or 0),
        cost_units=float(cost_summary.get("cost_units") or 0.0),
        context_tags=context_tags,
    )


def build_control_layer_observability_snapshot(*, max_tasks: int = 80) -> dict:
    repos = get_repository_registry()
    tasks = [task.model_dump() for task in repos.task_repo.get_all()]
    recent_tasks = sorted(
        tasks,
        key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0),
        reverse=True,
    )[: max(1, int(max_tasks))]

    loop_counts = {"none": 0, "warning": 0, "detected": 0, "stopped": 0}
    approval_counts: dict[str, int] = {}
    routing_backend_counts: dict[str, int] = {}
    routing_reason_counts: dict[str, int] = {}
    context_counts = {
        "with_bundle": 0,
        "without_bundle": 0,
        "with_compaction": 0,
        "near_budget_limit": 0,
    }
    compaction_reason_counts: dict[str, int] = {}
    loop_action_counts: dict[str, int] = {}

    def _inc(counter: dict[str, int], key: str) -> None:
        normalized = str(key or "").strip() or "unknown"
        counter[normalized] = int(counter.get(normalized) or 0) + 1

    def _resolve_loop_state(task_payload: dict) -> str:
        history = list(task_payload.get("history") or [])
        detected = False
        warning = False
        stopped = False
        for entry in history:
            if not isinstance(entry, dict):
                continue
            loop_detection = dict(entry.get("loop_detection") or {})
            if bool(loop_detection.get("detected")):
                detected = True
                action = str(loop_detection.get("action") or "").strip().lower()
                if action in {"stop", "stopped", "blocked"}:
                    stopped = True
                if action:
                    _inc(loop_action_counts, action)
            classification = str(loop_detection.get("classification") or entry.get("classification") or "").strip().lower()
            if classification in {"warning", "near_loop", "risk"}:
                warning = True
        if stopped:
            return "stopped"
        if detected:
            return "detected"
        if warning:
            return "warning"
        return "none"

    for task in recent_tasks:
        proposal = dict(task.get("last_proposal") or {})
        routing = dict(proposal.get("routing") or {})
        backend = str(routing.get("effective_backend") or proposal.get("backend") or "unknown").strip() or "unknown"
        routing_reason = str(routing.get("reason") or "unknown").strip() or "unknown"
        _inc(routing_backend_counts, backend)
        _inc(routing_reason_counts, routing_reason)

        approval_status = (
            str(task.get("approval_status") or "").strip().lower()
            or str((proposal.get("review") or {}).get("status") or "").strip().lower()
            or "not_required"
        )
        _inc(approval_counts, approval_status)

        loop_state = _resolve_loop_state(task)
        loop_counts[loop_state] = int(loop_counts.get(loop_state) or 0) + 1

        bundle_id = str(task.get("context_bundle_id") or "").strip()
        if not bundle_id:
            context_counts["without_bundle"] += 1
            continue
        bundle = repos.context_bundle_repo.get_by_id(bundle_id)
        if bundle is None:
            context_counts["without_bundle"] += 1
            continue
        context_counts["with_bundle"] += 1
        metadata = dict(bundle.bundle_metadata or {})
        budget = dict(metadata.get("budget") or {})
        compaction = dict(metadata.get("compaction") or {})
        if float(budget.get("retrieval_utilization") or 0.0) >= 0.95:
            context_counts["near_budget_limit"] += 1
        dropped = int(compaction.get("dropped_chunk_count") or 0)
        if dropped > 0:
            context_counts["with_compaction"] += 1
        for reason, count in dict(compaction.get("dropped_reasons") or {}).items():
            try:
                increment = int(count)
            except (TypeError, ValueError):
                increment = 0
            if increment <= 0:
                continue
            compaction_reason_counts[str(reason)] = int(compaction_reason_counts.get(str(reason)) or 0) + increment

    return {
        "version": "control-layer-observability-v1",
        "tasks_scanned": len(recent_tasks),
        "loop": {
            "counts": loop_counts,
            "actions": dict(sorted(loop_action_counts.items(), key=lambda item: item[0])),
        },
        "routing": {
            "backend_counts": dict(sorted(routing_backend_counts.items(), key=lambda item: (-item[1], item[0]))),
            "reason_counts": dict(sorted(routing_reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        },
        "approval": {
            "status_counts": dict(sorted(approval_counts.items(), key=lambda item: (-item[1], item[0]))),
        },
        "context": {
            **context_counts,
            "compaction_reason_counts": dict(sorted(compaction_reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        },
        "updated_at": time.time(),
    }
