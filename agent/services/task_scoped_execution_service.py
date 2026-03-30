from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass
from typing import Callable

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import TaskConflictError, TaskNotFoundError, WorkerForwardingError
from agent.common.sgpt import SUPPORTED_CLI_BACKENDS
from agent.config import settings
from agent.models import TaskStepExecuteRequest
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import normalize_research_artifact
from agent.runtime_policy import build_trace_record, normalize_task_kind, resolve_cli_backend, review_policy, runtime_routing_config
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.task_handler_registry import get_task_handler_registry
from agent.services.task_execution_policy_service import resolve_execution_policy
from agent.services.task_runtime_service import get_local_task_status, update_local_task_status
from agent.utils import _extract_command, _extract_reason, _extract_tool_calls, _log_terminal_entry


@dataclass(frozen=True)
class TaskScopedRouteResponse:
    data: dict
    status: str = "success"
    message: str | None = None
    code: int = 200


class TaskScopedExecutionService:
    """Owns task-scoped proposal/execution orchestration so routes stay thin."""

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

        prompt, worker_context_meta = self._build_task_propose_prompt(
            tid=tid,
            task=task,
            base_prompt=base_prompt,
            tool_definitions_resolver=tool_definitions_resolver,
        )

        if request_data.providers:
            return self._propose_task_with_comparisons(
                tid=tid,
                task=task,
                request_data=request_data,
                prompt=prompt,
                base_prompt=base_prompt,
                worker_context_meta=worker_context_meta,
                cli_runner=cli_runner,
                cfg=cfg,
            )

        return self._propose_single_task_step(
            tid=tid,
            task=task,
            request_data=request_data,
            prompt=prompt,
            base_prompt=base_prompt,
            worker_context_meta=worker_context_meta,
            cli_runner=cli_runner,
            cfg=cfg,
        )

    def execute_task_step(
        self,
        tid: str,
        request_data,
        *,
        forwarder: Callable,
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

        exec_started_at = time.time()
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
            pipeline=pipeline,
            exec_started_at=exec_started_at,
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

    def _propose_task_with_comparisons(
        self,
        *,
        tid: str,
        task: dict,
        request_data,
        prompt: str,
        base_prompt: str,
        worker_context_meta: dict,
        cli_runner: Callable,
        cfg: dict,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
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
            selected_model = ((parts[1].strip() if len(parts) > 1 else "") or request_data.model or cfg.get("default_model") or cfg.get("model"))
            if requested_backend not in SUPPORTED_CLI_BACKENDS:
                return entry, {"error": f"unsupported_backend:{requested_backend}", "backend": requested_backend}

            effective_backend, routing_reason = self._resolve_cli_backend(
                task_kind,
                requested_backend=requested_backend,
                agent_cfg=cfg,
            )
            started_at = time.time()
            rc, cli_out, cli_err, backend_used = cli_runner(
                prompt=prompt,
                options=["--no-interaction"],
                timeout=compare_policy.timeout_seconds,
                backend=effective_backend,
                model=selected_model,
                routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_policy_version},
            )
            latency_ms = int((time.time() - started_at) * 1000)
            raw_res = cli_out or ""
            routing = {"task_kind": task_kind, "effective_backend": effective_backend, "reason": routing_reason}
            cli_result = {"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]}
            if rc != 0 and not raw_res.strip():
                return entry, {"error": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if not raw_res:
                return entry, {"error": "empty_response", "backend": backend_used, "routing": routing, "cli_result": cli_result}
            if backend_used == "deerflow":
                deerflow_res = self._build_research_result(raw_res, backend_used, tid, rc, cli_err, latency_ms)
                deerflow_res["model"] = selected_model
                deerflow_res["routing"] = routing
                return entry, deerflow_res
            return entry, {
                "reason": _extract_reason(raw_res),
                "command": _extract_command(raw_res),
                "tool_calls": _extract_tool_calls(raw_res),
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
        prompt: str,
        base_prompt: str,
        worker_context_meta: dict,
        cli_runner: Callable,
        cfg: dict,
    ) -> TaskScopedRouteResponse:
        task_kind = normalize_task_kind(None, base_prompt)
        effective_backend, routing_reason = self._resolve_cli_backend(task_kind, requested_backend="auto")
        timeout = int((current_app.config.get("AGENT_CONFIG", {}) or {}).get("command_timeout", 60) or 60)
        policy_version = runtime_routing_config(current_app.config.get("AGENT_CONFIG", {}) or {})["policy_version"]
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
        started_at = time.time()
        rc, cli_out, cli_err, backend_used = cli_runner(
            prompt=prompt,
            options=["--no-interaction"],
            timeout=timeout,
            backend=effective_backend,
            model=request_data.model,
            routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": policy_version},
        )
        latency_ms = int((time.time() - started_at) * 1000)
        append_stage(
            pipeline,
            name="execute",
            status="ok" if rc == 0 or bool(cli_out) else "error",
            metadata={"backend_used": backend_used, "returncode": rc, "latency_ms": latency_ms},
            started_at=started_at,
        )
        raw_res = cli_out or ""
        if rc != 0 and not raw_res.strip():
            return TaskScopedRouteResponse(
                status="error",
                message="llm_cli_failed",
                data={"details": cli_err or f"backend '{backend_used}' failed with exit code {rc}", "backend": backend_used},
                code=502,
            )
        if not raw_res:
            return TaskScopedRouteResponse(status="error", message="llm_failed", data={}, code=502)

        routing = {"task_kind": task_kind, "effective_backend": effective_backend, "reason": routing_reason}
        if backend_used == "deerflow":
            research_res = self._build_research_result(raw_res, backend_used, tid, rc, cli_err, latency_ms)
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
                model=request_data.model or cfg.get("default_model") or cfg.get("model"),
                routing=routing,
                cli_result=research_res.get("cli_result"),
                worker_context=worker_context_meta,
                trace=trace,
                review=self._build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
                pipeline=pipeline_payload,
                research_artifact=research_res.get("research_artifact"),
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
            return TaskScopedRouteResponse(data=response_payload)

        reason = _extract_reason(raw_res)
        command = _extract_command(raw_res)
        tool_calls = _extract_tool_calls(raw_res)
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
            model=request_data.model or cfg.get("default_model") or cfg.get("model"),
            routing=routing,
            cli_result={"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
            worker_context=worker_context_meta,
            trace=trace,
            review=self._build_review_state(current_app.config.get("AGENT_CONFIG", {}) or {}, backend_used, task_kind),
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
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=tid)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
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
        artifact_ref = get_core_services().task_execution_tracking_service.persist_research_artifact(
            tid=tid,
            task=task,
            research_artifact=research_artifact,
        )
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
        if "command" in response:
            update_local_task_status(task["id"], "proposing", last_proposal=response)

    def _persist_forwarded_execution(self, *, tid: str, response: dict, task: dict, request_data) -> None:
        if "status" not in response:
            return
        history = task.get("history", [])
        proposal_meta = task.get("last_proposal", {}) or {}
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
                "forwarded": True,
                "timestamp": time.time(),
            }
        )
        update_local_task_status(tid, response["status"], history=history)

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
        handler = get_task_handler_registry().resolve(task_kind)
        if handler is None or not hasattr(handler, "propose"):
            return None
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
        )
        return self._coerce_handler_response(response)

    def _try_handler_execute(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data,
        forwarder: Callable,
    ) -> TaskScopedRouteResponse | None:
        handler = get_task_handler_registry().resolve(task_kind)
        if handler is None or not hasattr(handler, "execute"):
            return None
        response = handler.execute(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            service=self,
            forwarder=forwarder,
        )
        return self._coerce_handler_response(response)

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

    def _resolve_cli_backend(self, task_kind: str, requested_backend: str = "auto", agent_cfg: dict | None = None) -> tuple[str, str]:
        backend, reason, _ = resolve_cli_backend(
            task_kind=task_kind,
            requested_backend=requested_backend,
            supported_backends=SUPPORTED_CLI_BACKENDS,
            agent_cfg=agent_cfg if agent_cfg is not None else (current_app.config.get("AGENT_CONFIG", {}) or {}),
            fallback_backend="sgpt",
        )
        return backend, reason

    def _build_research_result(self, raw_res: str, backend_used: str, tid: str | None, rc: int, cli_err: str, latency_ms: int) -> dict:
        artifact = normalize_research_artifact(
            raw_res,
            backend=backend_used,
            task_id=tid,
            cli_result={"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
        )
        return {
            "reason": artifact.get("summary") or "Research report generated",
            "raw": raw_res,
            "research_artifact": artifact,
            "backend": backend_used,
            "command": None,
            "tool_calls": None,
            "cli_result": {"returncode": rc, "latency_ms": latency_ms, "stderr_preview": (cli_err or "")[:240]},
        }

    def _build_review_state(self, agent_cfg: dict, backend: str, task_kind: str) -> dict:
        policy = review_policy(agent_cfg, backend=backend, task_kind=task_kind)
        return {
            "required": bool(policy.get("required")),
            "status": "pending" if policy.get("required") else "not_required",
            "policy_version": policy.get("policy_version"),
            "reason": policy.get("reason"),
            "reviewed_by": None,
            "reviewed_at": None,
            "comment": None,
        }

    def _get_worker_execution_context(self, task: dict | None) -> dict:
        execution_context = dict((task or {}).get("worker_execution_context") or {})
        if execution_context:
            return execution_context
        bundle_id = str((task or {}).get("context_bundle_id") or "").strip()
        if not bundle_id:
            return {}
        bundle = get_repository_registry().context_bundle_repo.get_by_id(bundle_id)
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

    def _tool_definitions_for_task(self, task: dict | None, *, tool_definitions_resolver: Callable) -> list[dict]:
        execution_context = self._get_worker_execution_context(task)
        allowed_tools = list(execution_context.get("allowed_tools") or [])
        if allowed_tools:
            return tool_definitions_resolver(allowlist=allowed_tools)
        return tool_definitions_resolver()

    def _build_task_propose_prompt(
        self,
        *,
        tid: str,
        task: dict,
        base_prompt: str,
        tool_definitions_resolver: Callable,
    ) -> tuple[str, dict]:
        execution_context = self._get_worker_execution_context(task)
        context_payload = dict(execution_context.get("context") or {})
        context_text = str(context_payload.get("context_text") or "").strip()
        allowed_tools = list(execution_context.get("allowed_tools") or [])
        expected_output_schema = dict(execution_context.get("expected_output_schema") or {})
        tools_desc = json.dumps(
            self._tool_definitions_for_task(task, tool_definitions_resolver=tool_definitions_resolver),
            indent=2,
            ensure_ascii=False,
        )

        prompt_sections: list[str] = []
        system_prompt = self._get_system_prompt_for_task(tid)
        if system_prompt:
            prompt_sections.append(system_prompt)
        prompt_sections.append(f"Aktueller Auftrag: {base_prompt}")
        if context_text:
            prompt_sections.append(f"Selektierter Hub-Kontext:\n{context_text}")
        if expected_output_schema:
            prompt_sections.append(
                "Erwartetes Ausgabeschema (JSON Schema oder Strukturhinweis):\n"
                f"{json.dumps(expected_output_schema, indent=2, ensure_ascii=False)}"
            )
        prompt_sections.append(f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}")
        prompt_sections.append(
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            '  "reason": "Kurze Begründung",\n'
            '  "command": "Shell-Befehl (optional)",\n'
            '  "tool_calls": [ { "name": "tool_name", "args": { "arg1": "val1" } } ] (optional)\n'
            "}"
        )
        return "\n\n".join(section for section in prompt_sections if section), {
            "context_bundle_id": execution_context.get("context_bundle_id") or task.get("context_bundle_id"),
            "allowed_tools": allowed_tools,
            "expected_output_schema": expected_output_schema,
            "context_chunk_count": len(context_payload.get("chunks") or []),
            "has_context_text": bool(context_text),
        }

    def _get_system_prompt_for_task(self, tid: str) -> str | None:
        task = get_repository_registry().task_repo.get_by_id(tid)
        if not task:
            return None

        role_id = task.assigned_role_id
        template_id = None
        if task.team_id and task.assigned_agent_url:
            members = get_repository_registry().team_member_repo.get_by_team(task.team_id)
            for member in members:
                if member.agent_url == task.assigned_agent_url:
                    if not role_id:
                        role_id = member.role_id
                    template_id = getattr(member, "custom_template_id", None)
                    break

        if role_id and not template_id:
            role = get_repository_registry().role_repo.get_by_id(role_id)
            if role:
                template_id = role.default_template_id

        if not template_id:
            return None
        template = get_repository_registry().template_repo.get_by_id(template_id)
        if not template:
            return None

        prompt = template.prompt_template
        variables = {
            "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
            "task_title": task.title or "Kein Titel",
            "task_description": task.description or "Keine Beschreibung",
        }
        if task.team_id:
            team = get_repository_registry().team_repo.get_by_id(task.team_id)
            if team:
                variables["team_name"] = team.name
        if role_id:
            role = get_repository_registry().role_repo.get_by_id(role_id)
            if role:
                variables["role_name"] = role.name
        for key, value in variables.items():
            prompt = prompt.replace("{{" + key + "}}", str(value))
        return prompt


task_scoped_execution_service = TaskScopedExecutionService()


def get_task_scoped_execution_service() -> TaskScopedExecutionService:
    return task_scoped_execution_service
