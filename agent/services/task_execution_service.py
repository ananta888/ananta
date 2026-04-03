from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass

from flask import current_app

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
from agent.services.task_execution_policy_service import (
    classify_execution_failure,
    compute_execution_retry_delay,
    resolve_task_scope_allowed_tools,
    resolve_execution_policy,
    should_retry_execution,
    validate_task_scoped_tool_calls,
)
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.task_runtime_service import get_local_task_status, get_task_runtime_service
from agent.shell import get_shell
from agent.tool_guardrails import ToolGuardrailDecision, estimate_text_tokens, estimate_tool_calls_tokens, evaluate_tool_call_guardrails
from agent.tools import registry as tool_registry
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry


@dataclass(frozen=True)
class LocalExecutionResult:
    output: str
    exit_code: int | None
    retries_used: int
    failure_type: str
    retry_history: list[dict]
    status: str


class TaskExecutionService:
    """Encapsulates direct proposal/execution route behavior away from Flask handlers."""

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
        pipeline: dict | None = None,
        exec_started_at: float | None = None,
    ) -> LocalExecutionResult:
        output_parts: list[str] = []
        overall_exit_code = 0
        retries_used = 0
        failure_type = "success"
        retry_history: list[dict] = []
        effective_task = task or {}

        if tool_calls:
            allowed_tools = resolve_task_scope_allowed_tools(effective_task)
            known_tools = [definition.get("name") for definition in tool_registry.get_tool_definitions()]
            blocked_tools, blocked_reasons_by_tool = validate_task_scoped_tool_calls(
                tool_calls,
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
                        "tool_call_count": len(tool_calls or []),
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
                        tool_calls,
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
                "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
            }
            token_usage["estimated_total_tokens"] = sum(int(token_usage.get(key) or 0) for key in token_usage)
            decision = evaluate_tool_call_guardrails(tool_calls, guard_cfg, token_usage=token_usage)
            if pipeline is not None:
                append_stage(
                    pipeline,
                    name="guardrails",
                    status="ok" if decision.allowed else "blocked",
                    metadata={"tool_call_count": len(tool_calls or []), "blocked_tools": decision.blocked_tools},
                )
            if not decision.allowed:
                if tid:
                    self._append_guardrail_block_history(tid, effective_task, command, tool_calls, decision)
                raise ToolGuardrailError(
                    details={
                        "blocked_tools": decision.blocked_tools,
                        "blocked_reasons": decision.reasons,
                        "guardrails": decision.details,
                    }
                )
            for tool_call in tool_calls:
                name = tool_call.get("name")
                args = tool_call.get("args", {})
                current_app.logger.info("Task %s führt Tool aus: %s mit %s", tid or "<direct>", name, args)
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
                    overall_exit_code = 1
                output_parts.append(result_text)

        if command:
            command_output, command_exit_code, retries_used, failure_type, retry_history = self._execute_shell_command_with_policy(
                tid=tid,
                command=command,
                execution_policy=execution_policy,
            )
            output_parts.append(command_output)
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

        final_output = "\n---\n".join(output_parts)
        final_exit_code = overall_exit_code
        effective_failure_type = failure_type if command else ("success" if final_exit_code == 0 else "tool_failure")
        return LocalExecutionResult(
            output=final_output,
            exit_code=final_exit_code,
            retries_used=retries_used if command else 0,
            failure_type=effective_failure_type,
            retry_history=retry_history if command else [],
            status="completed" if final_exit_code == 0 else "failed",
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
            reason=reason,
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
    ) -> None:
        history = list((task or {}).get("history") or [])
        history.append(
            {
                "event_type": "tool_guardrail_blocked",
                "reason": reason,
                "command": command,
                "tool_calls": tool_calls or [],
                "blocked_tools": decision.blocked_tools,
                "blocked_reasons": decision.reasons,
                "guardrails": decision.details,
                "timestamp": time.time(),
            }
        )
        get_task_runtime_service().update_local_task_status(
            tid,
            "failed",
            history=history,
            last_output=f"[tool_guardrail] blocked: {', '.join(decision.reasons)}",
            last_exit_code=1,
        )

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

    def _execute_shell_command_with_policy(
        self,
        *,
        tid: str | None,
        command: str,
        execution_policy: TaskExecutionPolicyContract,
    ) -> tuple[str, int | None, int, str, list[dict]]:
        shell = get_shell()
        retries_used = 0
        attempt = 0
        latest_output = ""
        latest_exit_code: int | None = -1
        failure_type = "success"
        retry_history: list[dict] = []

        while True:
            attempt += 1
            latest_output, latest_exit_code = shell.execute(command, timeout=execution_policy.timeout_seconds)
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


task_execution_service = TaskExecutionService()


def get_task_execution_service() -> TaskExecutionService:
    return task_execution_service
