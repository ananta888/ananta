"""Proposal persistence of the single-backend task propose path.

Split out of ``_task_scoped_propose_single`` (SRP): once the CLI outcome of a
propose run is final, these functions build trace/review/flow metrics and
persist a research, interactive-terminal or command proposal, append the
CLI session turn and log the terminal entries.
"""

from __future__ import annotations

from typing import Any

from flask import current_app

from agent.common.utils.structured_action_utils import extract_structured_action_fields
from agent.pipeline_trace import append_stage
from agent.runtime_policy import build_trace_record
from agent.services._task_scoped_propose_run import (
    INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
    ProposeRun,
    propose_flow_metrics,
    repair_proposal,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.native_worker_runtime_service import get_native_worker_runtime_service
from agent.services.service_registry import get_core_services
from agent.utils import _extract_reason, _log_terminal_entry


def _proposal_trace(run: ProposeRun, **extra_metadata: Any) -> dict:
    return build_trace_record(
        task_id=run.tid,
        event_type="proposal_result",
        task_kind=run.task_kind,
        backend=run.backend_used,
        requested_backend="auto",
        routing_reason=run.routing_reason,
        policy_version=run.policy_version,
        metadata={**run.worker_context_meta, "source": "task_propose", **extra_metadata},
    )


def _proposal_review(run: ProposeRun, *, command, tool_calls) -> Any:
    return run.deps.build_review_state(
        current_app.config.get("AGENT_CONFIG", {}) or {},
        run.backend_used,
        run.task_kind,
        command=command,
        tool_calls=tool_calls,
    )


def _append_session_turn(run: ProposeRun, *, trace: dict, response_payload: dict, proposal_mode: str) -> None:
    if not run.session_payload:
        return
    turn = get_cli_session_service().append_turn(
        session_id=run.session_payload["id"],
        prompt=run.prompt_for_cli,
        output=run.raw_res,
        model=run.proposal_model,
        trace_id=str(trace.get("trace_id") or ""),
        metadata={"backend_used": run.backend_used, "task_id": run.tid, "proposal_mode": proposal_mode},
    )
    if isinstance(turn, dict):
        response_payload.setdefault("routing", {})
        response_payload["routing"]["session_turn_id"] = turn.get("id")


def persist_research_proposal(run: ProposeRun, routing: dict) -> Any:
    research_res = run.deps.build_research_result(
        raw_res=run.raw_res,
        backend_used=run.backend_used,
        tid=run.tid,
        rc=run.rc,
        cli_err=run.cli_err,
        latency_ms=run.latency_ms,
        output_source=run.output_source,
        research_context=run.effective_research_context,
    )
    trace = _proposal_trace(run, artifact_kind="research_report")
    pipeline_payload = {**run.pipeline, "trace_id": trace["trace_id"]}
    response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
        tid=run.tid,
        task=run.task,
        reason=research_res.get("reason"),
        raw=run.raw_res,
        backend=run.backend_used,
        model=run.proposal_model,
        routing=routing,
        cli_result=research_res.get("cli_result"),
        worker_context=run.worker_context_meta,
        trace=trace,
        review=_proposal_review(run, command=None, tool_calls=None),
        pipeline=pipeline_payload,
        research_artifact=research_res.get("research_artifact"),
        research_context=run.effective_research_context,
        history_event={
            "event_type": "proposal_result",
            "reason": research_res.get("reason"),
            "backend": run.backend_used,
            "routing_reason": run.routing_reason,
            "latency_ms": run.latency_ms,
            "returncode": run.rc,
            "artifact_kind": "research_report",
            "source_count": len((research_res.get("research_artifact") or {}).get("sources") or []),
            "pipeline": pipeline_payload,
            "trace": trace,
            "flow_metrics": propose_flow_metrics(
                run,
                run_id=str(trace.get("trace_id") or ""),
                propose_ok=True,
                policy_classification=run.policy_classification,
            ),
        },
    )
    _append_session_turn(run, trace=trace, response_payload=response_payload, proposal_mode="research")
    return run.deps.route_response(data=response_payload)


def persist_interactive_finalize_proposal(run: ProposeRun, routing: dict) -> Any:
    reason = "Interactive OpenCode session finished; finalize workspace changes"
    append_stage(
        run.pipeline,
        name="parse",
        status="ok",
        metadata={"interactive_terminal_finalize": True, "has_command": True, "tool_call_count": 0},
    )
    trace = _proposal_trace(run, interactive_terminal=True)
    pipeline_payload = {**run.pipeline, "trace_id": trace["trace_id"]}
    retried = run.interactive_retry_meta.get("attempted")
    cli_result = {
        "returncode": run.rc,
        "latency_ms": run.latency_ms,
        "stderr_preview": (run.cli_err or "")[:240],
        "output_source": run.output_source,
        "repair_attempted": bool(retried),
        "repair_backend": run.backend_used if retried else None,
        "repair_model": run.proposal_model if retried else None,
    }
    response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
        tid=run.tid,
        task=run.task,
        reason=reason,
        raw=run.raw_res,
        backend=run.backend_used,
        model=run.proposal_model,
        routing=routing,
        cli_result=cli_result,
        worker_context=run.worker_context_meta,
        trace=trace,
        review=_proposal_review(run, command=INTERACTIVE_TERMINAL_FINALIZE_COMMAND, tool_calls=None),
        pipeline=pipeline_payload,
        command=INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
        tool_calls=None,
        history_event={
            "event_type": "proposal_result",
            "reason": reason,
            "backend": run.backend_used,
            "routing_reason": run.routing_reason,
            "latency_ms": run.latency_ms,
            "returncode": run.rc,
            "interactive_terminal": True,
            "pipeline": pipeline_payload,
            "trace": trace,
            "flow_metrics": propose_flow_metrics(
                run,
                run_id=str(trace.get("trace_id") or ""),
                propose_ok=True,
                policy_classification=run.policy_classification,
            ),
        },
    )
    _append_session_turn(run, trace=trace, response_payload=response_payload, proposal_mode="interactive_terminal")
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=run.prompt_for_cli, task_id=run.tid)
    _log_terminal_entry(
        current_app.config["AGENT_NAME"],
        0,
        "out",
        reason=reason,
        command=INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
        tool_calls=None,
        task_id=run.tid,
    )
    return run.deps.route_response(data=response_payload)


def _parse_structured_proposal(run: ProposeRun) -> tuple[Any, Any, Any]:
    """Extract reason/command/tool calls; repair once when neither command nor tool calls exist."""
    reason = _extract_reason(run.raw_res)
    command, tool_calls = extract_structured_action_fields(run.raw_res)
    if command or tool_calls:
        return reason, command, tool_calls
    repaired = repair_proposal(
        run, bad_output=run.raw_res, validation_error="missing_required_fields: command_or_tool_calls"
    )
    if not repaired:
        return reason, command, tool_calls
    run.apply_repair(repaired)
    reason = _extract_reason(run.raw_res)
    command, tool_calls = extract_structured_action_fields(run.raw_res)
    return reason, command, tool_calls


def _apply_native_worker_plan(run: ProposeRun, routing: dict, *, command, reason) -> str | None:
    policy_classification_summary = run.policy_classification
    if not (run.backend_used == "ananta-worker" and run.deps.native_worker_runtime_enabled(run.cfg)):
        return policy_classification_summary
    native_plan = get_native_worker_runtime_service().prepare_native_command_plan(
        tid=run.tid,
        task=run.task,
        command=command,
        reason=reason,
        worker_profile=run.worker_context_meta.get("worker_profile"),
        profile_source=run.worker_context_meta.get("profile_source"),
        trace_id=str((run.session_payload or {}).get("id") or ""),
        context_bundle_id=run.worker_context_meta.get("context_bundle_id"),
        agent_cfg=run.cfg,
    )
    run.worker_context_meta.update(dict(native_plan.get("worker_context_updates") or {}))
    runtime_path = str(native_plan.get("runtime_path") or "").strip().lower()
    if runtime_path:
        routing["worker_runtime_path"] = runtime_path
    policy_classification_summary = (
        str(native_plan.get("policy_classification_summary") or policy_classification_summary or "").strip().lower()
        or None
    )
    if policy_classification_summary:
        routing["policy_classification_summary"] = policy_classification_summary
    return policy_classification_summary


def persist_command_proposal(run: ProposeRun, routing: dict) -> Any:
    reason, command, tool_calls = _parse_structured_proposal(run)
    policy_classification_summary = _apply_native_worker_plan(run, routing, command=command, reason=reason)
    append_stage(
        run.pipeline,
        name="parse",
        status="ok",
        metadata={"has_command": bool(command), "tool_call_count": len(tool_calls or [])},
    )
    trace = _proposal_trace(run)
    pipeline_payload = {**run.pipeline, "trace_id": trace["trace_id"]}
    repair_meta = run.repair_meta
    response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
        tid=run.tid,
        task=run.task,
        reason=reason,
        raw=run.raw_res,
        backend=run.backend_used,
        model=run.proposal_model,
        routing=routing,
        cli_result={
            "returncode": run.rc,
            "latency_ms": run.latency_ms,
            "stderr_preview": (run.cli_err or "")[:240],
            "output_source": run.output_source,
            "repair_attempted": bool(repair_meta["attempted"]),
            "repair_backend": repair_meta["backend"],
            "repair_model": repair_meta["model"],
            "llm_call_profile": run.deps.build_llm_call_profile_entries(
                backend_used=run.backend_used,
                model=run.proposal_model,
                prompt=run.prompt_for_cli,
                raw_output=run.raw_res,
                latency_ms=run.latency_ms,
                rc=run.rc,
                repair_attempted=bool(repair_meta["attempted"]),
                repair_backend=repair_meta["backend"],
                repair_model=repair_meta["model"],
            ),
        },
        worker_context=run.worker_context_meta,
        trace=trace,
        review=_proposal_review(run, command=command, tool_calls=tool_calls),
        pipeline=pipeline_payload,
        command=command,
        tool_calls=tool_calls,
        history_event={
            "event_type": "proposal_result",
            "reason": reason,
            "backend": run.backend_used,
            "routing_reason": run.routing_reason,
            "latency_ms": run.latency_ms,
            "returncode": run.rc,
            "pipeline": pipeline_payload,
            "trace": trace,
            "flow_metrics": propose_flow_metrics(
                run,
                run_id=str(trace.get("trace_id") or ""),
                propose_ok=True,
                policy_classification=policy_classification_summary,
            ),
        },
    )
    _append_session_turn(run, trace=trace, response_payload=response_payload, proposal_mode="command")
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=run.prompt_for_cli, task_id=run.tid)
    _log_terminal_entry(
        current_app.config["AGENT_NAME"],
        0,
        "out",
        reason=reason,
        command=command,
        tool_calls=tool_calls,
        task_id=run.tid,
    )
    return run.deps.route_response(data=response_payload)


__all__ = [
    "persist_command_proposal",
    "persist_interactive_finalize_proposal",
    "persist_research_proposal",
]
