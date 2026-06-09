"""Single-backend task propose path for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
propose_single cluster of SPLIT-001 (sub-split 001m). The module owns the full
propose flow for a single CLI backend: session prep, prompt build, CLI invocation,
repair, research-result handling, interactive-terminal handling, and proposal
persistence.

Backwards compatibility is preserved at the service boundary via a thin
delegating wrapper in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from flask import current_app

from agent.common.utils.structured_action_utils import extract_structured_action_fields
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend
from agent.routes.tasks.orchestration_policy import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import build_trace_record, normalize_task_kind, runtime_routing_config
from agent.services.cli_session_service import get_cli_session_service
from agent.services.native_worker_runtime_service import get_native_worker_runtime_service
from agent.services.service_registry import get_core_services
from agent.utils import _extract_reason, _log_terminal_entry
from agent.services.worker_workspace_service import get_worker_workspace_service

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def propose_single_task_step(
    *,
    tid: str,
    task: dict,
    request_data,
    base_prompt: str,
    research_context: dict | None,
    cli_runner: Callable,
    cfg: dict,
    tool_definitions_resolver: Callable,
    allow_legacy_path: bool = False,
    resolve_requested_model: Callable,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
) -> "TaskScopedRouteResponse":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse
    from agent.services._task_scoped_citation import build_flow_metrics_payload, update_task_flow_metrics
    from agent.services._task_scoped_config_policy import (
        compact_research_context,
        native_worker_runtime_enabled,
        has_native_opencode_runtime,
        resolve_interactive_context_profile,
        resolve_interactive_propose_timeout,
        resolve_interactive_retry_timeout,
        resolve_opencode_execution_mode,
    )
    from agent.services._task_scoped_repair import build_llm_call_profile_entries, repair_task_proposal
    from agent.services._task_scoped_runtime import (
        build_research_result,
        build_review_state,
        prepare_task_cli_session,
        routing_dimensions,
        build_task_propose_prompt,
    )

    if not allow_legacy_path:
        raise NotImplementedError("FA-T003: legacy _propose_single_task_step is blocked for direct use.")
    task_kind = normalize_task_kind(None, base_prompt)
    required_capabilities = derive_required_capabilities(task, task_kind)
    research_specialization = derive_research_specialization(task, task_kind, required_capabilities)

    from agent.runtime_policy import resolve_cli_backend as _resolve_cli_backend_fn
    from agent.common.sgpt import SUPPORTED_CLI_BACKENDS
    _cfg_for_backend = cfg if isinstance(cfg, dict) else (current_app.config.get("AGENT_CONFIG", {}) or {})
    effective_backend, routing_reason = (lambda: (lambda r: (r[0], r[1]))(_resolve_cli_backend_fn(
        task_kind=task_kind,
        requested_backend=None,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=_cfg_for_backend,
        fallback_backend="sgpt",
        required_capabilities=required_capabilities,
    )))()

    workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
    # resolve_task_propose_timeout logic (static, duplicated here to avoid circular)
    task_kind_policies = cfg.get("task_kind_execution_policies") if isinstance(cfg.get("task_kind_execution_policies"), dict) else {}
    task_kind_cfg = task_kind_policies.get(task_kind) if isinstance(task_kind_policies.get(task_kind), dict) else {}
    general_timeout = int(cfg.get("command_timeout", 60) or 60)
    kind_timeout = int(task_kind_cfg.get("command_timeout") or 0)
    proposal_timeout = int(cfg.get("task_propose_timeout_seconds") or 0)
    timeout = max(60, general_timeout, kind_timeout, proposal_timeout)

    proposal_model = resolve_requested_model(
        agent_cfg=cfg,
        requested_model=getattr(request_data, "model", None),
    )
    policy_version = runtime_routing_config(cfg)["policy_version"] if isinstance(cfg, dict) else runtime_routing_config({})["policy_version"]
    session_payload = prepare_task_cli_session(
        tid=tid,
        task=task,
        backend=effective_backend,
        model=proposal_model,
        agent_cfg=cfg,
    )

    def _is_interactive_terminal_session(sp: dict | None) -> bool:
        if not isinstance(sp, dict):
            return False
        session_metadata = sp.get("metadata") if isinstance(sp.get("metadata"), dict) else {}
        execution_mode = str(session_metadata.get("opencode_execution_mode") or "").strip().lower()
        return execution_mode == "interactive_terminal"

    def _interactive_timeout_like_failure(*, rc: int, output: str, stderr: str) -> bool:
        if rc != 0 and not str(output or "").strip():
            return True
        timeout_markers = ("timeout", "timed out", "operation timed out", "session terminated")
        combined = (str(output or "") + " " + str(stderr or "")).lower()
        return any(marker in combined for marker in timeout_markers)

    interactive_terminal_session = effective_backend == "opencode" and _is_interactive_terminal_session(session_payload)
    interactive_context_profile = (
        resolve_interactive_context_profile(cfg, retry=False) if interactive_terminal_session else None
    )
    effective_research_context = (
        compact_research_context(research_context, profile=interactive_context_profile)
        if interactive_terminal_session
        else research_context
    )
    if interactive_terminal_session:
        timeout = resolve_interactive_propose_timeout(cfg, fallback=timeout)
    prompt_for_cli, worker_context_meta = build_task_propose_prompt(
        tid=tid,
        task=task,
        base_prompt=base_prompt,
        tool_definitions_resolver=(lambda *_args, **_kwargs: [])
        if interactive_terminal_session
        else tool_definitions_resolver,
        research_context=effective_research_context,
        interactive_terminal=interactive_terminal_session,
        context_profile=interactive_context_profile,
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
    from agent.services._task_scoped_config_policy import normalize_temperature
    requested_temperature = normalize_temperature(getattr(request_data, "temperature", None))
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
    if session_payload and not has_native_opencode_runtime(session_payload) and not interactive_terminal_session:
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
    if effective_research_context:
        cli_kwargs["research_context"] = effective_research_context
    rc, cli_out, cli_err, backend_used = invoke_cli_runner(cli_runner, **cli_kwargs)
    latency_ms = int((time.time() - started_at) * 1000)
    raw_res, output_source = coalesce_cli_output(cli_out, cli_err)
    repair_meta = {"attempted": False, "backend": None, "model": None}
    interactive_retry_meta = {"attempted": False, "timeout": None, "latency_ms": None}
    append_stage(
        pipeline,
        name="execute",
        status=(
            "ok"
            if (rc == 0 if interactive_terminal_session else (rc == 0 or bool(raw_res)))
            else "error"
        ),
        metadata={
            "backend_used": backend_used,
            "returncode": rc,
            "latency_ms": latency_ms,
            "output_source": output_source,
        },
        started_at=started_at,
    )
    if interactive_terminal_session and backend_used == "opencode":
        timeout_like_failure = _interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
        if timeout_like_failure:
            retry_profile = resolve_interactive_context_profile(cfg, retry=True)
            retry_research_context = compact_research_context(research_context, profile=retry_profile)
            retry_prompt, retry_worker_meta = build_task_propose_prompt(
                tid=tid,
                task=task,
                base_prompt=base_prompt,
                tool_definitions_resolver=(lambda *_args, **_kwargs: []),
                research_context=retry_research_context,
                interactive_terminal=True,
                context_profile=retry_profile,
            )
            if requested_temperature is not None:
                retry_prompt = (
                    f"{retry_prompt}\n\n"
                    f"[Sampling-Hinweis]\n"
                    f"Ziel-Temperatur fuer diese Antwort: {requested_temperature:.2f}\n"
                    "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
                )
            retry_timeout = resolve_interactive_retry_timeout(cfg, fallback=timeout)
            retry_kwargs = {
                **cli_kwargs,
                "prompt": retry_prompt,
                "timeout": retry_timeout,
            }
            if retry_research_context:
                retry_kwargs["research_context"] = retry_research_context
            started_retry = time.time()
            retry_rc, retry_out, retry_err, retry_backend = invoke_cli_runner(cli_runner, **retry_kwargs)
            retry_latency_ms = int((time.time() - started_retry) * 1000)
            retry_raw, retry_source = coalesce_cli_output(retry_out, retry_err)
            interactive_retry_meta = {
                "attempted": True,
                "timeout": retry_timeout,
                "latency_ms": retry_latency_ms,
            }
            append_stage(
                pipeline,
                name="interactive_retry",
                status="ok" if retry_rc == 0 else "error",
                metadata={
                    "backend_used": retry_backend,
                    "returncode": retry_rc,
                    "latency_ms": retry_latency_ms,
                    "output_source": retry_source,
                    "timeout": retry_timeout,
                },
                started_at=started_retry,
            )
            rc = retry_rc
            cli_err = retry_err
            cli_out = retry_out
            raw_res = retry_raw
            output_source = retry_source
            backend_used = retry_backend
            latency_ms += retry_latency_ms
            prompt_for_cli = retry_prompt
            worker_context_meta = retry_worker_meta
            effective_research_context = retry_research_context
    if not interactive_terminal_session and rc != 0 and not raw_res.strip():
        repaired = repair_task_proposal(
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
            research_context=effective_research_context,
            session=session_payload,
            workdir=str(workspace_context.workspace_dir),
            invoke_cli_runner=invoke_cli_runner,
            coalesce_cli_output=coalesce_cli_output,
            normalize_temperature=normalize_temperature,
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
        repaired = repair_task_proposal(
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
            research_context=effective_research_context,
            session=session_payload,
            workdir=str(workspace_context.workspace_dir),
            invoke_cli_runner=invoke_cli_runner,
            coalesce_cli_output=coalesce_cli_output,
            normalize_temperature=normalize_temperature,
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
        "policy_classification_summary": str(routing_reason or "").strip().lower() or None,
        "required_capabilities": required_capabilities,
        "research_specialization": research_specialization,
        **routing_dimensions(
            backend_used=backend_used,
            model=proposal_model,
            temperature=requested_temperature,
            requested_backend="auto",
            agent_cfg=cfg,
            worker_profile=worker_context_meta.get("worker_profile"),
            profile_source=worker_context_meta.get("profile_source"),
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
        config_execution_mode = resolve_opencode_execution_mode(cfg)
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
    if interactive_terminal_session and backend_used == "opencode":
        timeout_like_failure = _interactive_timeout_like_failure(rc=rc, output=raw_res, stderr=cli_err)
        if rc != 0 or timeout_like_failure:
            flow_metrics = build_flow_metrics_payload(
                run_id=str(session_payload.get("id") or "") if isinstance(session_payload, dict) else None,
                phase="propose",
                propose_ok=False,
                execute_ok=None,
                artifact_created=None,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
                policy_classification=str(routing_reason or "").strip().lower() or None,
            )
            update_task_flow_metrics(tid=tid, task=task, flow_metrics=flow_metrics)
            return TaskScopedRouteResponse(
                status="error",
                message="llm_cli_failed",
                data={
                    "details": cli_err or raw_res or f"backend '{backend_used}' failed with exit code {rc}",
                    "backend": backend_used,
                    "flow_metrics": flow_metrics,
                    "retry": interactive_retry_meta,
                },
                code=502,
            )
    if is_research_backend(backend_used):
        research_res = build_research_result(
            raw_res=raw_res,
            backend_used=backend_used,
            tid=tid,
            rc=rc,
            cli_err=cli_err,
            latency_ms=latency_ms,
            output_source=output_source,
            research_context=effective_research_context,
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
            review=build_review_state(
                current_app.config.get("AGENT_CONFIG", {}) or {},
                backend_used,
                task_kind,
                command=None,
                tool_calls=None,
            ),
            pipeline=pipeline_payload,
            research_artifact=research_res.get("research_artifact"),
            research_context=effective_research_context,
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
                "flow_metrics": build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(routing_reason or "").strip().lower() or None,
                ),
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
            "repair_attempted": bool(interactive_retry_meta.get("attempted")),
            "repair_backend": backend_used if interactive_retry_meta.get("attempted") else None,
            "repair_model": proposal_model if interactive_retry_meta.get("attempted") else None,
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
            review=build_review_state(
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
                "flow_metrics": build_flow_metrics_payload(
                    run_id=str(trace.get("trace_id") or ""),
                    phase="propose",
                    propose_ok=True,
                    execute_ok=None,
                    artifact_created=None,
                    worker_profile=worker_context_meta.get("worker_profile"),
                    profile_source=worker_context_meta.get("profile_source"),
                    policy_classification=str(routing_reason or "").strip().lower() or None,
                ),
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
    command, tool_calls = extract_structured_action_fields(raw_res)
    if not command and not tool_calls:
        repaired = repair_task_proposal(
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
            research_context=effective_research_context,
            session=session_payload,
            workdir=str(workspace_context.workspace_dir),
            invoke_cli_runner=invoke_cli_runner,
            coalesce_cli_output=coalesce_cli_output,
            normalize_temperature=normalize_temperature,
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
            command, tool_calls = extract_structured_action_fields(raw_res)
    policy_classification_summary = str(routing_reason or "").strip().lower() or None
    if backend_used == "ananta-worker" and native_worker_runtime_enabled(cfg):
        native_plan = get_native_worker_runtime_service().prepare_native_command_plan(
            tid=tid,
            task=task,
            command=command,
            reason=reason,
            worker_profile=worker_context_meta.get("worker_profile"),
            profile_source=worker_context_meta.get("profile_source"),
            trace_id=str((session_payload or {}).get("id") or ""),
            context_bundle_id=worker_context_meta.get("context_bundle_id"),
            agent_cfg=cfg,
        )
        worker_context_meta.update(dict(native_plan.get("worker_context_updates") or {}))
        runtime_path = str(native_plan.get("runtime_path") or "").strip().lower()
        if runtime_path:
            routing["worker_runtime_path"] = runtime_path
        policy_classification_summary = str(native_plan.get("policy_classification_summary") or policy_classification_summary or "").strip().lower() or None
        if policy_classification_summary:
            routing["policy_classification_summary"] = policy_classification_summary
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
            "llm_call_profile": build_llm_call_profile_entries(
                backend_used=backend_used,
                model=proposal_model,
                prompt=prompt_for_cli,
                raw_output=raw_res,
                latency_ms=latency_ms,
                rc=rc,
                repair_attempted=bool(repair_meta["attempted"]),
                repair_backend=repair_meta["backend"],
                repair_model=repair_meta["model"],
            ),
        },
        worker_context=worker_context_meta,
        trace=trace,
        review=build_review_state(
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
            "flow_metrics": build_flow_metrics_payload(
                run_id=str(trace.get("trace_id") or ""),
                phase="propose",
                propose_ok=True,
                execute_ok=None,
                artifact_created=None,
                worker_profile=worker_context_meta.get("worker_profile"),
                profile_source=worker_context_meta.get("profile_source"),
                policy_classification=policy_classification_summary,
            ),
        },
    )
    if session_payload:
        turn = get_cli_session_service().append_turn(
            session_id=session_payload["id"],
            prompt=prompt_for_cli,
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
