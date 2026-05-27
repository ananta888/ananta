from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import shlex
import time
from dataclasses import dataclass

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.common.errors import ToolGuardrailError
from agent.llm_benchmarks import estimate_cost_units
from agent.llm_integration import _call_llm
from agent.metrics import RETRIES_TOTAL
from agent.models import (
    ResearchArtifact,
    ResearchContextSummaryContract,
    CostSummaryContract,
    TaskArtifactReferenceContract,
    TaskCliResultContract,
    TaskExecutionPolicyContract,
    TaskReviewStateContract,
    TaskRoutingContract,
    TaskScopedStepExecuteResponse,
    TaskScopedStepProposeResponse,
    TaskStepExecuteRequest,
    TaskStepProposeRequest,
    TaskStepProposeResponse,
    TaskWorkerContextSummaryContract,
)
from agent.pipeline_trace import append_stage
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.command_chain_parser import CommandChainParser
from agent.services.command_to_tool_mapper import CommandToToolMapper
from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy
from agent.services.segment_preflight_validator import SegmentPreflightValidator
from agent.services.tool_intent_resolver import ToolIntentResolver
from agent.services.tool_intent_taxonomy_service import get_tool_intent_taxonomy_service
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    normalize_tool_call_name,
    resolve_task_scope_allowed_tools,
    resolve_execution_policy,
    should_retry_execution,
    validate_task_scoped_tool_calls,
)
from agent.services.doom_loop_service import get_doom_loop_service
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import get_local_task_status, get_task_runtime_service
from agent.shell import get_shell
from agent.tool_guardrails import ToolGuardrailDecision, estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry
from agent.services.tool_output_compaction_service import ToolOutputCompactionService, _build_from_config as _build_compaction_svc


@dataclass(frozen=True)
class LocalExecutionResult:
    output: str
    exit_code: int | None
    retries_used: int
    failure_type: str
    retry_history: list[dict]
    status: str
    loop_signals: list[dict]
    loop_detection: dict | None
    approval_decision: dict | None


class TaskExecutionService:
    """Encapsulates direct proposal/execution route behavior away from Flask handlers."""

    def __init__(self) -> None:
        self._compaction_svc: ToolOutputCompactionService | None = None

    def _get_compaction_svc(self, guard_cfg: dict) -> ToolOutputCompactionService:
        if self._compaction_svc is None:
            cfg = guard_cfg.get("tool_output_compaction") if isinstance(guard_cfg, dict) else None
            self._compaction_svc = _build_compaction_svc(cfg)
        return self._compaction_svc

    def resolve_policy(
        self,
        request_data: TaskStepExecuteRequest,
        *,
        source: str,
        agent_cfg: dict | None = None,
    ) -> TaskExecutionPolicyContract:
        resolved_agent_cfg = agent_cfg or {}
        execution_policy = resolve_execution_policy(
            request_data,
            agent_cfg=resolved_agent_cfg,
            source=source,
        )
        self._apply_implicit_execution_defaults(execution_policy, request_data, resolved_agent_cfg)
        return execution_policy

    def propose_direct_step(
        self,
        request_data: TaskStepProposeRequest,
        *,
        agent_cfg: dict,
        provider_urls: dict,
        openai_api_key: str | None,
        agent_name: str,
        llm_caller=None,
    ) -> dict:
        prompt = request_data.prompt or "Was soll ich als nächstes tun?"
        caller = llm_caller or _call_llm

        if request_data.providers:
            results: dict[str, dict] = {}

            def _call_single(provider_name: str) -> tuple[str, dict]:
                try:
                    provider_parts = provider_name.split(":", 1)
                    provider = provider_parts[0]
                    model = provider_parts[1] if len(provider_parts) > 1 else (request_data.model or agent_cfg.get("model", "llama3"))
                    raw = caller(
                        provider=provider,
                        model=model,
                        prompt=prompt,
                        urls=provider_urls,
                        api_key=openai_api_key,
                        temperature=request_data.temperature,
                    )
                    return provider_name, self._build_proposal_payload(raw)
                except Exception as exc:
                    return provider_name, {"error": str(exc)}

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(request_data.providers)) as executor:
                future_to_provider = {executor.submit(_call_single, provider_name): provider_name for provider_name in request_data.providers}
                for future in concurrent.futures.as_completed(future_to_provider):
                    provider_name, result = future.result()
                    results[provider_name] = result

            main_provider = request_data.providers[0]
            main_result = results.get(main_provider, {})
            return TaskStepProposeResponse(
                reason=main_result.get("reason", "Fehler bei primärem Provider"),
                command=main_result.get("command"),
                tool_calls=main_result.get("tool_calls"),
                raw=main_result.get("raw", ""),
                comparisons=results,
            ).model_dump()

        provider = request_data.provider or agent_cfg.get("provider", "ollama")
        model = request_data.model or agent_cfg.get("model", "llama3")
        raw = caller(
            provider=provider,
            model=model,
            prompt=prompt,
            urls=provider_urls,
            api_key=openai_api_key,
            temperature=request_data.temperature,
        )
        proposal_payload = self._build_proposal_payload(raw)

        if request_data.task_id:
            proposal_state = {"reason": proposal_payload["reason"]}
            if proposal_payload.get("command"):
                proposal_state["command"] = proposal_payload["command"]
            if proposal_payload.get("tool_calls"):
                proposal_state["tool_calls"] = proposal_payload["tool_calls"]
            get_task_runtime_service().update_local_task_status(request_data.task_id, "proposing", last_proposal=proposal_state)
            _log_terminal_entry(agent_name, 0, "in", prompt=prompt, task_id=request_data.task_id)
            _log_terminal_entry(
                agent_name,
                0,
                "out",
                reason=proposal_payload["reason"],
                command=proposal_payload.get("command"),
                tool_calls=proposal_payload.get("tool_calls"),
                task_id=request_data.task_id,
            )

        return TaskStepProposeResponse(
            reason=proposal_payload["reason"],
            command=proposal_payload.get("command"),
            tool_calls=proposal_payload.get("tool_calls"),
            raw=proposal_payload["raw"],
        ).model_dump()

    def execute_direct_step(
        self,
        request_data: TaskStepExecuteRequest,
        *,
        agent_cfg: dict,
        agent_name: str,
    ) -> dict:
        execution_policy = self.resolve_policy(request_data, source="execute_step", agent_cfg=agent_cfg)
        execution_run = self.execute_local_step(
            tid=request_data.task_id,
            task=get_local_task_status(request_data.task_id) if request_data.task_id else None,
            command=request_data.command,
            tool_calls=request_data.tool_calls,
            execution_policy=execution_policy,
            guard_cfg=agent_cfg,
        )

        if request_data.task_id:
            get_task_runtime_service().update_local_task_status(
                request_data.task_id,
                execution_run.status,
                last_output=execution_run.output,
                last_exit_code=execution_run.exit_code,
            )

        _log_terminal_entry(
            agent_name,
            0,
            "out",
            command=request_data.command,
            tool_calls=request_data.tool_calls,
            task_id=request_data.task_id,
        )
        _log_terminal_entry(
            agent_name,
            0,
            "in",
            output=execution_run.output,
            exit_code=execution_run.exit_code,
            task_id=request_data.task_id,
        )

        estimated_tokens = max(
            0,
            estimate_text_tokens(request_data.command) + estimate_text_tokens(execution_run.output) + estimate_tool_calls_tokens(request_data.tool_calls),
        )
        cost_units, pricing_source = estimate_cost_units(
            agent_cfg,
            "",
            "",
            estimated_tokens,
        )
        return {
            "output": execution_run.output,
            "exit_code": execution_run.exit_code,
            "task_id": request_data.task_id,
            "status": execution_run.status,
            "retry_history": execution_run.retry_history if request_data.command else [],
            "loop_signals": execution_run.loop_signals or None,
            "loop_detection": execution_run.loop_detection,
            "approval_decision": execution_run.approval_decision,
            "cost_summary": {
                "provider": None,
                "model": None,
                "task_kind": request_data.task_kind,
                "tokens_total": estimated_tokens,
                "cost_units": cost_units,
                "latency_ms": None,
                "pricing_source": pricing_source,
            },
            "retries_used": execution_run.retries_used,
            "failure_type": execution_run.failure_type,
            "execution_policy": execution_policy.model_dump(),
        }

    def execute_local_step(
        self,
        *,
        tid: str | None,
        task: dict | None,
        command: str | None,
        tool_calls: list[dict] | None,
        execution_policy: TaskExecutionPolicyContract,
        guard_cfg: dict,
        working_directory: str | None = None,
        pipeline: dict | None = None,
        exec_started_at: float | None = None,
    ) -> LocalExecutionResult:
        output_parts: list[str] = []
        overall_exit_code = 0
        retries_used = 0
        failure_type = "success"
        retry_history: list[dict] = []
        effective_task = task or {}
        loop_signals: list[dict] = []
        loop_trace_id = self._resolve_loop_trace_id(effective_task)
        loop_service = get_doom_loop_service()
        loop_signature_for_command = self._loop_signature(command)
        # SCG-004: analyse the command chain before running full-string preflight guards.
        # For allowed chains, approval/risk use segment-aware mode instead of full-string matching.
        # For commands with unsupported operators (pipes etc.), block early with a clear reason.
        _chain_preflight_deferred = False
        _command_analysis = None
        if command:
            _command_analysis = ShellCommandAnalyzer().analyze(command, guard_cfg)
            if not _command_analysis.allowed:
                if pipeline is not None:
                    append_stage(
                        pipeline,
                        name="command_chain_analysis",
                        status="blocked",
                        metadata=_command_analysis.as_dict(),
                    )
                raise ToolGuardrailError(
                    details={
                        "blocked_tools": [],
                        "blocked_reasons": [
                            f"shell_operator_unsupported:{op}"
                            if _command_analysis.denied_reason == "unsupported_operator"
                            else (_command_analysis.denied_reason or "shell_chain_invalid")
                            for op in (_command_analysis.unsupported_operators or ["?"])
                        ],
                        "shell_command_analysis": _command_analysis.as_dict(),
                    }
                )
            if _command_analysis.contains_chain:
                _chain_preflight_deferred = True
                if pipeline is not None:
                    append_stage(
                        pipeline,
                        name="command_chain_analysis",
                        status="ok",
                        metadata={
                            **_command_analysis.as_dict(),
                            "approval_preflight_deferred_to_segments": True,
                        },
                    )

        approval_decision = get_approval_policy_service().evaluate(
            command=command,
            tool_calls=tool_calls,
            task=effective_task,
            agent_cfg=guard_cfg,
            command_analysis=_command_analysis,
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
        # For allowed chain commands, per-segment approval/risk runs in _execute_shell_command_with_policy.
        # Only block here for non-chain commands (or when tool_calls is the concern, not the command).
        if approval_payload.get("classification") == "blocked" and approval_payload.get("enforced") and not _chain_preflight_deferred:
            if tid:
                self._append_approval_block_history(
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
                self._append_approval_block_history(
                    tid=tid,
                    task=effective_task,
                    command=command,
                    tool_calls=tool_calls,
                    approval_decision=approval_payload,
                    reason="approval_confirmation_required",
                )
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
                self._append_guardrail_block_history(
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

        normalized_tool_calls = self._normalize_runtime_tool_calls(tool_calls)
        if normalized_tool_calls:
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
            self._enforce_worker_execution_contract_tool_classes(
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

            # Re-run policy checks against final normalized intents.
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
                    self._append_guardrail_block_history(
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
                    self._append_guardrail_block_history(tid, effective_task, command, normalized_tool_calls, decision)
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
                # SCG-008: for shell tool calls, analyse the command argument for unsupported operators.
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
                                self._append_guardrail_block_history(
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
                    if not self._is_non_fatal_tool_error(tool_name=name, error_text=tool_result.error):
                        overall_exit_code = 1
                # OHA-006: compact tool output before storing in output_parts
                _compaction = self._get_compaction_svc(guard_cfg).compact(
                    tool_name=str(name or "tool"),
                    output=result_text,
                    task_kind=str(effective_task.get("task_kind") or ""),
                )
                if _compaction.compaction_ratio < 1.0:
                    # OHA-007: emit audit event for compaction telemetry
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
                loop_signals.append(
                    loop_service.build_signal(
                        task_id=tid,
                        trace_id=loop_trace_id,
                        backend_name=str(name or "tool"),
                        action_type="tool_call",
                        failure_type="success" if tool_result.success else "tool_failure",
                        iteration_count=len(loop_signals) + 1,
                        action_signature=self._loop_signature(f"{name}:{json.dumps(args or {}, sort_keys=True, ensure_ascii=True)}"),
                        progress_made=bool(tool_result.success),
                    )
                )

        if command:
            _wec = effective_task.get("worker_execution_context") or {}
            shell_mode = str(_wec.get("shell_command_mode") or "").strip().lower()
            policy = ShellCommandPolicy.from_agent_cfg(guard_cfg)
            # Complex shell mode requires both task intent (pipeline mode) and explicit policy allowance.
            allow_complex_shell = shell_mode == "pipeline" and bool(policy.allow_complex_shell_mode)
            command_output, command_exit_code, retries_used, failure_type, retry_history = self._execute_shell_command_with_policy(
                tid=tid,
                command=command,
                execution_policy=execution_policy,
                working_directory=working_directory,
                allow_complex_shell=allow_complex_shell,
                task=effective_task,
                agent_cfg=guard_cfg,
            )
            # OHA-006: compact shell output before storing in output_parts
            _shell_compaction = self._get_compaction_svc(guard_cfg).compact(
                tool_name="shell_execute",
                output=command_output,
                command=command,
                task_kind=str(effective_task.get("task_kind") or ""),
            )
            if _shell_compaction.compaction_ratio < 1.0:
                # OHA-007: emit audit event for shell compaction telemetry
                log_audit("tool_output_compacted", {
                    "tool": "shell_execute",
                    "task_id": tid,
                    "input_chars": _shell_compaction.input_chars,
                    "output_chars": _shell_compaction.output_chars,
                    "compaction_ratio": _shell_compaction.compaction_ratio,
                    "applied_rule_ids": _shell_compaction.applied_rule_ids,
                    "preserved_signal_count": len(_shell_compaction.preserved_signals),
                    "original_ref": _shell_compaction.original_ref,
                })
            output_parts.append(_shell_compaction.compacted_text)
            if command_exit_code != 0:
                overall_exit_code = command_exit_code
            if pipeline is not None:
                append_stage(
                    pipeline,
                    name="shell_execute",
                    status="ok" if command_exit_code == 0 else "error",
                    metadata={"exit_code": command_exit_code, "failure_type": failure_type, "retries_used": retries_used},
                    started_at=exec_started_at,
                )
            for retry in retry_history:
                loop_signals.append(
                    loop_service.build_signal(
                        task_id=tid,
                        trace_id=loop_trace_id,
                        backend_name="shell",
                        action_type="shell_command",
                        failure_type=str(retry.get("failure_type") or "command_failure"),
                        iteration_count=int(retry.get("attempt") or (len(loop_signals) + 1)),
                        action_signature=loop_signature_for_command,
                        progress_made=False,
                    )
                )
            if command_exit_code == 0:
                loop_signals.append(
                    loop_service.build_signal(
                        task_id=tid,
                        trace_id=loop_trace_id,
                        backend_name="shell",
                        action_type="shell_command",
                        failure_type="success",
                        iteration_count=max(1, len(retry_history) + 1),
                        action_signature=loop_signature_for_command,
                        progress_made=True,
                    )
                )
            elif not retry_history:
                loop_signals.append(
                    loop_service.build_signal(
                        task_id=tid,
                        trace_id=loop_trace_id,
                        backend_name="shell",
                        action_type="shell_command",
                        failure_type=failure_type,
                        iteration_count=1,
                        action_signature=loop_signature_for_command,
                        progress_made=False,
                    )
                )

        final_output = "\n---\n".join(output_parts)
        final_exit_code = overall_exit_code
        effective_failure_type = failure_type if command else ("success" if final_exit_code == 0 else "tool_failure")
        recoverable_shell_missing_binary = self._is_recoverable_missing_binary_failure(
            command=command,
            output=final_output,
            exit_code=final_exit_code,
        )
        loop_detection = self._evaluate_doom_loop(
            tid=tid,
            task=effective_task,
            guard_cfg=guard_cfg,
            loop_signals=loop_signals,
        )
        return LocalExecutionResult(
            output=final_output,
            exit_code=final_exit_code,
            retries_used=retries_used if command else 0,
            failure_type=effective_failure_type,
            retry_history=retry_history if command else [],
            status="completed" if final_exit_code == 0 else ("needs_review" if recoverable_shell_missing_binary else "failed"),
            loop_signals=loop_signals,
            loop_detection=loop_detection,
            approval_decision=approval_payload,
        )

    @staticmethod
    def _is_recoverable_missing_binary_failure(*, command: str | None, output: str | None, exit_code: int | None) -> bool:
        if not command or int(exit_code or 0) == 0:
            return False
        text = str(output or "").lower()
        if "command not found" not in text:
            return False
        # Missing optional CLIs should not hard-fail default execution paths.
        return True

    @staticmethod
    def _is_non_fatal_tool_error(*, tool_name: str | None, error_text: str | None) -> bool:
        name = str(tool_name or "").strip().lower()
        text = str(error_text or "").strip().lower()
        # Keep local execution progressing when optional web search capability is disabled.
        if name == "web_search" and "action pack 'browser'" in text and "deaktiviert" in text:
            return True
        return False

    def persist_task_proposal_result(
        self,
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
            routing=self._routing_contract(routing),
            cli_result=self._cli_result_contract(cli_result),
            comparisons=comparisons,
            research_artifact=self._research_artifact_contract(research_artifact),
            research_context=self._research_context_contract(research_context),
            worker_context=self._worker_context_contract(worker_context),
            trace=trace,
            pipeline=pipeline,
            review=self._review_contract(review),
        ).model_dump(exclude_none=True)

    def finalize_task_execution_response(
        self,
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
        execution_policy: TaskExecutionPolicyContract,
        review: dict | None = None,
        artifact_refs: list[dict] | None = None,
        extra_history: dict | None = None,
    ) -> dict:
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
            review=self._review_contract(review),
            artifacts=[TaskArtifactReferenceContract.model_validate(ref) for ref in list(artifact_refs or [])] or None,
            execution_scope=tracking.get("execution_scope") or None,
            execution_provenance=tracking.get("execution_provenance") or None,
        )
        return response.model_dump(exclude_none=True)

    def _append_guardrail_block_history(
        self,
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
            "command": command[:200] if command else None,  # SCG-012: redact long commands
            "tool_calls": tool_calls or [],
            "blocked_tools": decision.blocked_tools,
            "blocked_reasons": decision.reasons,
            "guardrails": decision.details,
            "timestamp": time.time(),
        }
        # SCG-012: include chain diagnostics when available
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
            trace_id=self._resolve_loop_trace_id(task or {}),
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
        self,
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
            trace_id=self._resolve_loop_trace_id(task or {}),
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
            trace_id=self._resolve_loop_trace_id(task or {}),
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

    def _resolve_loop_trace_id(self, task: dict) -> str | None:
        trace_id = str(task.get("goal_trace_id") or "").strip()
        if trace_id:
            return trace_id
        proposal_trace = (task.get("last_proposal") or {}).get("trace") if isinstance(task.get("last_proposal"), dict) else {}
        trace_id = str((proposal_trace or {}).get("trace_id") or "").strip()
        return trace_id or None

    def _enforce_worker_execution_contract_tool_classes(
        self,
        *,
        normalized_tool_calls: list[dict],
        allowed_tool_classes: set[str],
    ) -> None:
        if not allowed_tool_classes:
            return
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

    def _loop_signature(self, value: str | None) -> str | None:
        signature = str(value or "").strip()
        if not signature:
            return None
        return signature[:260]

    def _evaluate_doom_loop(
        self,
        *,
        tid: str | None,
        task: dict,
        guard_cfg: dict,
        loop_signals: list[dict],
    ) -> dict | None:
        if not loop_signals:
            return None
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
                        "trace_id": self._resolve_loop_trace_id(task),
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

    def _apply_implicit_execution_defaults(
        self,
        execution_policy: TaskExecutionPolicyContract,
        request_data: TaskStepExecuteRequest,
        agent_cfg: dict,
    ) -> None:
        explicit_fields = set(getattr(request_data, "model_fields_set", set()) or set())
        if "retries" not in explicit_fields and agent_cfg.get("command_retries") is not None:
            execution_policy.retries = max(0, min(int(agent_cfg.get("command_retries") or 0), 10))
        if "retry_delay" not in explicit_fields and agent_cfg.get("command_retry_delay") is not None:
            execution_policy.retry_delay_seconds = max(0, min(int(agent_cfg.get("command_retry_delay") or 0), 60))
        if request_data.retry_policy_override is None and agent_cfg.get("command_retryable_exit_codes") is not None:
            execution_policy.retryable_exit_codes = [int(code) for code in list(agent_cfg.get("command_retryable_exit_codes") or [])]
        if request_data.retry_policy_override is None and agent_cfg.get("command_retry_on_timeouts") is not None:
            execution_policy.retry_on_timeouts = bool(agent_cfg.get("command_retry_on_timeouts"))

    def _normalize_runtime_tool_calls(self, tool_calls: list[dict] | None) -> list[dict]:
        normalized: list[dict] = []
        for item in list(tool_calls or []):
            if not isinstance(item, dict):
                continue
            tc = dict(item)
            raw_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
            args = tc.get("args") or tc.get("tool_input") or tc.get("parameters") or {}
            if not isinstance(args, dict):
                args = {}
            canonical = normalize_tool_call_name(raw_name)
            if canonical:
                tc["name"] = canonical
                tc["tool_name"] = canonical
            if "command" not in args and args.get("cmd"):
                args["command"] = args.get("cmd")
            tc["args"] = args
            normalized.append(tc)
        return normalized

    def _build_proposal_payload(self, raw_response: str) -> dict:
        reason = _extract_reason(raw_response)
        command = _extract_command(raw_response)
        tool_calls = _extract_tool_calls(raw_response)
        return {
            "reason": reason,
            "command": command if command and command != raw_response.strip() else None,
            "tool_calls": tool_calls,
            "raw": raw_response,
        }

    def _cli_result_contract(self, cli_result: dict | None) -> TaskCliResultContract | None:
        if not isinstance(cli_result, dict):
            return None
        return TaskCliResultContract.model_validate(cli_result)

    def _routing_contract(self, routing: dict | None) -> TaskRoutingContract | None:
        if not isinstance(routing, dict):
            return None
        return TaskRoutingContract.model_validate(routing)

    def _review_contract(self, review: dict | None) -> TaskReviewStateContract | None:
        if not isinstance(review, dict):
            return None
        return TaskReviewStateContract.model_validate(review)

    def _worker_context_contract(self, worker_context: dict | None) -> TaskWorkerContextSummaryContract | None:
        if not isinstance(worker_context, dict):
            return None
        return TaskWorkerContextSummaryContract.model_validate(worker_context)

    def _research_artifact_contract(self, research_artifact: dict | None) -> ResearchArtifact | None:
        if not isinstance(research_artifact, dict):
            return None
        return ResearchArtifact.model_validate(research_artifact)

    def _research_context_contract(self, research_context: dict | None) -> ResearchContextSummaryContract | None:
        if not isinstance(research_context, dict):
            return None
        return ResearchContextSummaryContract.model_validate(research_context)

    @staticmethod
    def _repair_command_transcription_noise(command: str) -> str:
        # Repair common streamed-output artifacts like "mode> ls.py" -> "models.py".
        repaired = str(command or "")
        join_fragment_pattern = re.compile(r"(?<=[A-Za-z0-9_./-])>\s+(?=[A-Za-z0-9_./-])")
        while True:
            next_value, count = join_fragment_pattern.subn("", repaired)
            repaired = next_value
            if count <= 0:
                break
        return repaired.strip()

    def _execute_single_shell_command_with_policy(
        self,
        *,
        tid: str | None,
        command: str,
        execution_policy: TaskExecutionPolicyContract,
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
        self,
        *,
        tid: str | None,
        command: str,
        execution_policy: TaskExecutionPolicyContract,
        working_directory: str | None = None,
        allow_complex_shell: bool = False,
        task: dict | None = None,
        agent_cfg: dict | None = None,
    ) -> tuple[str, int | None, int, str, list[dict]]:
        repaired_command = self._repair_command_transcription_noise(command)
        plan = CommandChainParser().parse(repaired_command)
        if not plan.allowed and not allow_complex_shell:
            message = (
                "Error: Unsupported shell operators in command: "
                + ", ".join(plan.unsupported_operators or [str(plan.denied_reason or "invalid_command")])
                + ". Request a single command without chaining/redirection."
            )
            return message, -1, 0, "command_runtime_error", []
        if not plan.allowed and allow_complex_shell:
            # Pipeline-mode: run the full command as-is via shell instead of splitting
            command_segments = [repaired_command]
            chain_segments = []
        else:
            command_segments = [seg.raw for seg in plan.segments]
            chain_segments = list(plan.segments)
        if not command_segments:
            return "", 0, 0, "success", []

        # SCG-005: use SegmentPreflightValidator — every segment must be allowed before any execution.
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
            mapped = CommandToToolMapper().map(segment)
            if mapped.mapped_tool and mapped.mapped_tool in known_tool_set:
                tool_result = tool_registry.execute(mapped.mapped_tool, mapped.args)
                output = str(tool_result.output or "")
                exit_code = 0 if tool_result.success else 1
                retries_used = 0
                failure_type = "success" if tool_result.success else "tool_failure"
                retry_history = [{"attempt": 1, "mapped_tool": mapped.mapped_tool, "mapping_reason": mapped.reason}]
            else:
                output, exit_code, retries_used, failure_type, retry_history = self._execute_single_shell_command_with_policy(
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


task_execution_service = TaskExecutionService()


def get_task_execution_service() -> TaskExecutionService:
    return task_execution_service
