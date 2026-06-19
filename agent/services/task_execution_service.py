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
from agent.services.task_execution_step_executor import (
    run_preflight_guards,
    execute_tool_calls,
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

        # HDE-005/HDE-020: reuse-before-LLM. When hub-direct execution is
        # enabled and the router classifies the prompt as safely
        # deterministic, the call is authorized and dispatched to the
        # worker runtime — no LLM is invoked and no llm_call_profile is
        # created. Policy blocks never silently fall back to the LLM.
        direct_response = self._try_hub_direct_execution(request_data, prompt=prompt, agent_cfg=agent_cfg)
        if direct_response is not None:
            return direct_response

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

    def _try_hub_direct_execution(
        self,
        request_data: TaskStepProposeRequest,
        *,
        prompt: str,
        agent_cfg: dict,
    ) -> dict | None:
        """HDE-005: classify, authorize and dispatch before any LLM call.

        Returns None when the worker/LLM path should run (feature off,
        not eligible with fallback, or no resolvable workspace). Policy
        blocks and approval-required outcomes return a direct response —
        they must not be bypassed by a silent LLM fallback (HDE-020).
        """
        from agent.common.audit import (
            AUDIT_HUB_DIRECT_CANDIDATE_DETECTED,
            AUDIT_HUB_DIRECT_FALLBACK_TO_WORKER,
            audit_hub_direct_event,
        )
        from agent.services.hub_direct_execution_router import get_hub_direct_execution_router
        from agent.services.hub_tool_execution_adapter import get_hub_tool_execution_adapter
        from agent.services.task_execution_metrics import record_hub_direct_metric

        cfg = agent_cfg.get("hub_direct_execution") if isinstance(agent_cfg.get("hub_direct_execution"), dict) else {}
        if not bool(cfg.get("enabled", False)) or not bool(cfg.get("direct_before_worker", True)):
            return None

        task = get_local_task_status(request_data.task_id) if request_data.task_id else None
        decision = get_hub_direct_execution_router().classify(prompt, task=task, agent_cfg=agent_cfg)
        audit_enabled = bool(cfg.get("audit_enabled", True))

        if not decision.eligible:
            if audit_enabled and bool(cfg.get("fallback_to_worker", True)):
                audit_hub_direct_event(
                    AUDIT_HUB_DIRECT_FALLBACK_TO_WORKER,
                    task_id=request_data.task_id,
                    reason_code=decision.reason_code,
                )
            record_hub_direct_metric("fallback_to_worker_count", reason_code=decision.reason_code)
            if bool(cfg.get("fallback_to_worker", True)):
                return None
            return self._direct_proposal_response(
                request_data,
                decision=decision,
                direct_result={"kind": "direct_not_eligible", "reason_code": decision.reason_code},
            )

        workspace_ref = self._resolve_direct_workspace(task, agent_cfg)
        if workspace_ref is None:
            record_hub_direct_metric("fallback_to_worker_count", reason_code="no_workspace_ref")
            return None

        if audit_enabled:
            audit_hub_direct_event(
                AUDIT_HUB_DIRECT_CANDIDATE_DETECTED,
                tool_name=decision.tool_name,
                reason_code=decision.reason_code,
                task_id=request_data.task_id,
                confidence=decision.confidence,
            )

        record_hub_direct_metric("direct_execution_count", tool_name=decision.tool_name, reason_code=decision.reason_code)
        direct_result = get_hub_tool_execution_adapter().execute_direct(
            tool_name=decision.tool_name or "",
            arguments=decision.arguments,
            agent_cfg=agent_cfg,
            task=task,
            task_id=request_data.task_id,
            workspace_ref=workspace_ref,
            reason_code=decision.reason_code,
        )

        kind = str(direct_result.get("kind") or "")
        if kind == "direct_tool_result" and str((direct_result.get("tool_result") or {}).get("status")) == "ok":
            record_hub_direct_metric("direct_execution_success_count", tool_name=decision.tool_name)
            if decision.source == "dynamic":
                record_hub_direct_metric("custom_tool_reuse_count", tool_name=decision.tool_name)
        elif kind == "direct_policy_blocked":
            record_hub_direct_metric(
                "direct_execution_blocked_count",
                tool_name=decision.tool_name,
                reason_code=str((direct_result.get("policy_decision") or {}).get("reason") or ""),
            )
        elif kind == "direct_tool_result":
            # Recoverable tool failure: the worker may still take over.
            if bool(cfg.get("fallback_to_worker", True)):
                record_hub_direct_metric("fallback_to_worker_count", tool_name=decision.tool_name, reason_code="direct_tool_failed")
                if audit_enabled:
                    audit_hub_direct_event(
                        AUDIT_HUB_DIRECT_FALLBACK_TO_WORKER,
                        tool_name=decision.tool_name,
                        task_id=request_data.task_id,
                        reason_code="direct_tool_failed",
                    )
                return None
        record_hub_direct_metric("avoided_llm_call_count", tool_name=decision.tool_name)
        from agent.services.task_execution_metrics import record_hub_direct_decision

        record_hub_direct_decision(
            {
                "tool_name": decision.tool_name,
                "reason_code": decision.reason_code,
                "kind": kind,
                "task_id": request_data.task_id,
                "status": str((direct_result.get("tool_result") or {}).get("status") or ""),
                "source": decision.source,
            }
        )
        return self._direct_proposal_response(request_data, decision=decision, direct_result=direct_result)

    def _direct_proposal_response(self, request_data, *, decision, direct_result: dict) -> dict:
        """TaskStepProposeResponse-compatible payload without any LLM cost."""
        reason = f"hub_direct_execution:{decision.tool_name or decision.reason_code}"
        if request_data.task_id:
            get_task_runtime_service().update_local_task_status(
                request_data.task_id,
                "proposing",
                last_proposal={"reason": reason, "direct_execution_kind": str(direct_result.get("kind") or "")},
            )
        response = TaskStepProposeResponse(reason=reason, command=None, tool_calls=None, raw="").model_dump()
        response["direct_execution"] = {**direct_result, "decision": decision.as_dict()}
        response["cost_summary"] = {
            "provider": None,
            "model": None,
            "task_kind": getattr(request_data, "task_kind", None),
            "tokens_total": 0,
            "cost_units": 0.0,
            "latency_ms": None,
            "pricing_source": "hub_direct_execution",
        }
        return response

    @staticmethod
    def _resolve_direct_workspace(task: dict | None, agent_cfg: dict) -> str | None:
        """Explicit workspace only (HDW-004): task workspace or configured root."""
        candidate = str((task or {}).get("workspace_dir") or "").strip()
        if not candidate:
            runtime_cfg = agent_cfg.get("worker_runtime") if isinstance(agent_cfg.get("worker_runtime"), dict) else {}
            candidate = str(runtime_cfg.get("workspace_root") or "").strip()
        return candidate or None

    def execute_direct_decision(
        self,
        decision,
        *,
        agent_cfg: dict,
        task_id: str | None = None,
        workspace_ref: str | None = None,
    ) -> dict:
        """HDE-005: execute a DirectExecutionDecision without synthesizing
        a command string — the decision carries tool name and arguments."""
        from agent.services.hub_tool_execution_adapter import get_hub_tool_execution_adapter

        task = get_local_task_status(task_id) if task_id else None
        resolved_workspace = workspace_ref or self._resolve_direct_workspace(task, agent_cfg)
        return get_hub_tool_execution_adapter().execute_direct(
            tool_name=decision.tool_name or "",
            arguments=decision.arguments,
            agent_cfg=agent_cfg,
            task=task,
            task_id=task_id,
            workspace_ref=resolved_workspace,
            reason_code=decision.reason_code,
        )

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

        _preflight_result = run_preflight_guards(
            tid=tid, command=command, tool_calls=tool_calls,
            effective_task=effective_task, guard_cfg=guard_cfg,
            pipeline=pipeline, _command_analysis=_command_analysis,
            _chain_preflight_deferred=_chain_preflight_deferred,
            approval_policy_service=get_approval_policy_service(),
        )
        if isinstance(_preflight_result, LocalExecutionResult):
            return _preflight_result
        approval_payload = _preflight_result

        _tc_result = execute_tool_calls(
            tool_calls=tool_calls, effective_task=effective_task,
            tid=tid, guard_cfg=guard_cfg, command=command,
            loop_trace_id=loop_trace_id, approval_payload=approval_payload,
            pipeline=pipeline,
            compaction_svc=self._get_compaction_svc(guard_cfg),
        )
        if isinstance(_tc_result, LocalExecutionResult):
            return _tc_result
        output_parts, loop_signals, overall_exit_code = _tc_result

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
