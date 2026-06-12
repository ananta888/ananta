from __future__ import annotations

import threading
import time

from flask import current_app

from agent.llm_benchmarks import estimate_cost_units
from agent.llm_benchmarks import record_benchmark_sample as persist_benchmark_sample
from agent.llm_benchmarks import resolve_benchmark_identity as shared_resolve_benchmark_identity
from agent.runtime_policy import normalize_task_kind
from agent.services.repository_registry import get_repository_registry
from agent.tool_guardrails import estimate_text_tokens, estimate_tool_calls_tokens


# HDE-021: in-process counters for hub-direct execution and tool reuse.
# Only reason codes and tool names are recorded — never raw prompts or
# tool outputs.
_HUB_DIRECT_METRIC_NAMES = (
    "direct_execution_count",
    "direct_execution_success_count",
    "direct_execution_blocked_count",
    "fallback_to_worker_count",
    "avoided_llm_call_count",
    "custom_tool_reuse_count",
)
_hub_direct_lock = threading.Lock()
_hub_direct_counters: dict[str, int] = {name: 0 for name in _HUB_DIRECT_METRIC_NAMES}
_hub_direct_by_tool: dict[str, int] = {}
_hub_direct_by_reason: dict[str, int] = {}
_HUB_DIRECT_BREAKDOWN_LIMIT = 200

_SOURCE_LINE_POLICY_METRIC_NAMES = (
    "source_line_policy_evaluations_total",
    "source_line_policy_blocked_total",
    "source_line_policy_followup_required_total",
    "source_line_policy_warning_total",
)
_source_line_policy_lock = threading.Lock()
_source_line_policy_counters: dict[str, int] = {name: 0 for name in _SOURCE_LINE_POLICY_METRIC_NAMES}
_source_line_policy_by_category: dict[str, int] = {}
_source_line_policy_by_reason: dict[str, int] = {}


def record_hub_direct_metric(
    metric: str,
    *,
    tool_name: str | None = None,
    reason_code: str | None = None,
) -> None:
    if metric not in _HUB_DIRECT_METRIC_NAMES:
        return
    with _hub_direct_lock:
        _hub_direct_counters[metric] += 1
        tool = str(tool_name or "").strip()
        if tool and len(_hub_direct_by_tool) < _HUB_DIRECT_BREAKDOWN_LIMIT:
            _hub_direct_by_tool[tool] = int(_hub_direct_by_tool.get(tool) or 0) + 1
        reason = str(reason_code or "").strip()
        if reason and len(_hub_direct_by_reason) < _HUB_DIRECT_BREAKDOWN_LIMIT:
            _hub_direct_by_reason[reason] = int(_hub_direct_by_reason.get(reason) or 0) + 1


def hub_direct_metrics_snapshot() -> dict:
    with _hub_direct_lock:
        return {
            "version": "hub-direct-metrics-v1",
            **dict(_hub_direct_counters),
            "by_tool": dict(sorted(_hub_direct_by_tool.items(), key=lambda item: (-item[1], item[0]))),
            "by_reason": dict(sorted(_hub_direct_by_reason.items(), key=lambda item: (-item[1], item[0]))),
            "updated_at": time.time(),
        }


_hub_direct_recent_decisions: list[dict] = []
_HUB_DIRECT_RECENT_LIMIT = 50


def record_hub_direct_decision(entry: dict) -> None:
    """Keep the last direct-execution decisions for diagnostics (HDE-022).

    Entries carry tool name, reason code, kind and IDs — no prompts and
    no tool outputs.
    """
    slim = {
        "tool_name": entry.get("tool_name"),
        "reason_code": entry.get("reason_code"),
        "kind": entry.get("kind"),
        "task_id": entry.get("task_id"),
        "status": entry.get("status"),
        "source": entry.get("source"),
        "at": time.time(),
    }
    with _hub_direct_lock:
        _hub_direct_recent_decisions.append(slim)
        del _hub_direct_recent_decisions[:-_HUB_DIRECT_RECENT_LIMIT]


def last_hub_direct_decisions() -> list[dict]:
    with _hub_direct_lock:
        return list(reversed(_hub_direct_recent_decisions))


def reset_hub_direct_metrics() -> None:
    with _hub_direct_lock:
        for name in _HUB_DIRECT_METRIC_NAMES:
            _hub_direct_counters[name] = 0
        _hub_direct_by_tool.clear()
        _hub_direct_by_reason.clear()
        _hub_direct_recent_decisions.clear()


def record_source_line_policy_metric(
    decision: str,
    *,
    category: str | None = None,
    reason_code: str | None = None,
) -> None:
    normalized = str(decision or "").strip().lower()
    with _source_line_policy_lock:
        _source_line_policy_counters["source_line_policy_evaluations_total"] += 1
        if normalized == "blocked":
            _source_line_policy_counters["source_line_policy_blocked_total"] += 1
        elif normalized == "followup_required":
            _source_line_policy_counters["source_line_policy_followup_required_total"] += 1
        elif normalized == "warning":
            _source_line_policy_counters["source_line_policy_warning_total"] += 1
        cat = str(category or "").strip()
        if cat and len(_source_line_policy_by_category) < _HUB_DIRECT_BREAKDOWN_LIMIT:
            _source_line_policy_by_category[cat] = int(_source_line_policy_by_category.get(cat) or 0) + 1
        reason = str(reason_code or "").strip()
        if reason and len(_source_line_policy_by_reason) < _HUB_DIRECT_BREAKDOWN_LIMIT:
            _source_line_policy_by_reason[reason] = int(_source_line_policy_by_reason.get(reason) or 0) + 1


def source_line_policy_metrics_snapshot() -> dict:
    with _source_line_policy_lock:
        return {
            "version": "source-line-policy-metrics-v1",
            **dict(_source_line_policy_counters),
            "by_category": dict(sorted(_source_line_policy_by_category.items(), key=lambda item: (-item[1], item[0]))),
            "by_reason": dict(sorted(_source_line_policy_by_reason.items(), key=lambda item: (-item[1], item[0]))),
            "updated_at": time.time(),
        }


def reset_source_line_policy_metrics() -> None:
    with _source_line_policy_lock:
        for name in _SOURCE_LINE_POLICY_METRIC_NAMES:
            _source_line_policy_counters[name] = 0
        _source_line_policy_by_category.clear()
        _source_line_policy_by_reason.clear()


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
