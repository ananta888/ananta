from __future__ import annotations

import concurrent.futures
import json
import logging

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.common.errors import ToolGuardrailError
from agent.llm_benchmarks import estimate_cost_units
from agent.llm_integration import _call_llm
from agent.models import (
    TaskExecutionPolicyContract,
    TaskStepExecuteRequest,
    TaskStepProposeRequest,
    TaskStepProposeResponse,
)
from agent.pipeline_trace import append_stage
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy
from agent.services.tool_intent_resolver import ToolIntentResolver
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.task_execution_policy_service import (
    resolve_task_scope_allowed_tools,
    resolve_execution_policy,
    validate_task_scoped_tool_calls,
)
from agent.services.task_runtime_service import get_local_task_status, get_task_runtime_service
from agent.tool_guardrails import ToolGuardrailDecision, estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import _log_terminal_entry
from agent.services.tool_output_compaction_service import ToolOutputCompactionService, _build_from_config as _build_compaction_svc

from agent.services.task_execution_context_builder import (
    LocalExecutionResult,
    apply_implicit_execution_defaults,
    approval_call_identity,
    build_proposal_payload,
    is_non_fatal_tool_error,
    is_recoverable_missing_binary_failure,
    loop_signature,
    normalize_runtime_tool_calls,
    resolve_loop_trace_id,
)
from agent.services.task_execution_result_handler import (
    _append_approval_block_history,
    _append_guardrail_block_history,
    _evaluate_doom_loop,
    _enforce_worker_execution_contract_tool_classes,
    _enter_pending_approval,
    _execute_shell_command_with_policy,
)


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
        apply_implicit_execution_defaults(execution_policy, request_data, resolved_agent_cfg)
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
                    return provider_name, build_proposal_payload(raw)
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
        proposal_payload = build_proposal_payload(raw)

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
        loop_trace_id = resolve_loop_trace_id(effective_task)
        loop_signature_for_command = loop_signature(command)
        _chain_preflight_deferred = False
        _command_analysis = None
        _wec_for_analysis = effective_task.get("worker_execution_context") or {}
        _shell_mode_for_analysis = str(_wec_for_analysis.get("shell_command_mode") or "").strip().lower()
        _shell_policy_for_analysis = ShellCommandPolicy.from_agent_cfg(guard_cfg)
        allow_complex_shell = (
            _shell_mode_for_analysis == "pipeline"
            and bool(_shell_policy_for_analysis.allow_complex_shell_mode)
        )
        if command:
            analysis_cfg = dict(guard_cfg or {})
            if allow_complex_shell:
                shell_cfg = dict((analysis_cfg.get("shell_command_policy") or {}))
                deny_ops = [str(op) for op in list(shell_cfg.get("deny_operators") or [])]
                shell_cfg["deny_operators"] = [op for op in deny_ops if op != "|"]
                shell_cfg["allow_complex_shell_mode"] = True
                analysis_cfg["shell_command_policy"] = shell_cfg
            _command_analysis = ShellCommandAnalyzer().analyze(command, analysis_cfg)
            if not _command_analysis.allowed:
                if allow_complex_shell and _command_analysis.denied_reason == "unsupported_operator":
                    _chain_preflight_deferred = True
                    if pipeline is not None:
                        append_stage(
                            pipeline,
                            name="command_chain_analysis",
                            status="ok",
                            metadata={
                                **_command_analysis.as_dict(),
                                "approval_preflight_deferred_to_segments": True,
                                "allow_complex_shell_override": True,
                            },
                        )
                    _command_analysis = None
                else:
                    if pipeline is not None:
                        append_stage(
                            pipeline,
                            name="command_chain_analysis",
                            status="blocked",
                            metadata=_command_analysis.as_dict(),
                        )
                    blocked_reasons = [
                        f"shell_operator_unsupported:{op}"
                        if _command_analysis.denied_reason == "unsupported_operator"
                        else (_command_analysis.denied_reason or "shell_chain_invalid")
                        for op in (_command_analysis.unsupported_operators or ["?"])
                    ]
                    return LocalExecutionResult(
                        output=f"Error: blocked by shell command policy ({', '.join(blocked_reasons)})",
                        exit_code=-1,
                        retries_used=0,
                        failure_type="command_runtime_error",
                        retry_history=[{"attempt": 0, "blocked_reasons": blocked_reasons}],
                        status="blocked",
                        loop_signals=[],
                        loop_detection=None,
                        approval_decision={
                            "classification": "blocked",
                            "reason_code": blocked_reasons[0] if blocked_reasons else "shell_chain_invalid",
                        },
                    )
            if _command_analysis is not None and _command_analysis.contains_chain:
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
                _chain_preflight_deferred = True

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

        normalized_tool_calls = normalize_runtime_tool_calls(tool_calls)
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
                _compaction = self._get_compaction_svc(guard_cfg).compact(
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

        if command:
            command_output, command_exit_code, retries_used, failure_type, retry_history = _execute_shell_command_with_policy(
                tid=tid,
                command=command,
                execution_policy=execution_policy,
                working_directory=working_directory,
                allow_complex_shell=allow_complex_shell,
                task=effective_task,
                agent_cfg=guard_cfg,
            )
            _shell_compaction = self._get_compaction_svc(guard_cfg).compact(
                tool_name="shell_execute",
                output=command_output,
                command=command,
                task_kind=str(effective_task.get("task_kind") or ""),
            )
            if _shell_compaction.compaction_ratio < 1.0:
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
            from agent.services.doom_loop_service import get_doom_loop_service

            loop_service = get_doom_loop_service()
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
        recoverable_shell_missing_binary = is_recoverable_missing_binary_failure(
            command=command,
            output=final_output,
            exit_code=final_exit_code,
        )
        loop_detection = _evaluate_doom_loop(
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

    def _execute_shell_command_with_policy(
        self,
        *,
        tid: str | None,
        command: str,
        execution_policy,
        working_directory: str | None = None,
        allow_complex_shell: bool = False,
        task: dict | None = None,
        agent_cfg: dict | None = None,
    ):
        return _execute_shell_command_with_policy(
            tid=tid,
            command=command,
            execution_policy=execution_policy,
            working_directory=working_directory,
            allow_complex_shell=allow_complex_shell,
            task=task,
            agent_cfg=agent_cfg,
        )

    @staticmethod
    def _is_non_fatal_tool_error(*, tool_name: str | None, error_text: str | None) -> bool:
        return is_non_fatal_tool_error(tool_name=tool_name, error_text=error_text)

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
        _append_guardrail_block_history(
            tid,
            task,
            command,
            tool_calls,
            decision,
            reason=reason,
            command_chain_summary=command_chain_summary,
        )

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
        from agent.services.task_execution_result_handler import _persist_task_proposal_result

        return _persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=reason,
            raw=raw,
            backend=backend,
            model=model,
            routing=routing,
            cli_result=cli_result,
            worker_context=worker_context,
            trace=trace,
            review=review,
            pipeline=pipeline,
            command=command,
            tool_calls=tool_calls,
            comparisons=comparisons,
            research_artifact=research_artifact,
            research_context=research_context,
            forwarded_request=forwarded_request,
            history_event=history_event,
        )

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
        execution_policy,
        review: dict | None = None,
        artifact_refs: list[dict] | None = None,
        extra_history: dict | None = None,
    ) -> dict:
        from agent.services.task_execution_result_handler import _finalize_task_execution_response

        return _finalize_task_execution_response(
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
            execution_policy=execution_policy,
            review=review,
            artifact_refs=artifact_refs,
            extra_history=extra_history,
        )


task_execution_service = TaskExecutionService()


def get_task_execution_service() -> TaskExecutionService:
    return task_execution_service
