from __future__ import annotations

import concurrent.futures
import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from flask import current_app, has_app_context

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import TaskConflictError, TaskNotFoundError, WorkerForwardingError
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS, resolve_codex_runtime_config
from agent.common.utils.structured_action_utils import (
    extract_structured_action_fields,
    locally_repair_structured_action_output,
    normalize_structured_action_payload,
    parse_structured_action_payload,
    sanitize_structured_output_text,
)
from agent.config import settings
from agent.model_selection import normalize_legacy_model_name
from agent.models import TaskStepExecuteRequest
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend, normalize_research_artifact
from agent.routes.tasks.orchestration_policy import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import (
    build_trace_record,
    normalize_task_kind,
    resolve_cli_backend,
    review_policy,
    runtime_routing_config,
)
from agent.security_risk import (
    classify_command_risk,
    classify_tool_calls_risk,
    has_file_access_signal,
    has_terminal_signal,
    max_risk_level,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.context_manager_service import get_context_manager_service
from agent.services.live_terminal_session_service import get_live_terminal_session_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.bridge_adapter_registry import BridgeAdapterRegistry
from agent.services.capability_registry import CapabilityRegistry
from agent.services.domain_action_router import DomainActionRouter
from agent.services.domain_policy_loader import DomainPolicyLoader
from agent.services.domain_policy_service import DomainPolicyService
from agent.services.domain_registry import DomainRegistry
from agent.services.repository_registry import get_repository_registry
from agent.services.research_context_bridge_service import get_research_context_bridge_service
from agent.services.service_registry import get_core_services
from agent.services.task_execution_policy_service import normalize_allowed_tools, resolve_execution_policy
from agent.services.task_handler_registry import get_task_handler_registry
from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
from agent.services.task_template_resolution import resolve_task_role_template
from agent.services.verification_service import get_verification_service
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.utils import _extract_reason, _log_terminal_entry

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


@dataclass(frozen=True)
class TaskScopedRouteResponse:
    data: dict
    status: str = "success"
    message: str | None = None
    code: int = 200


class TaskScopedExecutionService:
    """Owns task-scoped proposal/execution orchestration so routes stay thin."""

    @staticmethod
    def _is_interactive_terminal_session(session_payload: dict | None) -> bool:
        metadata = (session_payload or {}).get("metadata") if isinstance((session_payload or {}).get("metadata"), dict) else {}
        return str(metadata.get("opencode_execution_mode") or "").strip().lower() == "interactive_terminal"

    @staticmethod
    def _normalize_temperature(value: float | int | str | None) -> float | None:
        if value is None:
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if normalized < 0.0:
            normalized = 0.0
        if normalized > 2.0:
            normalized = 2.0
        return normalized

    @staticmethod
    def _default_model(agent_cfg: dict) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        return normalize_legacy_model_name(
            str(agent_cfg.get("default_model") or agent_cfg.get("model") or "").strip() or None,
            provider=provider,
        )

    @classmethod
    def _resolve_requested_model(cls, *, agent_cfg: dict, requested_model: str | None) -> str | None:
        provider = str(agent_cfg.get("default_provider") or agent_cfg.get("provider") or "").strip().lower() or None
        resolved = str(requested_model or "").strip() or cls._default_model(agent_cfg)
        return normalize_legacy_model_name(resolved, provider=provider)

    @staticmethod
    def _resolve_task_propose_timeout(agent_cfg: dict, task_kind: str) -> int:
        task_kind_policies = agent_cfg.get("task_kind_execution_policies") if isinstance(agent_cfg.get("task_kind_execution_policies"), dict) else {}
        task_kind_cfg = task_kind_policies.get(task_kind) if isinstance(task_kind_policies.get(task_kind), dict) else {}
        general_timeout = int(agent_cfg.get("command_timeout", 60) or 60)
        kind_timeout = int(task_kind_cfg.get("command_timeout") or 0)
        proposal_timeout = int(agent_cfg.get("task_propose_timeout_seconds") or 0)
        return max(60, general_timeout, kind_timeout, proposal_timeout)

    @staticmethod
    def _invoke_cli_runner(cli_runner: Callable, **cli_kwargs):
        try:
            return cli_runner(**cli_kwargs)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            signature_target = cli_runner
            side_effect = getattr(cli_runner, "side_effect", None)
            if callable(side_effect):
                signature_target = side_effect
            signature = inspect.signature(signature_target)
            if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
                raise
            filtered_kwargs = {key: value for key, value in cli_kwargs.items() if key in signature.parameters}
            return cli_runner(**filtered_kwargs)

    def propose_task_step(
        self,
        tid: str,
        request_data,
        *,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/propose",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=self._persist_forwarded_proposal,
        )
        if forwarded is not None:
            return forwarded

        cfg = current_app.config["AGENT_CONFIG"]
        base_prompt = request_data.prompt or task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"
        explicit_task_kind = str(task.get("task_kind") or "").strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(None, base_prompt)
        research_context_summary = get_research_context_bridge_service().build_context(
            task=task,
            research_context=getattr(request_data, "research_context", None),
            query=base_prompt,
        )
        handler_response = self._try_handler_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            base_prompt=base_prompt,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
        )
        if handler_response is not None:
            return handler_response

        if request_data.providers:
            prompt, worker_context_meta = self._build_task_propose_prompt(
                tid=tid,
                task=task,
                base_prompt=base_prompt,
                tool_definitions_resolver=tool_definitions_resolver,
                research_context=research_context_summary,
            )
            return self._propose_task_with_comparisons(
                tid=tid,
                task=task,
                request_data=request_data,
                prompt=prompt,
                base_prompt=base_prompt,
                worker_context_meta=worker_context_meta,
                research_context=research_context_summary,
                cli_runner=cli_runner,
                cfg=cfg,
            )

        return self._propose_single_task_step(
            tid=tid,
            task=task,
            request_data=request_data,
            base_prompt=base_prompt,
            research_context=research_context_summary,
            cli_runner=cli_runner,
            cfg=cfg,
            tool_definitions_resolver=tool_definitions_resolver,
        )

    def execute_task_step(
        self,
        tid: str,
        request_data,
        *,
        forwarder: Callable,
        cli_runner: Callable | None = None,
        tool_definitions_resolver: Callable | None = None,
    ) -> TaskScopedRouteResponse:
        task = self._require_task(tid)
        forwarded = self._forward_task_request_if_remote(
            tid=tid,
            task=task,
            endpoint=f"/tasks/{tid}/step/execute",
            payload=request_data.model_dump(),
            forwarder=forwarder,
            on_success=lambda response, loaded_task: self._persist_forwarded_execution(
                tid=tid,
                response=response,
                task=loaded_task,
                request_data=request_data,
            ),
        )
        if forwarded is not None:
            return forwarded

        explicit_task_kind = str(
            getattr(request_data, "task_kind", None)
            or ((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind")
            or task.get("task_kind")
            or ""
        ).strip().lower()
        task_kind = explicit_task_kind or normalize_task_kind(
            None,
            request_data.command or task.get("description") or task.get("prompt") or "",
        )
        handler_response = self._try_handler_execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            forwarder=forwarder,
        )
        if handler_response is not None:
            return handler_response

        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        execution_policy = get_core_services().task_execution_service.resolve_policy(
            request_data,
            agent_cfg=agent_cfg,
            source="task_execute",
        )

        command = request_data.command
        tool_calls = request_data.tool_calls
        reason = "Direkte Ausführung"
        used_last_proposal = False

        if not command and not tool_calls:
            proposal = task.get("last_proposal")
            if not proposal:
                raise TaskConflictError("no_proposal")
            research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
            if isinstance(research_artifact, dict):
                return self._execute_research_artifact(
                    tid=tid,
                    task=task,
                    proposal=proposal,
                    research_artifact=research_artifact,
                    execution_policy=execution_policy,
                )
            command = proposal.get("command")
            tool_calls = proposal.get("tool_calls")
            reason = proposal.get("reason", "Vorschlag ausgeführt")
            used_last_proposal = True

        if task_kind == "domain_action":
            return self._execute_domain_action(
                tid=tid,
                task=task,
                task_kind=task_kind,
                request_data=request_data,
                command=command,
                reason=reason,
                execution_policy=execution_policy,
            )

        if command == _INTERACTIVE_TERMINAL_FINALIZE_COMMAND:
            return self._finalize_interactive_terminal_execution(
                tid=tid,
                task=task,
                reason=reason,
                execution_policy=execution_policy,
            )

        exec_started_at = time.time()
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        before_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((task.get("last_proposal", {}) or {}).get("routing") or {}).get("task_kind"),
            policy_version=((task.get("last_proposal", {}) or {}).get("trace") or {}).get("policy_version"),
            metadata={"task_id": tid},
        )
        execution_run = get_core_services().task_execution_service.execute_local_step(
            tid=tid,
            task=task,
            command=command,
            tool_calls=tool_calls,
            execution_policy=execution_policy,
            guard_cfg=agent_cfg,
            working_directory=str(workspace_ctx.workspace_dir),
            pipeline=pipeline,
            exec_started_at=exec_started_at,
        )
        execution_repair_meta: dict | None = None
        if used_last_proposal and cli_runner and self._is_shell_meta_blocked_failure(execution_run.output, execution_run.failure_type):
            repaired_execution = self._attempt_repaired_execute_after_meta_block(
                tid=tid,
                task=task,
                task_kind=task_kind,
                command=command,
                execution_output=execution_run.output,
                execution_policy=execution_policy,
                agent_cfg=agent_cfg,
                cli_runner=cli_runner,
                tool_definitions_resolver=tool_definitions_resolver,
                pipeline=pipeline,
                workspace_dir=str(workspace_ctx.workspace_dir),
                exec_started_at=exec_started_at,
            )
            if repaired_execution:
                command = repaired_execution["command"]
                tool_calls = repaired_execution["tool_calls"]
                reason = repaired_execution["reason"]
                execution_run = repaired_execution["execution_run"]
                execution_repair_meta = repaired_execution["repair_meta"]
        after_workspace_snapshot = get_worker_workspace_service().snapshot_directory(workspace_ctx.workspace_dir)
        changed_files = get_worker_workspace_service().detect_changed_files(before_workspace_snapshot, after_workspace_snapshot)
        workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        execution_duration_ms = int((time.time() - exec_started_at) * 1000)
        proposal_meta = task.get("last_proposal", {}) or {}
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            backend=proposal_meta.get("backend"),
            requested_backend=proposal_meta.get("backend"),
            routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={
                "retries_used": execution_run.retries_used,
                "duration_ms": execution_duration_ms,
                "failure_type": execution_run.failure_type,
            },
        )
        if execution_run.status == "completed":
            from agent.metrics import TASK_COMPLETED

            TASK_COMPLETED.inc()
        else:
            from agent.metrics import TASK_FAILED

            TASK_FAILED.inc()

        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=execution_run.status,
            reason=reason,
            command=command,
            tool_calls=tool_calls,
            output=execution_run.output,
            exit_code=execution_run.exit_code,
            retries_used=execution_run.retries_used,
            retry_history=execution_run.retry_history,
            failure_type=execution_run.failure_type,
            execution_duration_ms=execution_duration_ms,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            artifact_refs=workspace_artifact_refs or None,
            extra_history={
                "workspace_changed_files": changed_files,
                "workspace_dir": str(workspace_ctx.workspace_dir),
                "workspace_artifact_count": len(workspace_artifact_refs),
                "loop_signals": execution_run.loop_signals,
                "loop_detection": execution_run.loop_detection,
                "approval_decision": execution_run.approval_decision,
                "execution_repair": execution_repair_meta,
            },
        )

        history_len = len(task.get("history", []) or [])
        _log_terminal_entry(current_app.config["AGENT_NAME"], history_len, "out", command=command, task_id=tid)
        _log_terminal_entry(
            current_app.config["AGENT_NAME"],
            history_len,
            "in",
            output=execution_run.output,
            exit_code=execution_run.exit_code,
            task_id=tid,
        )
        return TaskScopedRouteResponse(data=response_payload)

    @staticmethod
    def _build_domain_action_router() -> DomainActionRouter:
        domain_registry = DomainRegistry()
        descriptors = domain_registry.load()
        capability_registry = CapabilityRegistry()
        capability_registry.load_from_descriptors(descriptors)
        policy_loader = DomainPolicyLoader(capability_registry=capability_registry)
        policy_service = DomainPolicyService(capability_registry=capability_registry)
        bridge_adapter_registry = BridgeAdapterRegistry()
        bridge_adapter_registry.load_from_descriptors(descriptors)
        return DomainActionRouter(
            domain_registry=domain_registry,
            capability_registry=capability_registry,
            policy_loader=policy_loader,
            policy_service=policy_service,
            bridge_adapter_registry=bridge_adapter_registry,
        )

    @staticmethod
    def _resolve_domain_action_payload(*, task: dict, command: str | None) -> dict:
        command_text = str(command or "").strip()
        inline_payload = None
        if command_text:
            try:
                parsed = json.loads(command_text)
            except json.JSONDecodeError as exc:
                raise TaskConflictError(
                    "domain_action_payload_invalid",
                    details={"reason": "command_must_be_valid_json_object", "error": str(exc)},
                )
            if not isinstance(parsed, dict):
                raise TaskConflictError(
                    "domain_action_payload_invalid",
                    details={"reason": "command_must_be_json_object"},
                )
            inline_payload = dict(parsed)
        payload = inline_payload or dict(task.get("domain_action_request") or {})
        if not payload:
            raise TaskConflictError(
                "domain_action_payload_missing",
                details={"reason": "provide_json_command_or_domain_action_request"},
            )
        required = ("domain_id", "capability_id", "action_id")
        missing = [key for key in required if not str(payload.get(key) or "").strip()]
        if missing:
            raise TaskConflictError(
                "domain_action_payload_invalid",
                details={"reason": "missing_required_fields", "fields": missing},
            )
        return payload

    def _execute_domain_action(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        command: str | None,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        payload = self._resolve_domain_action_payload(task=task, command=command)
        route_result = self._build_domain_action_router().route(
            domain_id=str(payload.get("domain_id") or "").strip(),
            capability_id=str(payload.get("capability_id") or "").strip(),
            action_id=str(payload.get("action_id") or "").strip(),
            execution_mode=str(payload.get("execution_mode") or "execute").strip() or "execute",
            context_summary=dict(payload.get("context_summary") or {}),
            actor_metadata=dict(payload.get("actor_metadata") or {}),
            approval=dict(payload.get("approval") or {}) if isinstance(payload.get("approval"), dict) else None,
        )
        route = route_result.as_dict()

        state = str(route.get("state") or "").strip().lower()
        if state in {"plan", "execution_started"}:
            status = "completed"
            exit_code = 0
            failure_type = "success"
        elif state == "approval_required":
            status = "blocked"
            exit_code = 1
            failure_type = "approval_required"
        elif state == "denied":
            status = "failed"
            exit_code = 1
            failure_type = "policy_denied"
        else:
            status = "failed"
            exit_code = 1
            failure_type = "degraded"

        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=task_kind,
            policy_version="domain_action_router_v1",
            metadata={
                "task_id": tid,
                "domain_id": route.get("domain_id"),
                "capability_id": route.get("capability_id"),
                "action_id": route.get("action_id"),
            },
        )
        append_stage(
            pipeline,
            name="domain_action_route",
            status="ok" if status == "completed" else "failed",
            metadata={"route_state": state, "route_reason": route.get("reason")},
        )
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=task_kind,
            backend="domain_action_router",
            requested_backend="domain_action_router",
            routing_reason="domain_action_router",
            policy_version="domain_action_router_v1",
            metadata={
                "source": "domain_action_execute",
                "domain_action_route": route,
            },
        )
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=status,
            reason=reason or "Domain action routed",
            command=command,
            tool_calls=request_data.tool_calls if isinstance(getattr(request_data, "tool_calls", None), list) else None,
            output=json.dumps(route, ensure_ascii=False),
            exit_code=exit_code,
            retries_used=0,
            retry_history=[],
            failure_type=failure_type,
            execution_duration_ms=0,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            extra_history={
                "domain_action_route": route,
                "domain_action_payload": payload,
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    def _propose_task_with_comparisons(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        prompt: str,
        base_prompt: str,
        worker_context_meta: dict,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        compare_policy = resolve_execution_policy(
            TaskStepExecuteRequest(timeout=timeout),
            agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
            source="task_propose_compare",
        )
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]

        def _run_single_provider(provider_entry: str) -> tuple[str, dict]:
            entry = str(provider_entry or "").strip()
            if not entry:
                return provider_entry, {"error": "invalid_provider_entry"}

            parts = entry.split(":", 1)
            requested_backend = str(parts[0] or "").strip().lower()
            selected_model = self._resolve_requested_model(
                agent_cfg=cfg,
                requested_model=(parts[1].strip() if len(parts) > 1 else "") or request_data.model,
            )
            if requested_backend not in SUPPORTED_CLI_BACKENDS:
                return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

            effective_backend, routing_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend=requested_backend,
                agent_cfg=cfg,
                required_capabilities=derive_required_capabilities(task, task_kind),
            )
            started_at = time.time()
            cli_kwargs = {
                "prompt": prompt,
                "options": ["--no-interaction"],
                "timeout": compare_policy.timeout_seconds,
                "backend": effective_backend,
                "model": selected_model,
                "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_policy_version},
                "workdir": str(workspace_context.workspace_dir),
            }
            if requested_temperature is not None:
                cli_kwargs["temperature"] = requested_temperature
            if research_context:
                cli_kwargs["research_context"] = research_context
            rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
            latency_ms = int((time.time() - started_at) * 1000)
            raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
            required_capabilities = derive_required_capabilities(task, task_kind)
            routing_dimensions = self._routing_dimensions(
                backend_used=backend_used,
                model=selected_model,
                temperature=requested_temperature,
                requested_backend=requested_backend,
                agent_cfg=cfg,
            )
            routing = {
                "task_kind": task_kind,
                "effective_backend": effective_backend,
                "reason": routing_reason,
                "required_capabilities": required_capabilities,
                "research_specialization": derive_research_specialization(task, task_kind, required_capabilities),
                **routing_dimensions,
            }
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
            }
            if rc != 0 and not raw_res.strip():
                return entry, {"error": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if not raw_res:
                return entry, {"error": "empty_response", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if is_research_backend(backend_used):
                research_res = self._build_research_result(
                    raw_res,
                    backend_used,
                    tid,
                    rc,
                    cli_err,
                    latency_ms,
                    output_source=output_source,
                    research_context=research_context,
                )
                research_res["model"] = selected_model
                research_res["routing"] = routing
                return entry, research_res
            command, tool_calls = self._extract_structured_action_fields(raw_res)
            return entry, {
                "reason": _extract_reason(raw_res),
                "command": command,
                "tool_calls": tool_calls,
                "raw": raw_res,
                "backend": backend_used,
                "model": selected_model,
                "routing": routing,
                "cli_result": cli_result,
            }

        results: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(request_data.providers))) as executor:
            futures = {executor.submit(_run_single_provider, provider_name): provider_name for provider_name in request_data.providers}
            for future in concurrent.futures.as_completed(futures):
                requested = futures[future]
                try:
                    provider_key, provider_result = future.result()
                    results[provider_key or requested] = provider_result
                except Exception as exc:
                    current_app.logger.error("Multi-Provider CLI Call for %s failed: %s", requested, exc)
                    results[requested] = {"error": str(exc)}

        successful_results = [
            results.get(provider_name)
            for provider_name in request_data.providers
            if isinstance(results.get(provider_name), dict) and not results.get(provider_name).get("error")
        ]
        if not successful_results:
            return TaskScopedRouteResponse(
                status="error",
                message="all_llm_failed",
                data={"comparisons": results},
                code=502,
            )

        main_res = results.get(request_data.providers[0])
        if not isinstance(main_res, dict) or main_res.get("error"):
            main_res = successful_results[0]

        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=(main_res.get("routing") or {}).get("task_kind"),
            backend=main_res.get("backend"),
            requested_backend=request_data.providers[0] if request_data.providers else "auto",
            routing_reason=((main_res.get("routing") or {}).get("reason")),
            policy_version=routing_policy_version,
            metadata={**worker_context_meta, "source": "task_propose_multi", "comparison_count": len(results)},
        )
        review = self._build_review_state(
            current_app.config.get("AGENT_CONFIG", {}) or {},
            backend=str(main_res.get("backend") or ""),
            task_kind=str(((main_res.get("routing") or {}).get("task_kind") or "")),
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
        )
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=main_res.get("reason"),
            raw=main_res.get("raw"),
            backend=main_res.get("backend"),
            model=main_res.get("model"),
            routing=main_res.get("routing"),
            cli_result=main_res.get("cli_result"),
            worker_context=worker_context_meta,
            trace=trace,
            review=review,
            comparisons=results,
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
            research_artifact=main_res.get("research_artifact"),
            research_context=main_res.get("research_context"),
            history_event={
                "event_type": "proposal_result",
                "reason": main_res.get("reason"),
                "backend": main_res.get("backend"),
                "routing_reason": ((main_res.get("routing") or {}).get("reason")),
                "latency_ms": int((main_res.get("cli_result") or {}).get("latency_ms") or 0),
                "returncode": int((main_res.get("cli_result") or {}).get("returncode") or 0),
                "comparison_count": len(results),
                "pipeline": None,
                "trace": trace,
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    def _propose_single_task_step(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        base_prompt: str,
        research_context: dict | None,
        cli_runner: Callable,
        cfg: dict,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        required_capabilities = derive_required_capabilities(task, task_kind)
        research_specialization = derive_research_specialization(task, task_kind, required_capabilities)
        effective_backend, routing_reason = self._resolve_cli_backend(
            task_kind,
            requested_backend="auto",
            required_capabilities=required_capabilities,
        )
        timeout = self._resolve_task_propose_timeout(cfg, task_kind)
        proposal_model = self._resolve_requested_model(agent_cfg=cfg, requested_model=request_data.model)
        policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
        session_payload = self._prepare_task_cli_session(
            tid=tid,
            task=task,
            backend=effective_backend,
            model=proposal_model,
            agent_cfg=cfg,
        )
        interactive_terminal_session = effective_backend == "opencode" and self._is_interactive_terminal_session(session_payload)
        prompt_for_cli, worker_context_meta = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=(lambda *_args, **_kwargs: [])
            if interactive_terminal_session
            else tool_definitions_resolver,
            research_context=research_context,
            interactive_terminal=interactive_terminal_session,
        )
        pipeline = new_pipeline_trace(
            pipeline="task_propose",
            task_kind=task_kind,
            policy_version=policy_version,
            metadata={"task_id": tid, "requested_backend": "auto", **worker_context_meta},
        )
        append_stage(
            pipeline,
            name="route",
            status="ok",
            metadata={"effective_backend": effective_backend, "reason": routing_reason},
        )
        requested_temperature = self._normalize_temperature(getattr(request_data, "temperature", None))
        if requested_temperature is not None:
            prompt_for_cli = (
                f"{prompt_for_cli}\n\n"
                f"[Sampling-Hinweis]\n"
                f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                + (
                    "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                    if interactive_terminal_session
                    else "Behalte strikt das JSON-Output-Schema ein."
                )
            )
        if session_payload and not self._has_native_opencode_runtime(session_payload) and not interactive_terminal_session:
            prompt_for_cli = (
                get_cli_session_service().build_prompt_with_history(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    max_turns=int(session_payload.get("max_turns_per_session") or 40),
                )
                or prompt_for_cli
            )
        started_at = time.time()
        cli_kwargs = {
            "prompt": prompt_for_cli,
            "options": ["--no-interaction"],
            "timeout": timeout,
            "backend": effective_backend,
            "model": proposal_model,
            "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
            "session": session_payload,
            "workdir": str(workspace_context.workspace_dir),
        }
        if requested_temperature is not None:
            cli_kwargs["temperature"] = requested_temperature
        if research_context:
            cli_kwargs["research_context"] = research_context
        rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
        latency_ms = int((time.time() - started_at) * 1000)
        raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
        repair_meta = {"attempted": False, "backend": None, "model": None}
        append_stage(
            pipeline,
            name="execute",
            status="ok" if rc == 0 or bool(raw_res) else "error",
            metadata={
                "backend_used": backend_used,
                "returncode": rc,
                "latency_ms": latency_ms,
                "output_source": output_source,
            },
            started_at=started_at,
        )
        if not interactive_terminal_session and rc != 0 and not raw_res.strip():
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_or_failed_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(
                    status="error",
                    message="llm_cli_failed",
                    data={"details": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used},
                    code=502,
                )
        if not interactive_terminal_session and not raw_res:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=(cli_err or ""),
                validation_error="empty_cli_response",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
            else:
                return TaskScopedRouteResponse(status="error", message="llm_failed", data={}, code=502)

        routing = {
            "task_kind": task_kind,
            "effective_backend": effective_backend,
            "reason": routing_reason,
            "required_capabilities": required_capabilities,
            "research_specialization": research_specialization,
            **self._routing_dimensions(
                backend_used=backend_used,
                model=proposal_model,
                temperature=requested_temperature,
                requested_backend="auto",
                agent_cfg=cfg,
            ),
        }
        if session_payload:
            routing["session_mode"] = "stateful"
            routing["session_id"] = session_payload["id"]
            routing["session_reused"] = bool(session_payload.get("session_reused"))
            session_metadata = session_payload.get("metadata") if isinstance(session_payload.get("metadata"), dict) else {}
            live_terminal_meta = (
                dict(session_metadata.get("opencode_live_terminal") or {})
                if isinstance(session_metadata.get("opencode_live_terminal"), dict)
                else {}
            )
            config_execution_mode = self._resolve_opencode_execution_mode(cfg)
            if (
                str(session_metadata.get("opencode_execution_mode") or "").strip().lower() in {"live_terminal", "interactive_terminal"}
                or (effective_backend == "opencode" and config_execution_mode in {"live_terminal", "interactive_terminal"})
            ):
                routing["execution_mode"] = str(session_metadata.get("opencode_execution_mode") or config_execution_mode).strip().lower()
                if not live_terminal_meta:
                    live_terminal_meta = (
                        dict((task.get("verification_status") or {}).get("opencode_live_terminal") or {})
                        if isinstance((task.get("verification_status") or {}).get("opencode_live_terminal"), dict)
                        else {}
                    )
                routing["live_terminal"] = live_terminal_meta
        if is_research_backend(backend_used):
            research_res = self._build_research_result(
                raw_res,
                backend_used,
                tid,
                rc,
                cli_err,
                latency_ms,
                output_source=output_source,
                research_context=research_context,
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "artifact_kind": "research_report"},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=research_res.get("reason"),
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=research_res.get("cli_result"),
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=None,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                research_artifact=research_res.get("research_artifact"),
                research_context=research_context,
                history_event={
                    "event_type": "proposal_result",
                    "reason": research_res.get("reason"),
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "artifact_kind": "research_report",
                    "source_count": len((research_res.get("research_artifact") or {}).get("sources") or []),
                    "pipeline": pipeline_payload,
                    "trace": trace,
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "research"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            return TaskScopedRouteResponse(data=response_payload)

        if interactive_terminal_session and backend_used == "opencode":
            reason = "Interactive OpenCode session finished; finalize workspace changes"
            append_stage(
                pipeline,
                name="parse",
                status="ok",
                metadata={"interactive_terminal_finalize": True, "has_command": True, "tool_call_count": 0},
            )
            trace = build_trace_record(
                task_id=tid,
                event_type="proposal_result",
                task_kind=task_kind,
                backend=backend_used,
                requested_backend="auto",
                routing_reason=routing_reason,
                policy_version=policy_version,
                metadata={**worker_context_meta, "source": "task_propose", "interactive_terminal": True},
            )
            pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
            cli_result = {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": False,
                "repair_backend": None,
                "repair_model": None,
            }
            response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
                tid=tid,
                task=task,
                reason=reason,
                raw=raw_res,
                backend=backend_used,
                model=proposal_model,
                routing=routing,
                cli_result=cli_result,
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(
                    current_app.config.get("AGENT_CONFIG", {}) or {},
                    backend_used,
                    task_kind,
                    command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                    tool_calls=None,
                ),
                pipeline=pipeline_payload,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                history_event={
                    "event_type": "proposal_result",
                    "reason": reason,
                    "backend": backend_used,
                    "routing_reason": routing_reason,
                    "latency_ms": latency_ms,
                    "returncode": rc,
                    "interactive_terminal": True,
                    "pipeline": pipeline_payload,
                    "trace": trace,
                },
            )
            if session_payload:
                turn = get_cli_session_service().append_turn(
                    session_id=session_payload["id"],
                    prompt=prompt_for_cli,
                    output=raw_res,
                    model=proposal_model,
                    trace_id=str(trace.get("trace_id") or ""),
                    metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "interactive_terminal"},
                )
                if isinstance(turn, dict):
                    response_payload.setdefault("routing", {})
                    response_payload["routing"]["session_turn_id"] = turn.get("id")
            _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
            _log_terminal_entry(
                current_app.config["AGENT_NAME"],
                0,
                "out",
                reason=reason,
                command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
                tool_calls=None,
                task_id=tid,
            )
            return TaskScopedRouteResponse(data=response_payload)

        reason = _extract_reason(raw_res)
        command, tool_calls = self._extract_structured_action_fields(raw_res)
        if not command and not tool_calls:
            repaired = self._repair_task_proposal(
                cli_runner=cli_runner,
                prompt=prompt_for_cli,
                bad_output=raw_res,
                validation_error="missing_required_fields: command_or_tool_calls",
                timeout=timeout,
                task_kind=task_kind,
                policy_version=policy_version,
                cfg=cfg,
                primary_backend=backend_used,
                primary_model=proposal_model,
                primary_temperature=requested_temperature,
                research_context=research_context,
                session=session_payload,
                workdir=str(workspace_context.workspace_dir),
            )
            if repaired:
                raw_res = repaired["raw"]
                output_source = repaired["output_source"]
                backend_used = repaired["backend_used"]
                rc = int(repaired["rc"])
                cli_err = str(repaired.get("stderr") or "")
                reason = _extract_reason(raw_res)
                repair_meta = {
                    "attempted": True,
                    "backend": repaired["backend_used"],
                    "model": repaired.get("model"),
                }
                command, tool_calls = self._extract_structured_action_fields(raw_res)
        append_stage(
            pipeline,
            name="parse",
            status="ok",
            metadata={"has_command": bool(command), "tool_call_count": len(tool_calls or [])},
        )
        trace = build_trace_record(
            task_id=tid,
            event_type="proposal_result",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend="auto",
            routing_reason=routing_reason,
            policy_version=policy_version,
            metadata={**worker_context_meta, "source": "task_propose"},
        )
        pipeline_payload = {**pipeline, "trace_id": trace["trace_id"]}
        response_payload = get_core_services().task_execution_service.persist_task_proposal_result(
            tid=tid,
            task=task,
            reason=reason,
            raw=raw_res,
            backend=backend_used,
            model=proposal_model,
            routing=routing,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
                "repair_attempted": bool(repair_meta["attempted"]),
                "repair_backend": repair_meta["backend"],
                "repair_model": repair_meta["model"],
            },
            worker_context=worker_context_meta,
            trace=trace,
            review=self._build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend_used,
                task_kind,
                command=command,
                tool_calls=tool_calls,
            ),
            pipeline=pipeline_payload,
            command=command,
            tool_calls=tool_calls,
            history_event={
                "event_type": "proposal_result",
                "reason": reason,
                "backend": backend_used,
                "routing_reason": routing_reason,
                "latency_ms": latency_ms,
                "returncode": rc,
                "pipeline": pipeline_payload,
                "trace": trace,
            },
        )
        if session_payload:
            turn = get_cli_session_service().append_turn(
                session_id=session_payload["id"],
                prompt=prompt,
                output=raw_res,
                model=proposal_model,
                trace_id=str(trace.get("trace_id") or ""),
                metadata={"backend_used": backend_used, "task_id": tid, "proposal_mode": "command"},
            )
            if isinstance(turn, dict):
                response_payload.setdefault("routing", {})
                response_payload["routing"]["session_turn_id"] = turn.get("id")
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt_for_cli, task_id=tid)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
        return TaskScopedRouteResponse(data=response_payload)

    def _finalize_interactive_terminal_execution(
        self,
        *,
        tid: str,
        task: dict,
        reason: str,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        workspace_ctx = get_worker_workspace_service().resolve_workspace_context(task=task)
        changed_files = get_worker_workspace_service().detect_changed_files_against_interactive_baseline(
            workspace_dir=workspace_ctx.workspace_dir
        )
        workspace_artifact_refs = get_worker_workspace_service().sync_changed_files_to_artifacts(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        diff_artifact_ref = get_worker_workspace_service().create_workspace_diff_artifact(
            task_id=tid,
            task=task,
            workspace_dir=workspace_ctx.workspace_dir,
            changed_rel_paths=changed_files,
            sync_cfg=workspace_ctx.artifact_sync,
        )
        artifact_refs = list(workspace_artifact_refs or [])
        if diff_artifact_ref:
            artifact_refs.append(diff_artifact_ref)
        proposal_meta = dict(task.get("last_proposal") or {})
        cli_result = proposal_meta.get("cli_result") if isinstance(proposal_meta.get("cli_result"), dict) else {}
        exit_code = int(cli_result.get("returncode") or 0)
        status = "completed" if exit_code == 0 else "failed"
        output_lines = [
            "Interactive OpenCode session finalized.",
            f"Workspace: {workspace_ctx.workspace_dir}",
            f"Changed files: {len(changed_files)}",
        ]
        if changed_files:
            output_lines.extend(f"- {rel}" for rel in changed_files[:50])
        else:
            output_lines.append("No tracked workspace changes detected.")
        if diff_artifact_ref:
            output_lines.append(f"Diff artifact: {diff_artifact_ref.get('artifact_id')}")
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            backend=proposal_meta.get("backend"),
            requested_backend=proposal_meta.get("backend"),
            routing_reason=((proposal_meta.get("routing") or {}).get("reason")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={
                "interactive_terminal_finalize": True,
                "changed_file_count": len(changed_files),
                "workspace_artifact_count": len(artifact_refs),
            },
        )
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal_meta.get("routing") or {}).get("task_kind")),
            policy_version=((proposal_meta.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "interactive_terminal_finalize": True},
        )
        append_stage(
            pipeline,
            name="interactive_terminal_finalize",
            status="ok" if exit_code == 0 else "error",
            metadata={
                "changed_file_count": len(changed_files),
                "workspace_artifact_count": len(artifact_refs),
                "exit_code": exit_code,
            },
        )
        output = "\n".join(output_lines)
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status=status,
            reason=reason or "Interactive OpenCode session finalized",
            command=_INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
            tool_calls=None,
            output=output,
            exit_code=exit_code,
            retries_used=0,
            retry_history=[],
            failure_type="success" if exit_code == 0 else "command_failure",
            execution_duration_ms=int(cli_result.get("latency_ms") or 0),
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            artifact_refs=artifact_refs or None,
            extra_history={
                "workspace_changed_files": changed_files,
                "workspace_dir": str(workspace_ctx.workspace_dir),
                "workspace_artifact_count": len(artifact_refs),
                "interactive_terminal_finalize": True,
            },
        )
        get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=workspace_ctx.workspace_dir)
        return TaskScopedRouteResponse(data=response_payload)

    def _execute_research_artifact(
        self,
        *,
        tid: str,
        task: dict,
        proposal: dict,
        research_artifact: dict,
        execution_policy,
    ) -> TaskScopedRouteResponse:
        review = (proposal.get("review") or {}) if isinstance(proposal, dict) else {}
        if review.get("required") and review.get("status") != "approved":
            raise TaskConflictError("research_review_required", details={"review": review, "task_id": tid})
        verification = self._verify_research_artifact(research_artifact)
        research_artifact["verification"] = verification
        if not verification.get("passed"):
            raise TaskConflictError(
                "research_artifact_verification_failed",
                details={"verification": verification, "task_id": tid},
            )
        output = str(research_artifact.get("report_markdown") or "")
        pipeline = new_pipeline_trace(
            pipeline="task_execute",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"task_id": tid, "artifact_execute": True},
        )
        append_stage(pipeline, name="artifact_finalize", status="ok", metadata={"artifact_kind": research_artifact.get("kind")})
        trace = build_trace_record(
            task_id=tid,
            event_type="execution_result",
            task_kind=((proposal.get("routing") or {}).get("task_kind")),
            backend=proposal.get("backend"),
            requested_backend=proposal.get("backend"),
            routing_reason=((proposal.get("routing") or {}).get("reason")),
            policy_version=((proposal.get("trace") or {}).get("policy_version")),
            metadata={"source": "research_artifact_execute", "artifact_kind": research_artifact.get("kind")},
        )
        trace["metadata"]["research_verification"] = verification
        artifact_ref = get_core_services().task_execution_tracking_service.persist_research_artifact(
            tid=tid,
            task=task,
            research_artifact=research_artifact,
        )
        verification_record = get_verification_service().create_or_update_record(
            tid,
            trace_id=trace.get("trace_id"),
            output=output,
            exit_code=0,
            gate_results=verification,
        )
        if isinstance(artifact_ref, dict):
            artifact_ref["verification_record_id"] = getattr(verification_record, "id", None)
            research_artifact.setdefault("trace", {})
            research_artifact["trace"]["persisted_artifact"] = artifact_ref
        from agent.metrics import TASK_COMPLETED

        TASK_COMPLETED.inc()
        response_payload = get_core_services().task_execution_service.finalize_task_execution_response(
            tid=tid,
            task=task,
            status="completed",
            reason=proposal.get("reason", "Research report persisted"),
            command=None,
            tool_calls=None,
            output=output,
            exit_code=0,
            retries_used=0,
            retry_history=[],
            failure_type="success",
            execution_duration_ms=0,
            trace=trace,
            pipeline={**pipeline, "trace_id": trace["trace_id"]},
            execution_policy=execution_policy,
            review=review,
            artifact_refs=[artifact_ref] if artifact_ref else None,
            extra_history={
                "artifact_kind": research_artifact.get("kind"),
                "artifact_ref": artifact_ref,
                "source_count": len(research_artifact.get("sources") or []),
                "verification": verification,
                "verification_record_id": getattr(verification_record, "id", None),
            },
        )
        return TaskScopedRouteResponse(data=response_payload)

    def _forward_task_request_if_remote(
        self,
        *,
        tid: str,
        task: dict,
        endpoint: str,
        payload: dict,
        forwarder: Callable,
        on_success: Callable[[dict, dict], None],
    ) -> TaskScopedRouteResponse | None:
        worker_url = task.get("assigned_agent_url")
        if not worker_url:
            return None
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") == my_url.rstrip("/"):
            return None
        try:
            response = forwarder(worker_url, endpoint, payload, token=task.get("assigned_agent_token"))
            response = unwrap_api_envelope(response)
            if isinstance(response, dict):
                on_success(response, task)
            return TaskScopedRouteResponse(data=response)
        except Exception as exc:
            current_app.logger.error("Forwarding an %s fehlgeschlagen: %s", worker_url, exc)
            raise WorkerForwardingError(details={"details": str(exc), "worker_url": worker_url})

    def _persist_forwarded_proposal(self, response: dict, task: dict) -> None:
        if not isinstance(response, dict):
            return
        has_proposal_payload = any(
            key in response
            for key in (
                "command",
                "tool_calls",
                "reason",
                "raw",
                "routing",
                "cli_result",
                "trace",
                "review",
                "pipeline",
                "research_artifact",
                "research_context",
                "worker_context",
            )
        )
        if not has_proposal_payload:
            return
        get_core_services().task_execution_service.persist_task_proposal_result(
            tid=task["id"],
            task=task,
            reason=str(response.get("reason") or ""),
            raw=str(response.get("raw") or ""),
            backend=(str(response.get("backend") or "").strip() or None),
            model=(str(response.get("model") or "").strip() or None),
            routing=response.get("routing") if isinstance(response.get("routing"), dict) else None,
            cli_result=response.get("cli_result") if isinstance(response.get("cli_result"), dict) else None,
            worker_context=response.get("worker_context") if isinstance(response.get("worker_context"), dict) else None,
            trace=response.get("trace") if isinstance(response.get("trace"), dict) else None,
            review=response.get("review") if isinstance(response.get("review"), dict) else None,
            pipeline=response.get("pipeline") if isinstance(response.get("pipeline"), dict) else None,
            command=(str(response.get("command") or "").strip() or None),
            tool_calls=response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None,
            comparisons=response.get("comparisons") if isinstance(response.get("comparisons"), dict) else None,
            research_artifact=response.get("research_artifact") if isinstance(response.get("research_artifact"), dict) else None,
            research_context=response.get("research_context") if isinstance(response.get("research_context"), dict) else None,
            history_event={
                "event_type": "proposal_result",
                "reason": str(response.get("reason") or ""),
                "backend": response.get("backend"),
                "routing_reason": ((response.get("routing") or {}).get("reason")) if isinstance(response.get("routing"), dict) else None,
                "forwarded": True,
                "timestamp": time.time(),
            },
        )

    def _persist_forwarded_execution(self, *, tid: str, response: dict, task: dict, request_data) -> None:
        if "status" not in response:
            return
        history = task.get("history", [])
        proposal_meta = task.get("last_proposal", {}) or {}
        verification_status = dict(task.get("verification_status") or {})
        execution_scope = response.get("execution_scope") if isinstance(response.get("execution_scope"), dict) else None
        execution_provenance = (
            response.get("execution_provenance") if isinstance(response.get("execution_provenance"), dict) else None
        )
        artifacts = list(response.get("artifacts") or []) if isinstance(response.get("artifacts"), list) else None
        review = response.get("review") if isinstance(response.get("review"), dict) else None
        if execution_scope:
            verification_status["execution_scope"] = dict(execution_scope)
        if execution_provenance:
            verification_status["execution_provenance"] = dict(execution_provenance)
        if artifacts is not None:
            verification_status["execution_artifacts"] = artifacts
        if review:
            verification_status["execution_review"] = dict(review)
        history.append(
            {
                "event_type": "execution_result",
                "prompt": task.get("description"),
                "reason": "Forwarded to " + str(task.get("assigned_agent_url")),
                "command": request_data.command or task.get("last_proposal", {}).get("command"),
                "output": response.get("output"),
                "exit_code": response.get("exit_code"),
                "backend": proposal_meta.get("backend"),
                "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
                "artifacts": artifacts,
                "execution_scope": execution_scope,
                "execution_provenance": execution_provenance,
                "review": review,
                "forwarded": True,
                "timestamp": time.time(),
            }
        )
        update_local_task_status(
            tid,
            response["status"],
            history=history,
            last_output=response.get("output"),
            last_exit_code=response.get("exit_code"),
            verification_status=verification_status,
        )

    def _try_handler_propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        base_prompt: str,
        cli_runner: Callable,
        forwarder: Callable,
        tool_definitions_resolver: Callable,
    ) -> TaskScopedRouteResponse | None:
        registry = get_task_handler_registry()
        handler = registry.resolve(task_kind)
        if handler is None or not hasattr(handler, "propose"):
            return None
        handler_descriptor = registry.resolve_descriptor(task_kind) or {}
        response = handler.propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            base_prompt=base_prompt,
            service=self,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
            handler_descriptor=handler_descriptor,
        )
        coerced = self._coerce_handler_response(response)
        if coerced is None:
            return None
        payload = dict(coerced.data or {})
        payload.setdefault("handler_contract", handler_descriptor or None)
        if bool((handler_descriptor.get("safety_flags") or {}).get("requires_review")) and "review" not in payload:
            base_review = self._build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend="handler",
                task_kind=task_kind,
                command=str(payload.get("command") or "") or None,
                tool_calls=payload.get("tool_calls"),
            )
            payload["review"] = {
                **base_review,
                "required": True,
                "status": "pending",
                "reason": "handler_safety_requires_review",
            }
        return TaskScopedRouteResponse(
            data=payload,
            status=coerced.status,
            message=coerced.message,
            code=coerced.code,
        )

    def _try_handler_execute(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        forwarder: Callable,
    ) -> TaskScopedRouteResponse | None:
        registry = get_task_handler_registry()
        handler = registry.resolve(task_kind)
        if handler is None or not hasattr(handler, "execute"):
            return None
        handler_descriptor = registry.resolve_descriptor(task_kind) or {}
        response = handler.execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            service=self,
            forwarder=forwarder,
            handler_descriptor=handler_descriptor,
        )
        coerced = self._coerce_handler_response(response)
        if coerced is None:
            return None
        payload = dict(coerced.data or {})
        payload.setdefault("handler_contract", handler_descriptor or None)
        return TaskScopedRouteResponse(
            data=payload,
            status=coerced.status,
            message=coerced.message,
            code=coerced.code,
        )

    def _coerce_handler_response(self, response: object | None) -> TaskScopedRouteResponse | None:
        if response is None:
            return None
        if isinstance(response, TaskScopedRouteResponse):
            return response
        if isinstance(response, dict):
            return TaskScopedRouteResponse(data=response)
        raise TypeError("task_handler_response_must_be_dict_or_TaskScopedRouteResponse")

    def _require_task(self, tid: str) -> dict:
        task = get_local_task_status(tid)
        if not task:
            raise TaskNotFoundError()
        return task

    def _resolve_cli_backend(
        self,
        task_kind: str,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
        required_capabilities: list[str] | None = None,
    ) -> tuple[str, str]:
        backend, reason, _ = resolve_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            supported_backends=SUPPORTED_CLI_BACKENDS,
            agent_cfg=agent_cfg if agent_cfg is not None else (current_app.config.get("AGENT_CONFIG", {}) or {}),
            fallback_backend="sgpt",
            required_capabilities=required_capabilities,
        )
        return backend, reason

    @staticmethod
    def _coalesce_cli_output(stdout: str | None, stderr: str | None) -> tuple[str, str]:
        out = str(stdout or "").strip()
        if out:
            return out, "stdout"
        err = str(stderr or "").strip()
        if err:
            return err, "stderr"
        return "", "none"

    @classmethod
    def _sanitize_structured_output_text(cls, raw_text: str) -> str:
        return sanitize_structured_output_text(raw_text)

    @staticmethod
    def _normalize_tool_calls(tool_calls: object) -> list[dict] | None:
        if isinstance(tool_calls, list) and all(isinstance(item, dict) for item in tool_calls):
            return tool_calls
        if isinstance(tool_calls, dict):
            return [tool_calls]
        return None

    @classmethod
    def _normalize_structured_action_payload(cls, data: object) -> dict | None:
        return normalize_structured_action_payload(data)

    @classmethod
    def _parse_structured_action_payload(cls, raw_text: str) -> dict | None:
        return parse_structured_action_payload(raw_text)

    @classmethod
    def _locally_repair_structured_action_output(cls, raw_text: str) -> str | None:
        return locally_repair_structured_action_output(raw_text)

    @classmethod
    def _extract_structured_action_fields(cls, raw_text: str) -> tuple[str | None, list[dict] | None]:
        return extract_structured_action_fields(raw_text)

    def _repair_task_proposal(
        self,
        *,
        cli_runner: Callable,
        prompt: str,
        bad_output: str,
        validation_error: str,
        timeout: int,
        task_kind: str,
        policy_version: str,
        cfg: dict,
        primary_backend: str,
        primary_model: str | None,
        primary_temperature: float | None = None,
        research_context: dict | None = None,
        session: dict | None = None,
        workdir: str | None = None,
    ) -> dict | None:
        locally_repaired = self._locally_repair_structured_action_output(bad_output)
        if locally_repaired:
            return {
                "raw": locally_repaired,
                "output_source": "local_repair",
                "backend_used": primary_backend,
                "model": primary_model,
                "temperature": self._normalize_temperature(primary_temperature),
                "stderr": "",
                "rc": 0,
            }
        default_model = str(cfg.get("default_model") or cfg.get("model") or "").strip() or None
        first_backend = str(primary_backend or "opencode").strip().lower()
        if first_backend not in SUPPORTED_CLI_BACKENDS:
            first_backend = "opencode"
        first_model = primary_model or default_model

        repair_backend = str(cfg.get("task_propose_repair_backend") or "opencode").strip().lower()
        if repair_backend not in SUPPORTED_CLI_BACKENDS:
            repair_backend = "opencode"
        repair_model = str(cfg.get("task_propose_repair_model") or "").strip() or default_model
        normalized_temperature = self._normalize_temperature(primary_temperature)
        timeout_like_failure = validation_error == "empty_or_failed_cli_response" and "timeout" in str(bad_output or "").lower()
        candidates: list[tuple[str, str | None, float | None]] = []
        if not timeout_like_failure or repair_backend == first_backend:
            candidates.append((first_backend, first_model, normalized_temperature))
        candidates.append((repair_backend, repair_model, normalized_temperature))
        deduped: list[tuple[str, str | None, float | None]] = []
        seen: set[tuple[str, str, str]] = set()
        for backend_name, model_name, temperature in candidates:
            key = (backend_name, str(model_name or ""), str(temperature))
            if key in seen:
                continue
            seen.add(key)
            deduped.append((backend_name, model_name, temperature))

        repair_prompt = self._build_repair_prompt(prompt=prompt, bad_output=bad_output, validation_error=validation_error)
        for backend_name, model_name, temperature in deduped:
            cli_kwargs = {
                "prompt": repair_prompt,
                "options": ["--no-interaction"],
                "timeout": timeout,
                "backend": backend_name,
                "model": model_name,
                "routing_policy": {"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
                "workdir": workdir,
            }
            if temperature is not None:
                cli_kwargs["temperature"] = temperature
            if research_context:
                cli_kwargs["research_context"] = research_context
            if session:
                cli_kwargs["session"] = session
            rc, cli_out, cli_err, backend_used = self._invoke_cli_runner(cli_runner, **cli_kwargs)
            raw_res, output_source = self._coalesce_cli_output(cli_out, cli_err)
            if not raw_res.strip():
                continue
            command, tool_calls = self._extract_structured_action_fields(raw_res)
            if not command and not tool_calls:
                continue
            return {
                "raw": raw_res,
                "output_source": output_source,
                "backend_used": backend_used,
                "model": model_name,
                "temperature": temperature,
                "stderr": cli_err,
                "rc": rc,
            }
        return None

    @staticmethod
    def _is_timeout_like_repair_failure(*, validation_error: str, bad_output: str) -> bool:
        error_marker = str(validation_error or "").strip().lower()
        output_marker = str(bad_output or "").strip().lower()
        if error_marker in {"empty_or_failed_cli_response", "empty_cli_response"}:
            if not output_marker:
                return True
            return "timeout" in output_marker or "timed out" in output_marker
        return "timeout" in output_marker or "timed out" in output_marker

    @staticmethod
    def _is_shell_meta_blocked_failure(output: str | None, failure_type: str | None) -> bool:
        if str(failure_type or "").strip().lower() != "command_runtime_error":
            return False
        text = str(output or "")
        markers = (
            "Befehlskettung (&&/||)",
            "Semikolons (;)",
            "Input/Output-Redirection",
            "Background-Execution (&)",
            "Unsupported shell operators in command",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _build_repair_prompt(*, prompt: str, bad_output: str, validation_error: str) -> str:
        preview = str(bad_output or "").strip()
        if len(preview) > 2000:
            preview = preview[:2000]
        return (
            "Der vorherige Modell-Output war leer/ungueltig oder nicht ausfuehrbar.\n"
            "Repariere die Antwort und gib NUR ein valides JSON-Objekt zurueck.\n\n"
            f"Validator/Fehlergrund: {validation_error}\n\n"
            "Anforderungen:\n"
            "- Genau ein JSON-Objekt, kein Markdown.\n"
            "- Felder: reason (string), command (string optional), tool_calls (array optional).\n"
            "- Mindestens eines von command oder tool_calls muss befuellt sein.\n\n"
            f"Original-Prompt:\n{prompt}\n\n"
            f"Fehlerhafter Output (Ausschnitt):\n{preview}\n"
        )

    def _attempt_repaired_execute_after_meta_block(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        command: str | None,
        execution_output: str | None,
        execution_policy,
        agent_cfg: dict,
        cli_runner: Callable,
        tool_definitions_resolver: Callable | None,
        pipeline: dict,
        workspace_dir: str,
        exec_started_at: float | None,
    ) -> dict | None:
        proposal_meta = dict(task.get("last_proposal") or {})
        research_context = proposal_meta.get("research_context") if isinstance(proposal_meta.get("research_context"), dict) else None
        prompt, _ = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=str(task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"),
            tool_definitions_resolver=tool_definitions_resolver or (lambda *_args, **_kwargs: []),
            research_context=research_context,
        )
        timeout = self._resolve_task_propose_timeout(agent_cfg, task_kind)
        routing_policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
        primary_backend = str(
            proposal_meta.get("backend")
            or ((proposal_meta.get("routing") or {}).get("execution_backend"))
            or ((proposal_meta.get("routing") or {}).get("effective_backend"))
            or "opencode"
        ).strip().lower()
        primary_model = self._resolve_requested_model(
            agent_cfg=agent_cfg,
            requested_model=str(proposal_meta.get("model") or "").strip() or None,
        )
        bad_output = json.dumps(
            {
                "blocked_command": command,
                "execution_error": execution_output,
                "raw_proposal_preview": str(proposal_meta.get("raw") or "")[:1200],
            },
            ensure_ascii=False,
        )
        repaired = self._repair_task_proposal(
            cli_runner=cli_runner,
            prompt=prompt,
            bad_output=bad_output,
            validation_error="shell_meta_character_blocked",
            timeout=timeout,
            task_kind=task_kind,
            policy_version=routing_policy_version,
            cfg=agent_cfg,
            primary_backend=primary_backend,
            primary_model=primary_model,
            primary_temperature=self._normalize_temperature(((proposal_meta.get("routing") or {}).get("inference_temperature"))),
            research_context=research_context,
            session=self._prepare_task_cli_session(
                tid=tid,
                task=task,
                backend=primary_backend,
                model=primary_model,
                agent_cfg=agent_cfg,
            ),
            workdir=workspace_dir,
        )
        if not repaired:
            return None
        repaired_command, repaired_tool_calls = self._extract_structured_action_fields(str(repaired.get("raw") or ""))
        if not repaired_command and not repaired_tool_calls:
            return None
        if repaired_command and repaired_command.strip() == str(command or "").strip() and not repaired_tool_calls:
            return None
        append_stage(
            pipeline,
            name="proposal_repair",
            status="ok",
            metadata={
                "reason": "shell_meta_character_blocked",
                "repair_backend": repaired.get("backend_used"),
                "repair_model": repaired.get("model"),
            },
        )
        repaired_run = get_core_services().task_execution_service.execute_local_step(
            tid=tid,
            task=task,
            command=repaired_command,
            tool_calls=repaired_tool_calls,
            execution_policy=execution_policy,
            guard_cfg=agent_cfg,
            working_directory=workspace_dir,
            pipeline=pipeline,
            exec_started_at=exec_started_at,
        )
        return {
            "reason": _extract_reason(str(repaired.get("raw") or "")) or "Repaired proposal after shell policy block.",
            "command": repaired_command,
            "tool_calls": repaired_tool_calls,
            "execution_run": repaired_run,
            "repair_meta": {
                "attempted": True,
                "trigger": "shell_meta_character_blocked",
                "repair_backend": repaired.get("backend_used"),
                "repair_model": repaired.get("model"),
                "output_source": repaired.get("output_source"),
            },
        }

    def _build_research_result(
        self,
        raw_res: str,
        backend_used: str,
        tid: str | None,
        rc: int,
        cli_err: str,
        latency_ms: int,
        output_source: str = "stdout",
        research_context: dict | None = None,
    ) -> dict:
        artifact = normalize_research_artifact(
            raw_res,
            backend=backend_used,
            task_id=tid,
            cli_result={
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
            },
            research_context=research_context,
        )
        return {
            "reason": artifact.get("summary") or "Research report generated",
            "raw": raw_res,
            "research_artifact": artifact,
            "research_context": research_context,
            "backend": backend_used,
            "command": None,
            "tool_calls": None,
            "cli_result": {
                "returncode": rc,
                "latency_ms": latency_ms,
                "stderr_preview": (cli_err or "")[:240],
                "output_source": output_source,
            },
        }

    def _verify_research_artifact(self, research_artifact: dict | None) -> dict:
        artifact = dict(research_artifact or {})
        report_markdown = str(artifact.get("report_markdown") or "").strip()
        sources = list(artifact.get("sources") or [])
        citations = list(artifact.get("citations") or [])
        passed = bool(report_markdown and sources)
        verification = {
            "passed": passed,
            "ready": passed,
            "has_report": bool(report_markdown),
            "has_sources": bool(sources),
            "has_citations": bool(citations),
            "source_count": len(sources),
            "citation_count": len(citations),
            "reason": "verified" if passed else "missing_sources_or_report",
        }
        artifact_verification = dict(artifact.get("verification") or {})
        artifact_verification.update(verification)
        artifact["verification"] = artifact_verification
        return artifact_verification

    def _build_review_state(
        self,
        agent_cfg: dict,
        backend: str,
        task_kind: str,
        *,
        command: str | None,
        tool_calls: list[dict] | None,
    ) -> dict:
        risk_level = max_risk_level(
            classify_command_risk(command),
            classify_tool_calls_risk(tool_calls, guard_cfg=agent_cfg),
        )
        policy = review_policy(
            agent_cfg,
            backend=backend,
            task_kind=task_kind,
            risk_level=risk_level,
            uses_terminal=has_terminal_signal(command),
            uses_file_access=has_file_access_signal(command, tool_calls),
        )
        return {
            "required": bool(policy.get("required")),
            "status": "pending" if policy.get("required") else "not_required",
            "policy_version": policy.get("policy_version"),
            "reason": policy.get("reason"),
            "risk_level": policy.get("risk_level"),
            "uses_terminal": policy.get("uses_terminal"),
            "uses_file_access": policy.get("uses_file_access"),
            "reviewed_by": None,
            "reviewed_at": None,
            "comment": None,
        }

    def _get_worker_execution_context(
        self,
        task: dict | None,
        *,
        tid: str | None = None,
        base_prompt: str | None = None,
    ) -> dict:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        if execution_context:
            execution_context["allowed_tools"] = normalize_allowed_tools(execution_context.get("allowed_tools"))
            return execution_context
        bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
        bundle = None
        if bundle_id:
            bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
        if bundle is None and (tid or (task or {})):
            resolved = get_context_manager_service().ensure_task_context_bundle(
                task=dict(task or {}),
                task_id=tid,
                query=base_prompt,
            )
            resolved_bundle = resolved.get("context_bundle")
            if resolved_bundle is not None:
                bundle = resolved_bundle
        if bundle is None:
            return {}
        return {
            "context_bundle_id": bundle.id,
            "context": {
                "context_text": bundle.context_text,
                "chunks": list(bundle.chunks or []),
                "token_estimate": int(bundle.token_estimate or 0),
                "bundle_metadata": dict(bundle.bundle_metadata or {}),
            },
        }

    def _tool_definitions_for_task(
        self,
        task: dict | None,
        *,
        tool_definitions_resolver: Callable,
        execution_context: dict | None = None,
    ) -> list[dict]:
        execution_context = dict(execution_context or self._get_worker_execution_context(task))
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        if allowed_tools:
            return tool_definitions_resolver(allowlist=allowed_tools)
        return tool_definitions_resolver()

    @staticmethod
    def _cli_session_policy(agent_cfg: dict | None) -> dict:
        cfg = agent_cfg or {}
        mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
        backends = [str(item or "").strip().lower() for item in list(mode.get("stateful_backends") or ["opencode", "codex"]) if str(item or "").strip()]
        return {
            "enabled": bool(mode.get("enabled", False)),
            "stateful_backends": backends,
            "max_turns_per_session": max(1, min(int(mode.get("max_turns_per_session") or 40), 200)),
            "max_sessions": max(1, min(int(mode.get("max_sessions") or 200), 2000)),
            "allow_task_scoped_auto_session": bool(mode.get("allow_task_scoped_auto_session", True)),
            "reuse_scope": str(mode.get("reuse_scope") or "task").strip().lower() or "task",
            "native_opencode_sessions": bool(mode.get("native_opencode_sessions", False)),
        }

    @staticmethod
    def _resolve_opencode_execution_mode(agent_cfg: dict | None) -> str:
        cfg = agent_cfg or {}
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        mode = str(runtime_cfg.get("execution_mode") or "live_terminal").strip().lower()
        return mode if mode in {"backend", "live_terminal", "interactive_terminal"} else "live_terminal"

    @staticmethod
    def _resolve_opencode_interactive_launch_mode(agent_cfg: dict | None) -> str:
        cfg = agent_cfg or {}
        runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
        mode = str(runtime_cfg.get("interactive_launch_mode") or "run").strip().lower()
        return mode if mode in {"run", "tui"} else "run"

    def _resolve_task_role_identity(self, tid: str, task: dict) -> tuple[str | None, str | None]:
        task_record = get_repository_registry().task_repo.get_by_id(tid)
        if not task_record:
            return None, None
        role_id = getattr(task_record, "assigned_role_id", None)
        if task_record.team_id and task_record.assigned_agent_url:
            members = get_repository_registry().team_member_repo.get_by_team(task_record.team_id)
            for member in members:
                if member.agent_url == task_record.assigned_agent_url and not role_id:
                    role_id = member.role_id
                    break
        role_name = None
        if role_id:
            role = get_repository_registry().role_repo.get_by_id(role_id)
            if role:
                role_name = role.name
        return str(role_id or "").strip() or None, str(role_name or "").strip() or None

    def _resolve_task_session_scope(self, *, tid: str, task: dict, policy: dict) -> tuple[str, str, str | None]:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        workspace = dict(execution_context.get("workspace") or {})
        explicit_scope_key = str(workspace.get("session_scope_key") or "").strip()
        if explicit_scope_key:
            explicit_scope_kind = str(workspace.get("session_scope_kind") or "workspace").strip().lower() or "workspace"
            return explicit_scope_kind, explicit_scope_key, None

        reuse_scope = str(policy.get("reuse_scope") or "task").strip().lower()
        if reuse_scope == "role":
            role_id, role_name = self._resolve_task_role_identity(tid, task)
            if role_id or role_name:
                role_key = role_id or f"role-name:{role_name}"
                return "role", str(role_key), role_name
        return "task", f"task:{tid}", None

    @staticmethod
    def _has_native_opencode_runtime(session_payload: dict | None) -> bool:
        metadata = (session_payload or {}).get("metadata") if isinstance((session_payload or {}).get("metadata"), dict) else {}
        runtime_meta = metadata.get("opencode_runtime") if isinstance(metadata.get("opencode_runtime"), dict) else {}
        return str(runtime_meta.get("kind") or "").strip().lower() == "native_server"

    def _prepare_task_cli_session(
        self,
        *,
        tid: str,
        task: dict,
        backend: str,
        model: str | None,
        agent_cfg: dict | None,
    ) -> dict | None:
        policy = self._cli_session_policy(agent_cfg)
        backend_name = str(backend or "").strip().lower()
        opencode_execution_mode = self._resolve_opencode_execution_mode(agent_cfg)
        opencode_interactive_launch_mode = self._resolve_opencode_interactive_launch_mode(agent_cfg)
        terminal_execution_mode = (
            opencode_execution_mode if backend_name == "opencode" and opencode_execution_mode in {"live_terminal", "interactive_terminal"} else None
        )
        if not terminal_execution_mode and (not policy["enabled"] or not policy["allow_task_scoped_auto_session"]):
            return None
        if not terminal_execution_mode and backend_name not in set(policy["stateful_backends"]):
            return None
        scope_kind, scope_key, role_name = self._resolve_task_session_scope(tid=tid, task=task, policy=policy)
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        workspace_dir = str(workspace_context.workspace_dir)
        verification = dict(task.get("verification_status") or {})
        session_meta = verification.get("cli_session") if isinstance(verification.get("cli_session"), dict) else {}
        existing_id = str(session_meta.get("session_id") or "").strip()
        session = get_cli_session_service().get_session(existing_id, include_history=False) if existing_id else None
        if (
            not session
            or str(session.get("status") or "").strip().lower() != "active"
            or str(session.get("backend") or "").strip().lower() != backend_name
        ):
            session = get_cli_session_service().find_active_session(
                backend=backend_name,
                scope_key=scope_key,
                scope_kind=scope_kind,
            )
        session_reused = False
        if session and str(session.get("status") or "").strip().lower() == "active" and str(session.get("backend") or "").strip().lower() == backend_name:
            session_payload = dict(session)
            session_reused = True
        else:
            session_payload = get_cli_session_service().create_session(
                backend=backend_name,
                model=model,
                metadata={
                    "source": "task_propose_auto_session",
                    "task_id": tid,
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                    "role_name": role_name,
                    "opencode_workdir": workspace_dir,
                    "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                },
                task_id=tid,
                conversation_id=scope_key,
            )
            verification["cli_session"] = {
                "session_id": session_payload.get("id"),
                "backend": backend_name,
                "model": model,
                "status": "active",
                "scope_kind": scope_kind,
                "scope_key": scope_key,
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if backend_name == "opencode" and not terminal_execution_mode:
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": "backend",
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": {},
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": "backend",
                "terminal_session_id": None,
                "forward_param": None,
                "terminal_status": None,
                "updated_at": time.time(),
            }
            verification["opencode_live_terminal"] = {}
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        if terminal_execution_mode:
            terminal_meta = (
                get_live_terminal_session_service().ensure_session_for_cli(
                    session_payload,
                    execution_mode=terminal_execution_mode,
                    workdir=workspace_dir,
                )
                or {}
            )
            interactive_terminal_workspace = (
                dict(verification.get("interactive_terminal_workspace") or {})
                if isinstance(verification.get("interactive_terminal_workspace"), dict)
                else {}
            )
            if terminal_execution_mode == "interactive_terminal" and not interactive_terminal_workspace.get("baseline_ready"):
                baseline_meta = get_worker_workspace_service().refresh_interactive_terminal_baseline(workspace_dir=Path(workspace_dir))
                interactive_terminal_workspace = {
                    "baseline_ready": True,
                    **baseline_meta,
                }
            session_payload = (
                get_cli_session_service().update_session(
                    str(session_payload.get("id") or ""),
                    metadata_updates={
                        "opencode_execution_mode": terminal_execution_mode,
                        "opencode_interactive_launch_mode": opencode_interactive_launch_mode,
                        "opencode_live_terminal": terminal_meta,
                        "opencode_workdir": workspace_dir,
                    },
                )
                or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "execution_mode": terminal_execution_mode,
                "terminal_session_id": terminal_meta.get("terminal_session_id"),
                "forward_param": terminal_meta.get("forward_param"),
                "agent_url": terminal_meta.get("agent_url"),
                "agent_name": terminal_meta.get("agent_name"),
                "terminal_status": terminal_meta.get("status"),
                "updated_at": time.time(),
            }
            if interactive_terminal_workspace:
                verification["interactive_terminal_workspace"] = interactive_terminal_workspace
            verification["opencode_live_terminal"] = dict(terminal_meta)
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        elif backend_name == "opencode" and bool(policy.get("native_opencode_sessions")):
            from agent.services.opencode_runtime_service import get_opencode_runtime_service

            runtime_meta = get_opencode_runtime_service().ensure_session_runtime(session_payload, model=model)
            session_payload = (
                get_cli_session_service().get_session(str(session_payload.get("id") or ""), include_history=False) or session_payload
            )
            verification["cli_session"] = {
                **verification.get("cli_session", {}),
                "native_session_id": runtime_meta.get("native_session_id"),
                "server_key": runtime_meta.get("server_key"),
                "server_url": runtime_meta.get("server_url"),
                "agent": runtime_meta.get("agent"),
                "updated_at": time.time(),
            }
            update_local_task_status(
                tid,
                str(task.get("status") or "assigned"),
                verification_status=verification,
            )
        get_cli_session_service().prune_sessions(max_sessions=policy["max_sessions"])
        session_payload["session_reused"] = bool(session_reused)
        session_payload["max_turns_per_session"] = policy["max_turns_per_session"]
        return session_payload

    def _build_task_propose_prompt(
        self,
        *,
        tid: str,
        task: dict,
        base_prompt: str,
        tool_definitions_resolver: Callable,
        research_context: dict | None = None,
        interactive_terminal: bool = False,
    ) -> tuple[str, dict]:
        execution_context = self._get_worker_execution_context(task, tid=tid, base_prompt=base_prompt)
        context_payload = dict(execution_context.get("context") or {})
        context_text = str(context_payload.get("context_text") or "").strip()
        workspace_payload = dict(execution_context.get("workspace") or {})
        workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
        allowed_tools = normalize_allowed_tools(execution_context.get("allowed_tools"))
        expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
        tool_definitions = self._tool_definitions_for_task(
            task,
            tool_definitions_resolver=tool_definitions_resolver,
            execution_context=execution_context,
        )

        prompt_sections: list[str] = []
        system_prompt = self._get_system_prompt_for_task(tid)
        instruction_stack = get_instruction_layer_service().assemble_for_task(
            task=task,
            base_prompt=base_prompt,
            system_prompt=system_prompt,
            emit_audit=True,
        )
        effective_system_prompt = str(instruction_stack.get("rendered_system_prompt") or "").strip() or None
        stack_diagnostics = dict(instruction_stack.get("diagnostics") or {})
        opencode_context_files = get_worker_workspace_service().prepare_opencode_context_files(
            task=task,
            workspace_context=workspace_context,
            base_prompt=base_prompt,
            system_prompt=effective_system_prompt,
            context_text=context_text,
            expected_output_schema=expected_output_schema,
            tool_definitions=tool_definitions,
            research_context=research_context,
            include_response_contract=not interactive_terminal,
        )
        prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")
        read_paths = [
            str(opencode_context_files.get("agents_path") or "").strip(),
            str(opencode_context_files.get("context_index_path") or "").strip(),
            str(opencode_context_files.get("task_brief_path") or "").strip(),
        ]
        if not interactive_terminal:
            read_paths.append(str(opencode_context_files.get("response_contract_path") or "").strip())
        read_paths = [item for item in read_paths if item]
        if read_paths:
            prompt_sections.append(
                "Lies zuerst die bereitgestellten Workspace-Dateien und verwende diesen Dateikontext "
                "statt lange Inhalte zu wiederholen:\n" + "\n".join(f"- {item}" for item in read_paths)
            )
        if context_text:
            context_preview = " ".join(str(context_text).split()).strip().lower()[:240]
            prompt_sections.append(
                "Selektierter Hub-Kontext ist ausgelagert in "
                f"{str(opencode_context_files.get('hub_context_path') or '.ananta/hub-context.md')}. "
                "Nutze diesen Kontext als verbindliche Grundlage."
            )
            prompt_sections.append(
                "Selektierter Research-Kontext ist im Hub-Kontext enthalten und wird aus derselben Datei geladen."
            )
            if context_preview:
                prompt_sections.append(f"Kurzvorschau Hub-Kontext: {context_preview}")
        research_prompt_section = str((research_context or {}).get("prompt_section") or "").strip()
        if research_prompt_section:
            prompt_sections.append("Selektierter Research-Kontext:\n" + research_prompt_section)
        if allowed_tools:
            prompt_sections.append(
                "Tool-Scope fuer diesen Task (nur diese Tools verwenden): "
                + ", ".join(str(item) for item in allowed_tools)
            )
        if expected_output_schema:
            prompt_sections.append(
                "Erwartetes Output-Schema (Kurzfassung): "
                + json.dumps(expected_output_schema, ensure_ascii=False)[:400]
            )
        if stack_diagnostics:
            prompt_sections.append(get_instruction_layer_service().render_diagnostics_brief(stack_diagnostics))
        prompt_sections.append(
            "Arbeitsverzeichnis fuer Datei-/Shell-Aktionen:\n"
            f"- workspace: {workspace_context.workspace_dir}\n"
            f"- artifacts: {workspace_context.artifacts_dir}\n"
            f"- rag_helper: {workspace_context.rag_helper_dir}\n"
            "Nutze ausschliesslich diesen Workspace fuer neue oder geaenderte Dateien."
        )
        if interactive_terminal:
            prompt_sections.append(
                "Arbeite direkt im Workspace mit normalem OpenCode-CLI. "
                "Fuehre die gewuenschten Datei- und Verzeichnis-Aenderungen im Workspace aus. "
                "Nutze bei Bedarf `rag_helper/` fuer Hilfsdateien oder ausgelagerten Kontext. "
                "Es ist keine JSON-Antwort erforderlich; Workspace-Aenderungen und Diffs werden nach dem Lauf automatisch erfasst."
            )
        else:
            prompt_sections.append(
                "Antworte ausschliesslich als genau ein JSON-Objekt. "
                "Beachte dafuer die Regeln in "
                f"{str(opencode_context_files.get('response_contract_path') or '.ananta/response-contract.md')} "
                "und setze mindestens eines von 'command' oder 'tool_calls'."
            )
            prompt_sections.append(
                "Priorisiere `tool_calls` fuer Datei-/Verzeichnis- und Code-Aenderungen. "
                "Falls ein Shell-Befehl erforderlich ist, liefere genau einen einzelnen `command` "
                "ohne `&&`, `||`, `;`, `>`, `<` oder `|`."
            )
        return "\n\n".join(section for section in prompt_sections if section), {
            "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
            "allowed_tools": allowed_tools,
            "expected_output_schema": expected_output_schema,
            "workspace": {
                "requested": workspace_payload or None,
                "workspace_dir": str(workspace_context.workspace_dir),
                "artifacts_dir": str(workspace_context.artifacts_dir),
                "rag_helper_dir": str(workspace_context.rag_helper_dir),
                "opencode_context_files": opencode_context_files,
            },
            "context_chunk_count": len(context_payload.get("chunks") or []),
            "has_context_text": bool(context_text),
            "instruction_layers": stack_diagnostics,
            "research_context": {
                "artifact_ids": list((research_context or {}).get("artifact_ids") or []),
                "knowledge_collection_ids": list((research_context or {}).get("knowledge_collection_ids") or []),
                "repo_scope_refs": list((research_context or {}).get("repo_scope_refs") or []),
                "truncated": bool((research_context or {}).get("truncated")),
                "context_char_count": int((research_context or {}).get("context_char_count") or 0),
            }
            if research_context
            else None,
        }

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        task = get_repository_registry().task_repo.get_by_id(tid)
        if not task:
            return None

        repos = get_repository_registry()
        resolved = resolve_task_role_template(task, repos=repos)
        role_id = resolved.get("role_id")
        template_id = resolved.get("template_id")
        if not template_id:
            return None
        template = repos.template_repo.get_by_id(template_id)
        if not template:
            return None

        prompt = template.prompt_template
        goal_text = ""
        goal_context = ""
        acceptance_criteria: list[str] = []
        goal_id = str(task.goal_id or "").strip()
        if goal_id:
            goal = repos.goal_repo.get_by_id(goal_id)
            if goal:
                goal_text = str(goal.goal or "").strip()
                goal_context = str(goal.context or "").strip()
                acceptance_criteria = [str(item) for item in (goal.acceptance_criteria or []) if str(item or "").strip()]
        variables = {
            "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
            "task_title": task.title or "Kein Titel",
            "task_description": task.description or "Keine Beschreibung",
            "team_goal": goal_text
            or str(task.title or "").strip()
            or str(task.description or "").strip()
            or str(resolved.get("team_name") or "").strip()
            or "aktuelles Teamziel",
            "goal_context": goal_context,
            "acceptance_criteria": "\n".join(f"- {item}" for item in acceptance_criteria),
        }
        if resolved.get("team_name"):
            variables["team_name"] = resolved["team_name"]
        if resolved.get("role_name"):
            variables["role_name"] = resolved["role_name"]
        for key, value in variables.items():
            prompt = prompt.replace("{{" + key + "}}", str(value))
        return prompt

    @staticmethod
    def _routing_dimensions(
        *,
        backend_used: str,
        model: str | None,
        temperature: float | None = None,
        requested_backend: str = "auto",
        agent_cfg: dict | None = None,
    ) -> dict:
        backend = str(backend_used or "").strip().lower()
        requested = str(requested_backend or "auto").strip().lower()
        dimensions = {
            "requested_backend": requested or "auto",
            "execution_backend": backend or requested or "sgpt",
            "inference_provider": None,
            "inference_model": str(model or "").strip() or None,
            "inference_temperature": TaskScopedExecutionService._normalize_temperature(temperature),
            "inference_base_url": None,
            "inference_target_kind": None,
            "inference_target_provider_type": None,
            "remote_hub": False,
            "instance_id": None,
            "max_hops": None,
        }
        cfg = agent_cfg if isinstance(agent_cfg, dict) else ((current_app.config.get("AGENT_CONFIG", {}) or {}) if has_app_context() else {})
        if backend == "codex":
            runtime_cfg = resolve_codex_runtime_config() if has_app_context() else {}
            dimensions.update(
                {
                    "inference_provider": runtime_cfg.get("target_provider") or str(cfg.get("default_provider") or "").strip().lower() or "openai_compatible",
                    "inference_base_url": runtime_cfg.get("base_url"),
                    "inference_target_kind": runtime_cfg.get("target_kind"),
                    "inference_target_provider_type": runtime_cfg.get("target_provider_type"),
                    "remote_hub": bool(runtime_cfg.get("remote_hub")),
                    "instance_id": runtime_cfg.get("instance_id"),
                    "max_hops": runtime_cfg.get("max_hops"),
                }
            )
            return dimensions
        dimensions["inference_provider"] = str(cfg.get("default_provider") or "").strip().lower() or None
        return dimensions


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
