from __future__ import annotations

import json
import logging
from typing import Any

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.common.errors import ToolGuardrailError
from agent.pipeline_trace import append_stage
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy
from agent.services.task_execution_context_builder import (
    LocalExecutionResult,
    approval_call_identity,
    is_non_fatal_tool_error,
    is_recoverable_missing_binary_failure,
    loop_signature,
    normalize_runtime_tool_calls,
    resolve_loop_trace_id,
)
from agent.services.task_execution_result_handler import (
    _append_approval_block_history,
    _append_guardrail_block_history,
    _enforce_worker_execution_contract_tool_classes,
    _enter_pending_approval,
    _execute_shell_command_with_policy,
)
from agent.services.task_execution_policy_service import (
    resolve_task_scope_allowed_tools,
    validate_task_scoped_tool_calls,
)
from agent.services.tool_intent_resolver import ToolIntentResolver
from agent.tool_guardrails import ToolGuardrailDecision, estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry


def run_preflight_guards(
    *,
    tid: str | None,
    command: str | None,
    tool_calls: list[dict] | None,
    effective_task: dict,
    guard_cfg: dict,
    pipeline: dict | None,
    _command_analysis: Any,
    _chain_preflight_deferred: bool,
) -> dict | None:
    _call_tool_name, _call_arguments = approval_call_identity(command=command, tool_calls=tool_calls)
    approval_decision = get_approval_policy_service().evaluate(
        command=command,
        tool_calls=tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
        command_analysis=_command_analysis,
        approval_context={
            "tool_name": _call_tool_name,
            "arguments": _call_arguments,
            "task_id": tid,
            "goal_id": effective_task.get("goal_id"),
        },
    )
    approval_payload = approval_decision.as_dict()
    if pipeline is not None:
        append_stage(
            pipeline,
            name="approval_check",
            status="blocked" if approval_payload.get("classification") == "blocked" else "ok",
            metadata={
                "classification": approval_payload.get("classification"),
                "reason_code": approval_payload.get("reason_code"),
                "required_confirmation_level": approval_payload.get("required_confirmation_level"),
            },
        )
    if approval_payload.get("classification") == "blocked" and approval_payload.get("enforced") and not _chain_preflight_deferred:
        if tid:
            _append_approval_block_history(
                tid=tid,
                task=effective_task,
                command=command,
                tool_calls=tool_calls,
                approval_decision=approval_payload,
            )
        raise ToolGuardrailError(
            details={
                "blocked_tools": [str((item or {}).get("name") or (item or {}).get("tool_name") or "").strip() for item in list(tool_calls or []) if isinstance(item, dict)],
                "blocked_reasons": [approval_payload.get("reason_code")],
                "approval": approval_payload,
            }
        )
    if (
        approval_payload.get("classification") == "confirm_required"
        and approval_payload.get("enforced")
        and not bool((effective_task or {}).get("approval_confirmed"))
        and not _chain_preflight_deferred
    ):
        if tid:
            _append_approval_block_history(
                tid=tid,
                task=effective_task,
                command=command,
                tool_calls=tool_calls,
                approval_decision=approval_payload,
                reason="approval_confirmation_required",
            )
        pending_state, pending_result = _enter_pending_approval(
            tid=tid,
            task=effective_task,
            guard_cfg=guard_cfg,
            tool_name=_call_tool_name,
            arguments=_call_arguments,
            approval_payload=approval_payload,
            pipeline=pipeline,
        )
        if pending_state == "pending":
            return pending_result
        if pending_state != "auto_granted":
            raise ToolGuardrailError(
                details={
                    "blocked_tools": [str((item or {}).get("name") or (item or {}).get("tool_name") or "").strip() for item in list(tool_calls or []) if isinstance(item, dict)],
                    "blocked_reasons": [approval_payload.get("reason_code")],
                    "approval": approval_payload,
                }
            )

    risk_decision = evaluate_execution_risk(
        command=command,
        tool_calls=tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
        command_analysis=_command_analysis,
    )
    if pipeline is not None:
        append_stage(
            pipeline,
            name="risk_policy",
            status="ok" if risk_decision.allowed else "blocked",
            metadata={
                "risk_level": risk_decision.risk_level,
                "review_required": risk_decision.review_required,
                "reasons": risk_decision.reasons,
            },
        )
    if not risk_decision.allowed and not _chain_preflight_deferred:
        if tid:
            _append_guardrail_block_history(
                tid,
                effective_task,
                command,
                tool_calls,
                risk_decision,
                reason="execution_risk_policy_blocked",
            )
        raise ToolGuardrailError(
            details={
                "blocked_tools": risk_decision.blocked_tools,
                "blocked_reasons": risk_decision.reasons,
                "guardrails": risk_decision.details,
                "risk_level": risk_decision.risk_level,
            }
        )

    loop_trace_id = resolve_loop_trace_id(effective_task)
    mutation_decision = get_mutation_gate_service().evaluate(
        command=command,
        tool_calls=tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
        approval_decision=approval_payload,
        risk_decision=risk_decision,
        trace_id=loop_trace_id,
        actor="system",
    )
    mutation_payload = mutation_decision.as_dict()
    get_execution_audit_service().emit(
        operation_type="mutation_gate_decision",
        outcome=str(mutation_payload.get("classification") or "unknown"),
        trace_id=loop_trace_id,
        goal_id=effective_task.get("goal_id"),
        task_id=tid,
        actor_role="hub",
        details={
            "reason_code": mutation_payload.get("reason_code"),
            "mutation_class": mutation_payload.get("mutation_class"),
            "normalized_target": mutation_payload.get("normalized_target"),
            "approval_scope": mutation_payload.get("approval_scope"),
            "source": "task_execution_service.preflight",
        },
    )
    if pipeline is not None:
        append_stage(
            pipeline,
            name="mutation_gate",
            status="blocked" if mutation_payload.get("classification") in {"blocked", "confirm_required"} else "ok",
            metadata={
                "classification": mutation_payload.get("classification"),
                "reason_code": mutation_payload.get("reason_code"),
                "mutation_class": mutation_payload.get("mutation_class"),
                "target": mutation_payload.get("normalized_target"),
            },
        )
    if mutation_payload.get("classification") in {"blocked", "confirm_required"} and not _chain_preflight_deferred:
        if tid:
            _append_approval_block_history(
                tid=tid,
                task=effective_task,
                command=command,
                tool_calls=tool_calls,
                approval_decision={
                    "reason_code": mutation_payload.get("reason_code"),
                    "scope": "mutation_gate",
                    "reviewed_by": "mutation_gate_service",
                    "decision": mutation_payload,
                },
                reason=str(mutation_payload.get("reason_code") or "mutation_gate_blocked"),
            )
        if mutation_payload.get("classification") == "confirm_required":
            pending_state, pending_result = _enter_pending_approval(
                tid=tid,
                task=effective_task,
                guard_cfg=guard_cfg,
                tool_name="mutation_gate",
                arguments={"command": str(command or ""), "tool_calls": list(tool_calls or [])},
                approval_payload={
                    "classification": "confirm_required",
                    "reason_code": mutation_payload.get("reason_code"),
                    "operation_class": mutation_payload.get("mutation_class"),
                    "governance_mode": (mutation_payload.get("approval_scope") or {}).get("governance_mode"),
                },
                pipeline=pipeline,
                target_fingerprint=str((mutation_payload.get("normalized_target") or {}).get("target_fingerprint") or "") or None,
            )
            if pending_state == "pending":
                return pending_result
            _mutation_auto_granted = pending_state == "auto_granted"
        else:
            _mutation_auto_granted = False
        if not _mutation_auto_granted:
            raise ToolGuardrailError(
                details={
                    "blocked_tools": [str((item or {}).get("name") or (item or {}).get("tool_name") or "").strip() for item in list(tool_calls or []) if isinstance(item, dict)],
                    "blocked_reasons": [mutation_payload.get("reason_code")],
                    "mutation_gate": mutation_payload,
                }
            )

    return approval_payload


def execute_tool_calls(
    *,
    tool_calls: list[dict] | None,
    effective_task: dict,
    tid: str | None,
    guard_cfg: dict,
    command: str | None,
    loop_trace_id: str,
    approval_payload: dict,
    pipeline: dict | None,
    compaction_svc,
) -> tuple[list[str], list[dict], int] | LocalExecutionResult:
    output_parts: list[str] = []
    loop_signals: list[dict] = []
    overall_exit_code = 0

    normalized_tool_calls = normalize_runtime_tool_calls(tool_calls)
    if not normalized_tool_calls:
        return output_parts, loop_signals, overall_exit_code

    allowed_tools = resolve_task_scope_allowed_tools(effective_task)
    known_tools = [definition.get("name") for definition in tool_registry.get_tool_definitions()]
    resolution = ToolIntentResolver().resolve(normalized_tool_calls, known_tools=known_tools)
    normalized_tool_calls = list(resolution.resolved_tool_calls)
    for event in resolution.remap_events:
        logger = current_app.logger if has_app_context() else logging.getLogger(__name__)
        logger.info(
            "tool_intent_resolved %s",
            json.dumps(
                {
                    "original_tool": event.original_tool,
                    "resolved_tool": event.resolved_tool,
                    "reason": event.reason,
                    "resolved_intent": event.resolved_intent,
                    "resolved_risk": event.resolved_risk,
                    "tool_class": event.tool_class,
                    "confidence": event.confidence,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        get_execution_audit_service().emit(
            operation_type="tool_intent_remap",
            outcome="remapped",
            trace_id=loop_trace_id,
            goal_id=effective_task.get("goal_id"),
            task_id=tid,
            actor_role="hub",
            details={
                "original_tool": event.original_tool,
                "resolved_tool": event.resolved_tool,
                "reason": event.reason,
                "resolved_intent": event.resolved_intent,
                "resolved_risk": event.resolved_risk,
                "tool_class": event.tool_class,
            },
        )
    worker_execution_contract = dict((effective_task or {}).get("worker_execution_contract") or {})
    allowed_tool_classes = {
        str(item).strip().lower()
        for item in list(worker_execution_contract.get("allowed_tool_classes") or [])
        if str(item).strip()
    }
    _enforce_worker_execution_contract_tool_classes(
        normalized_tool_calls=normalized_tool_calls,
        allowed_tool_classes=allowed_tool_classes,
    )
    if resolution.unresolved:
        reason_codes = sorted({item.reason_code for item in resolution.unresolved})
        summary = ", ".join(f"{item.original_tool}:{item.reason_code}" for item in resolution.unresolved)
        return LocalExecutionResult(
            output=f"[tool_intent] unresolved: {summary}",
            exit_code=1,
            retries_used=0,
            failure_type="tool_intent_unresolved_recoverable",
            retry_history=[{"attempt": 0, "reason_codes": reason_codes}],
            status="needs_review",
            loop_signals=[],
            loop_detection=None,
            approval_decision=approval_payload,
        )

    normalized_approval = get_approval_policy_service().evaluate(
        command=command,
        tool_calls=normalized_tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
    )
    normalized_approval_payload = normalized_approval.as_dict()
    if normalized_approval_payload.get("classification") == "blocked" and normalized_approval_payload.get("enforced"):
        raise ToolGuardrailError(
            details={
                "blocked_tools": [str((item or {}).get("name") or "").strip() for item in normalized_tool_calls],
                "blocked_reasons": [normalized_approval_payload.get("reason_code")],
                "approval": normalized_approval_payload,
            }
        )
    normalized_risk = evaluate_execution_risk(
        command=command,
        tool_calls=normalized_tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
    )
    if not normalized_risk.allowed:
        raise ToolGuardrailError(
            details={
                "blocked_tools": normalized_risk.blocked_tools,
                "blocked_reasons": normalized_risk.reasons,
                "guardrails": normalized_risk.details,
                "risk_level": normalized_risk.risk_level,
            }
        )
    normalized_mutation = get_mutation_gate_service().evaluate(
        command=command,
        tool_calls=normalized_tool_calls,
        task=effective_task,
        agent_cfg=guard_cfg,
        approval_decision=normalized_approval_payload,
        risk_decision=normalized_risk,
        trace_id=loop_trace_id,
        actor="system",
    ).as_dict()
    get_execution_audit_service().emit(
        operation_type="mutation_gate_decision",
        outcome=str(normalized_mutation.get("classification") or "unknown"),
        trace_id=loop_trace_id,
        goal_id=effective_task.get("goal_id"),
        task_id=tid,
        actor_role="hub",
        details={
            "reason_code": normalized_mutation.get("reason_code"),
            "mutation_class": normalized_mutation.get("mutation_class"),
            "normalized_target": normalized_mutation.get("normalized_target"),
            "approval_scope": normalized_mutation.get("approval_scope"),
            "source": "task_execution_service.normalized_tool_calls",
        },
    )
    if normalized_mutation.get("classification") in {"blocked", "confirm_required"}:
        raise ToolGuardrailError(
            details={
                "blocked_tools": [str((item or {}).get("name") or "").strip() for item in normalized_tool_calls],
                "blocked_reasons": [normalized_mutation.get("reason_code")],
                "mutation_gate": normalized_mutation,
            }
        )

    blocked_tools, blocked_reasons_by_tool = validate_task_scoped_tool_calls(
        normalized_tool_calls,
        allowed_tools=allowed_tools,
        known_tools=known_tools,
    )
    if pipeline is not None:
        append_stage(
            pipeline,
            name="task_scope_validation",
            status="ok" if not blocked_tools else "blocked",
            metadata={
                "allowed_tools": allowed_tools,
                "tool_call_count": len(normalized_tool_calls or []),
                "blocked_tools": blocked_tools,
            },
        )
    if blocked_tools:
        scope_decision = ToolGuardrailDecision(
            allowed=False,
            blocked_tools=blocked_tools,
            reasons=list(dict.fromkeys(blocked_reasons_by_tool.values())),
            details={
                "allowed_tools_scope": allowed_tools,
                "blocked_reasons_by_tool": blocked_reasons_by_tool,
            },
        )
        if tid:
            _append_guardrail_block_history(
                tid,
                effective_task,
                command,
                normalized_tool_calls,
                scope_decision,
                reason="tool_scope_blocked",
            )
        raise ToolGuardrailError(
            details={
                "blocked_tools": blocked_tools,
                "blocked_reasons": scope_decision.reasons,
                "blocked_reasons_by_tool": blocked_reasons_by_tool,
                "guardrails": scope_decision.details,
            }
        )

    token_usage = {
        "prompt_tokens": estimate_text_tokens(command or effective_task.get("description")),
        "history_tokens": estimate_text_tokens(json.dumps(effective_task.get("history", []), ensure_ascii=False)),
        "tool_calls_tokens": estimate_tool_calls_tokens(normalized_tool_calls),
    }
    token_usage["estimated_total_tokens"] = sum(int(token_usage.get(key) or 0) for key in token_usage)
    decision = evaluate_tool_call_guardrails(normalized_tool_calls, guard_cfg, token_usage=token_usage)
    if pipeline is not None:
        append_stage(
            pipeline,
            name="guardrails",
            status="ok" if decision.allowed else "blocked",
            metadata={"tool_call_count": len(normalized_tool_calls or []), "blocked_tools": decision.blocked_tools},
        )
    if not decision.allowed:
        if tid:
            _append_guardrail_block_history(tid, effective_task, command, normalized_tool_calls, decision)
        raise ToolGuardrailError(
            details={
                "blocked_tools": decision.blocked_tools,
                "blocked_reasons": decision.reasons,
                "guardrails": decision.details,
            }
        )

    for tool_call in normalized_tool_calls:
        name = str(tool_call.get("name") or tool_call.get("tool_name") or "").strip()
        args = tool_call.get("args") or tool_call.get("tool_input") or tool_call.get("parameters") or {}
        _SHELL_TOOL_NAMES = {"shell_execute", "run_command", "execute_command", "bash"}
        if name in _SHELL_TOOL_NAMES and isinstance(args, dict):
            _tc_cmd = str(args.get("command") or args.get("cmd") or "").strip()
            if _tc_cmd:
                _tc_analysis = ShellCommandAnalyzer().analyze(_tc_cmd, guard_cfg)
                if not _tc_analysis.allowed:
                    _reasons = [
                        f"shell_operator_unsupported:{op}"
                        if _tc_analysis.denied_reason == "unsupported_operator"
                        else (_tc_analysis.denied_reason or "shell_chain_invalid")
                        for op in (_tc_analysis.unsupported_operators or ["?"])
                    ]
                    if tid:
                        _append_guardrail_block_history(
                            tid,
                            effective_task,
                            _tc_cmd,
                            [tool_call],
                            ToolGuardrailDecision(
                                allowed=False,
                                blocked_tools=[name],
                                reasons=_reasons,
                                details={"shell_command_analysis": _tc_analysis.as_dict()},
                            ),
                            reason="shell_command_chain_blocked",
                        )
                    raise ToolGuardrailError(
                        details={
                            "blocked_tools": [name],
                            "blocked_reasons": _reasons,
                            "shell_command_analysis": _tc_analysis.as_dict(),
                        }
                    )
        logger = current_app.logger if has_app_context() else logging.getLogger(__name__)
        logger.info("Task %s führt Tool aus: %s mit %s", tid or "<direct>", name, args)
        tool_result = tool_registry.execute(name, args)
        if pipeline is not None:
            append_stage(
                pipeline,
                name="tool_call",
                status="ok" if tool_result.success else "error",
                metadata={"tool": name},
            )
        result_text = f"Tool '{name}': {'Erfolg' if tool_result.success else 'Fehler'}"
        if tool_result.output:
            result_text += f"\nOutput: {tool_result.output}"
        if tool_result.error:
            result_text += f"\nError: {tool_result.error}"
            if not is_non_fatal_tool_error(tool_name=name, error_text=tool_result.error):
                overall_exit_code = 1
        _compaction = compaction_svc.compact(
            tool_name=str(name or "tool"),
            output=result_text,
            task_kind=str(effective_task.get("task_kind") or ""),
        )
        if _compaction.compaction_ratio < 1.0:
            log_audit("tool_output_compacted", {
                "tool": name,
                "task_id": tid,
                "input_chars": _compaction.input_chars,
                "output_chars": _compaction.output_chars,
                "compaction_ratio": _compaction.compaction_ratio,
                "applied_rule_ids": _compaction.applied_rule_ids,
                "preserved_signal_count": len(_compaction.preserved_signals),
                "original_ref": _compaction.original_ref,
            })
        output_parts.append(_compaction.compacted_text)
        from agent.services.doom_loop_service import get_doom_loop_service

        loop_signals.append(
            get_doom_loop_service().build_signal(
                task_id=tid,
                trace_id=loop_trace_id,
                backend_name=str(name or "tool"),
                action_type="tool_call",
                failure_type="success" if tool_result.success else "tool_failure",
                iteration_count=len(loop_signals) + 1,
                action_signature=loop_signature(f"{name}:{json.dumps(args or {}, sort_keys=True, ensure_ascii=True)}"),
                progress_made=bool(tool_result.success),
            )
        )

    return output_parts, loop_signals, overall_exit_code


