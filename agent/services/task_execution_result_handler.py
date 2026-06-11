from __future__ import annotations

import logging
import time

from flask import current_app

from agent.common.audit import log_audit
from agent.common.errors import ToolGuardrailError
from agent.metrics import RETRIES_TOTAL
from agent.pipeline_trace import append_stage
from agent.services.command_chain_parser import CommandChainParser
from agent.services.command_to_tool_mapper import CommandToToolMapper
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.segment_preflight_validator import SegmentPreflightValidator
from agent.services.task_execution_context_builder import (
    LocalExecutionResult,
    is_recoverable_missing_binary_failure,
    repair_command_transcription_noise,
    resolve_loop_trace_id,
)
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    should_retry_execution,
)
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import get_task_runtime_service
from agent.shell import get_shell
from agent.tools import registry as tool_registry


def _append_guardrail_block_history(
    tid: str,
    task: dict | None,
    command: str | None,
    tool_calls: list[dict] | None,
    decision,
    reason: str = "tool_guardrail_blocked",
    command_chain_summary: dict | None = None,
) -> None:
    history = list((task or {}).get("history") or [])
    entry: dict = {
        "event_type": "tool_guardrail_blocked",
        "reason": reason,
        "command": command[:200] if command else None,
        "tool_calls": tool_calls or [],
        "blocked_tools": decision.blocked_tools,
        "blocked_reasons": decision.reasons,
        "guardrails": decision.details,
        "timestamp": time.time(),
    }
    if command_chain_summary:
        entry["command_chain"] = command_chain_summary
    history.append(entry)
    get_task_runtime_service().update_local_task_status(
        tid,
        "blocked",
        history=history,
        status_reason_code="security_or_policy_denied",
        status_reason_details={
            "blocked_tools": list(decision.blocked_tools or []),
            "blocked_reasons": list(decision.reasons or []),
            "guardrails": dict(decision.details or {}),
        },
        last_output=f"[tool_guardrail] blocked: {', '.join(decision.reasons)}",
        last_exit_code=1,
    )
    get_execution_audit_service().emit(
        operation_type="security_policy_block",
        outcome="blocked",
        trace_id=resolve_loop_trace_id(task or {}),
        goal_id=(task or {}).get("goal_id"),
        task_id=tid,
        actor_role="hub",
        details={
            "reason": reason,
            "blocked_tools": list(decision.blocked_tools or []),
            "blocked_reasons": list(decision.reasons or []),
            **({"command_chain": dict(command_chain_summary or {})} if command_chain_summary else {}),
        },
    )


def _append_approval_block_history(
    *,
    tid: str,
    task: dict | None,
    command: str | None,
    tool_calls: list[dict] | None,
    approval_decision: dict,
    reason: str = "approval_blocked",
) -> None:
    history = list((task or {}).get("history") or [])
    history.append(
        {
            "event_type": "approval_blocked",
            "reason": reason,
            "command": command,
            "tool_calls": tool_calls or [],
            "approval_decision": dict(approval_decision or {}),
            "timestamp": time.time(),
        }
    )
    get_task_runtime_service().update_local_task_status(
        tid,
        "blocked",
        history=history,
        status_reason_code=str((approval_decision or {}).get("reason_code") or "approval_blocked"),
        last_output=f"[approval] blocked: {approval_decision.get('reason_code')}",
        last_exit_code=1,
    )
    get_execution_audit_service().emit(
        operation_type="approval_block",
        outcome="blocked",
        trace_id=resolve_loop_trace_id(task or {}),
        goal_id=(task or {}).get("goal_id"),
        task_id=tid,
        actor_role="hub",
        details={
            "reason": reason,
            "approval_decision": dict(approval_decision or {}),
        },
    )
    reason_code = str((approval_decision or {}).get("reason_code") or "").strip().lower()
    approval_action = "expired" if "expired" in reason_code else "reject"
    get_execution_audit_service().emit_approval_event(
        trace_id=resolve_loop_trace_id(task or {}),
        task_id=tid,
        goal_id=(task or {}).get("goal_id"),
        action=approval_action,
        approver_identity=str((approval_decision or {}).get("reviewed_by") or "policy_engine"),
        approval_scope=str((approval_decision or {}).get("scope") or "task_execution"),
        approval_source="approval_policy_service",
        write_allowed=False,
        actor_role="hub",
        details={"reason_code": reason_code or None},
    )


def _evaluate_doom_loop(
    *,
    tid: str | None,
    task: dict,
    guard_cfg: dict,
    loop_signals: list[dict],
) -> dict | None:
    if not loop_signals:
        return None
    from agent.services.doom_loop_service import get_doom_loop_service

    loop_service = get_doom_loop_service()
    history_signals = loop_service.collect_signals_from_history(list(task.get("history") or []))
    decision = loop_service.detect(
        signals=[*history_signals, *list(loop_signals)],
        policy=(guard_cfg or {}).get("doom_loop_policy"),
    )
    if not decision.detected:
        return None
    payload = decision.as_dict()
    payload["enforced"] = bool(payload.get("action") in {"pause", "abort"} and decision.policy.get("enforce_pause_abort"))
    if tid:
        current_app.logger.warning(
            "Task %s doom-loop detected: class=%s severity=%s action=%s",
            tid,
            payload.get("classification"),
            payload.get("severity"),
            payload.get("action"),
        )
        try:
            log_audit(
                "doom_loop_detected",
                {
                    "task_id": tid,
                    "trace_id": resolve_loop_trace_id(task),
                    "classification": payload.get("classification"),
                    "severity": payload.get("severity"),
                    "action": payload.get("action"),
                    "reasons": payload.get("reasons"),
                    "metrics": payload.get("metrics"),
                },
            )
        except Exception:
            current_app.logger.debug("doom-loop audit log failed for task %s", tid)
    return payload


def _enforce_worker_execution_contract_tool_classes(
    *,
    normalized_tool_calls: list[dict],
    allowed_tool_classes: set[str],
) -> None:
    if not allowed_tool_classes:
        return
    from agent.services.tool_intent_taxonomy_service import get_tool_intent_taxonomy_service

    blocked_tools: list[str] = []
    blocked_reasons: dict[str, str] = {}
    taxonomy = get_tool_intent_taxonomy_service()
    for tc in normalized_tool_calls:
        tool_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
        tool_class = str(taxonomy.classify_tool(tool_name).get("tool_class") or "unknown").strip().lower()
        if tool_class not in allowed_tool_classes:
            blocked_tools.append(tool_name or "<missing>")
            blocked_reasons[tool_name or "<missing>"] = "tool_class_not_allowed_for_worker_execution_contract"
    if blocked_tools:
        raise ToolGuardrailError(
            details={
                "blocked_tools": blocked_tools,
                "blocked_reasons": sorted(set(blocked_reasons.values())),
                "blocked_reasons_by_tool": blocked_reasons,
                "allowed_tool_classes": sorted(allowed_tool_classes),
            }
        )


def _execute_single_shell_command_with_policy(
    *,
    tid: str | None,
    command: str,
    execution_policy,
    working_directory: str | None = None,
) -> tuple[str, int | None, int, str, list[dict]]:
    shell = get_shell()
    retries_used = 0
    attempt = 0
    latest_output = ""
    latest_exit_code: int | None = -1
    failure_type = "success"
    retry_history: list[dict] = []
    effective_command = command
    if working_directory:
        import shlex

        quoted = shlex.quote(str(working_directory))
        effective_command = f"cd {quoted}\n{command}"

    while True:
        attempt += 1
        latest_output, latest_exit_code = shell.execute(effective_command, timeout=execution_policy.timeout_seconds)
        failure_type = classify_execution_failure(latest_exit_code, latest_output)
        if latest_exit_code == 0:
            return latest_output, latest_exit_code, retries_used, failure_type, retry_history

        should_retry = retries_used < execution_policy.retries and should_retry_execution(
            exit_code=latest_exit_code,
            output=latest_output,
            policy=execution_policy,
        )
        delay = compute_execution_retry_delay(policy=execution_policy, attempt=retries_used + 1) if should_retry else 0.0
        retry_history.append(
            {
                "attempt": attempt,
                "exit_code": latest_exit_code,
                "failure_type": failure_type,
                "retry_scheduled": should_retry,
                "delay_seconds": round(delay, 3),
            }
        )
        if not should_retry:
            return latest_output, latest_exit_code, retries_used, failure_type, retry_history

        retries_used += 1
        RETRIES_TOTAL.inc()
        current_app.logger.info(
            "Task %s Shell-Fehler (%s, exit_code %s). Retry in %.2fs... (%s/%s)",
            tid or "<direct>",
            failure_type,
            latest_exit_code,
            delay,
            retries_used,
            execution_policy.retries,
        )
        if delay > 0:
            time.sleep(delay)


def _execute_shell_command_with_policy(
    *,
    tid: str | None,
    command: str,
    execution_policy,
    working_directory: str | None = None,
    allow_complex_shell: bool = False,
    task: dict | None = None,
    agent_cfg: dict | None = None,
) -> tuple[str, int | None, int, str, list[dict]]:
    repaired_command = repair_command_transcription_noise(command)
    plan = CommandChainParser().parse(repaired_command)
    if not plan.allowed and not allow_complex_shell:
        message = (
            "Error: Unsupported shell operators in command: "
            + ", ".join(plan.unsupported_operators or [str(plan.denied_reason or "invalid_command")])
            + ". Request a single command without chaining/redirection."
        )
        return message, -1, 0, "command_runtime_error", []
    if not plan.allowed and allow_complex_shell:
        command_segments = [repaired_command]
        chain_segments = []
    else:
        command_segments = [seg.raw for seg in plan.segments]
        chain_segments = list(plan.segments)
    if not command_segments:
        return "", 0, 0, "success", []

    effective_task = task or {}
    cfg = dict(agent_cfg or {})
    known_tools = [definition.get("name") for definition in tool_registry.get_tool_definitions()]
    known_tool_set = set(known_tools)
    preflight = SegmentPreflightValidator().validate_segments(
        segments=chain_segments,
        task=effective_task,
        agent_cfg=cfg,
        known_tools=known_tools,
    )
    validation_meta = preflight.as_validation_meta()
    get_execution_audit_service().emit(
        operation_type="mutation_gate_decision",
        outcome="allow" if preflight.allowed else "blocked",
        trace_id=resolve_loop_trace_id(effective_task),
        goal_id=effective_task.get("goal_id"),
        task_id=tid,
        actor_role="hub",
        details={
            "reason_code": "mutation_gate_segment_preflight_ok" if preflight.allowed else "mutation_gate_segment_preflight_blocked",
            "source": "task_execution_service.segment_preflight",
            "segment_count": len(chain_segments),
            "denied_segment_index": preflight.denied_segment_index,
            "reason_codes": list(preflight.reason_codes or []),
            "validations": validation_meta,
        },
    )
    if not preflight.allowed:
        denied_idx = preflight.denied_segment_index or 0
        return (
            f"Error: command chain segment denied at index {denied_idx}: {', '.join(preflight.reason_codes)}",
            -1,
            0,
            "command_runtime_error",
            [{"attempt": 0, "command_chain": {"segment_count": len(command_segments), "validations": validation_meta}}],
        )

    aggregate_outputs: list[str] = []
    aggregate_retries = 0
    aggregate_history: list[dict] = []
    if repaired_command != str(command or "").strip():
        aggregate_history.append(
            {
                "attempt": 0,
                "normalization": "transcription_noise_repaired",
            }
        )
    if len(command_segments) > 1:
        aggregate_history.append(
            {
                "attempt": 0,
                "normalization": "command_chain_parsed",
                "segment_count": len(command_segments),
                "validations": validation_meta,
            }
        )

    last_exit_code = 0
    last_failure_type = "success"
    any_executed = False
    for index, segment in enumerate(command_segments):
        operator_before = None
        if index < len(chain_segments):
            operator_before = chain_segments[index].operator_before
        if operator_before == "&&" and last_exit_code != 0:
            aggregate_history.append({"attempt": 0, "segment_index": index + 1, "segment_command": segment, "skipped_by": "&&"})
            continue
        if operator_before == "||" and last_exit_code == 0:
            aggregate_history.append({"attempt": 0, "segment_index": index + 1, "segment_command": segment, "skipped_by": "||"})
            continue
        from agent.services.command_to_tool_mapper import CommandToToolMapper

        mapped = CommandToToolMapper().map(segment)
        if mapped.mapped_tool and mapped.mapped_tool in known_tool_set:
            tool_result = tool_registry.execute(mapped.mapped_tool, mapped.args)
            output = str(tool_result.output or "")
            exit_code = 0 if tool_result.success else 1
            retries_used = 0
            failure_type = "success" if tool_result.success else "tool_failure"
            retry_history = [{"attempt": 1, "mapped_tool": mapped.mapped_tool, "mapping_reason": mapped.reason}]
        else:
            output, exit_code, retries_used, failure_type, retry_history = _execute_single_shell_command_with_policy(
                tid=tid,
                command=segment,
                execution_policy=execution_policy,
                working_directory=working_directory,
            )
        aggregate_retries += retries_used
        any_executed = True
        if output:
            aggregate_outputs.append(output)
        for entry in list(retry_history or []):
            aggregate_history.append(
                {
                    **entry,
                    "segment_index": index + 1,
                    "segment_command": segment,
                    "operator_before": operator_before,
                }
            )
        last_exit_code = int(exit_code or 0)
        last_failure_type = failure_type
        if exit_code != 0:
            next_operator = chain_segments[index].operator_after if index < len(chain_segments) else None
            if next_operator == "||":
                continue
            if next_operator == "&&":
                continue
            return "\n---\n".join(aggregate_outputs), exit_code, aggregate_retries, failure_type, aggregate_history

    if not any_executed:
        return "\n---\n".join(aggregate_outputs), 0, aggregate_retries, "success", aggregate_history
    if last_exit_code == 0:
        return "\n---\n".join(aggregate_outputs), 0, aggregate_retries, "success", aggregate_history
    return "\n---\n".join(aggregate_outputs), last_exit_code, aggregate_retries, last_failure_type, aggregate_history


def _enter_pending_approval(
    *,
    tid: str | None,
    task: dict,
    guard_cfg: dict,
    tool_name: str,
    arguments: dict,
    approval_payload: dict,
    pipeline: dict | None = None,
    target_fingerprint: str | None = None,
) -> tuple[str, LocalExecutionResult | None]:
    try:
        from agent.services.approval_request_service import get_approval_request_service

        svc = get_approval_request_service()
        if not svc.get_lifecycle_config(guard_cfg).get("enabled"):
            return "disabled", None
        request = svc.create_pending_request(
            task_id=tid,
            goal_id=str((task or {}).get("goal_id") or "").strip() or None,
            trace_id=resolve_loop_trace_id(task or {}),
            tool_name=tool_name,
            arguments=arguments,
            target_fingerprint=target_fingerprint,
            risk_class=str((approval_payload or {}).get("operation_class") or "unknown"),
            governance_mode=str((approval_payload or {}).get("governance_mode") or "balanced"),
            scope={"source": "task_execution_service", "reason_code": (approval_payload or {}).get("reason_code")},
            agent_cfg=guard_cfg,
        )
    except Exception:
        logging.getLogger(__name__).warning("pending approval registration failed", exc_info=True)
        return "disabled", None
    if request.status == "granted":
        return "auto_granted", None
    if tid:
        try:
            from agent.services.repository_registry import get_repository_registry

            task_repo = get_repository_registry().task_repo
            task_row = task_repo.get_by_id(tid)
            if task_row is not None:
                task_row.status = "pending_approval"
                task_row.status_reason_code = "approval_request_pending"
                task_repo.save(task_row)
        except Exception:
            logging.getLogger(__name__).warning("task pending_approval status update failed", exc_info=True)
    if pipeline is not None:
        append_stage(
            pipeline,
            name="pending_approval",
            status="blocked",
            metadata={"approval_request_id": request.id, "tool_name": tool_name},
        )
    return "pending", LocalExecutionResult(
        output=f"Pending approval: {tool_name} requires hub approval (request {request.id}).",
        exit_code=None,
        retries_used=0,
        failure_type="pending_approval",
        retry_history=[{"attempt": 0, "approval_request_id": request.id}],
        status="pending_approval",
        loop_signals=[],
        loop_detection=None,
        approval_decision={
            **dict(approval_payload or {}),
            "approval_request_id": request.id,
            "status": "pending_approval",
        },
    )


def _persist_task_proposal_result(
    *,
    tid: str,
    task: dict | None,
    reason: str,
    raw: str | None,
    backend: str | None,
    model: str | None,
    routing: dict | None,
    cli_result: dict | None,
    worker_context: dict | None,
    trace: dict | None,
    review: dict | None,
    pipeline: dict | None = None,
    command: str | None = None,
    tool_calls: list[dict] | None = None,
    comparisons: dict | None = None,
    research_artifact: dict | None = None,
    research_context: dict | None = None,
    forwarded_request: dict | None = None,
    history_event: dict | None = None,
) -> dict:
    from agent.models import TaskScopedStepProposeResponse
    from agent.services.task_execution_context_builder import (
        build_cli_result_contract,
        build_research_artifact_contract,
        build_research_context_contract,
        build_review_contract,
        build_routing_contract,
        build_worker_context_contract,
    )

    proposal = {
        "reason": reason,
        "backend": backend,
        "model": model,
        "routing": routing,
        "cli_result": cli_result,
        "trace": trace,
        "worker_context": worker_context,
        "review": review,
    }
    if pipeline:
        proposal["pipeline"] = pipeline
    if comparisons:
        proposal["comparisons"] = comparisons
    if research_artifact:
        proposal["research_artifact"] = research_artifact
    if research_context:
        proposal["research_context"] = research_context
    if forwarded_request:
        proposal["forwarded_request"] = dict(forwarded_request)
    if command and command != str(raw or "").strip():
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls

    get_task_execution_tracking_service().persist_proposal_result(
        tid=tid,
        task=task,
        proposal=proposal,
        history_event=history_event,
    )

    return TaskScopedStepProposeResponse(
        status="proposing",
        reason=reason or "",
        command=proposal.get("command"),
        tool_calls=proposal.get("tool_calls"),
        raw=raw,
        backend=backend,
        model=model,
        routing=build_routing_contract(routing),
        cli_result=build_cli_result_contract(cli_result),
        comparisons=comparisons,
        research_artifact=build_research_artifact_contract(research_artifact),
        research_context=build_research_context_contract(research_context),
        worker_context=build_worker_context_contract(worker_context),
        trace=trace,
        pipeline=pipeline,
        review=build_review_contract(review),
    ).model_dump(exclude_none=True)


def _finalize_task_execution_response(
    *,
    tid: str,
    task: dict | None,
    status: str,
    reason: str,
    command: str | None,
    tool_calls: list[dict] | None,
    output: str,
    exit_code: int | None,
    retries_used: int,
    retry_history: list[dict] | None,
    failure_type: str,
    execution_duration_ms: int,
    trace: dict,
    pipeline: dict,
    execution_policy,
    review: dict | None = None,
    artifact_refs: list[dict] | None = None,
    extra_history: dict | None = None,
) -> dict:
    from agent.models import CostSummaryContract, TaskArtifactReferenceContract, TaskScopedStepExecuteResponse
    from agent.services.task_execution_context_builder import build_review_contract

    tracking = get_task_execution_tracking_service().finalize_execution_result(
        tid=tid,
        task=task,
        status=status,
        reason=reason,
        command=command,
        tool_calls=tool_calls,
        output=output,
        exit_code=exit_code,
        retries_used=retries_used,
        retry_history=retry_history,
        failure_type=failure_type,
        execution_duration_ms=execution_duration_ms,
        trace=trace,
        pipeline=pipeline,
        artifact_refs=artifact_refs,
        extra_history=extra_history,
    )
    response = TaskScopedStepExecuteResponse(
        output=output,
        exit_code=exit_code,
        task_id=tid,
        status=status,
        retry_history=list(retry_history or []),
        cost_summary=CostSummaryContract.model_validate(tracking["cost_summary"]),
        trace=trace,
        pipeline=pipeline,
        memory_entry_id=tracking["memory_entry"].id if tracking.get("memory_entry") else None,
        retries_used=retries_used,
        failure_type=failure_type,
        execution_policy=execution_policy,
        review=build_review_contract(review),
        artifacts=[TaskArtifactReferenceContract.model_validate(ref) for ref in list(artifact_refs or [])] or None,
        execution_scope=tracking.get("execution_scope") or None,
        execution_provenance=tracking.get("execution_provenance") or None,
    )
    return response.model_dump(exclude_none=True)
