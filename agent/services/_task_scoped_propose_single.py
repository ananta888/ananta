"""Single-backend task propose path for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
propose_single cluster of SPLIT-001 (sub-split 001m). The module owns the full
propose flow for a single CLI backend: session prep, prompt build, CLI invocation,
repair, research-result handling, interactive-terminal handling, and proposal
persistence. Per-run state and collaborators live in
``_task_scoped_propose_run``; proposal persistence lives in
``_task_scoped_propose_persist``.

Backwards compatibility is preserved at the service boundary via a thin
delegating wrapper in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from flask import current_app

from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend
from agent.services.worker_routing_policy_utils import derive_required_capabilities, derive_research_specialization
from agent.runtime_policy import normalize_task_kind, runtime_routing_config
from agent.services.cli_session_service import get_cli_session_service
from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.services._task_scoped_propose_persist import (
    persist_command_proposal,
    persist_interactive_finalize_proposal,
    persist_research_proposal,
)
from agent.services._task_scoped_propose_run import (
    INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
    ProposeCollaborators,
    ProposeRun,
    propose_flow_metrics,
    repair_proposal,
)

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = INTERACTIVE_TERMINAL_FINALIZE_COMMAND
_INTERACTIVE_TIMEOUT_MARKERS = ("timeout", "timed out", "operation timed out", "session terminated")


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
    deps = ProposeCollaborators.load()
    if not allow_legacy_path:
        raise NotImplementedError("FA-T003: legacy _propose_single_task_step is blocked for direct use.")
    run = _start_propose_run(
        tid=tid,
        task=task,
        request_data=request_data,
        base_prompt=base_prompt,
        research_context=research_context,
        cli_runner=cli_runner,
        cfg=cfg,
        resolve_requested_model=resolve_requested_model,
        invoke_cli_runner=invoke_cli_runner,
        coalesce_cli_output=coalesce_cli_output,
        deps=deps,
    )
    _prepare_prompt(run, request_data=request_data, tool_definitions_resolver=tool_definitions_resolver)
    cli_kwargs = _invoke_primary_cli(run)
    if run.interactive_opencode and _interactive_timeout_like_failure(
        rc=run.rc, output=run.raw_res, stderr=run.cli_err
    ):
        _run_interactive_retry(run, cli_kwargs)
    failure = _repair_empty_cli_response(run)
    if failure is not None:
        return failure
    routing = _build_routing(run)
    failure = _interactive_failure_response(run)
    if failure is not None:
        return failure
    if is_research_backend(run.backend_used):
        return persist_research_proposal(run, routing)
    if run.interactive_opencode:
        return persist_interactive_finalize_proposal(run, routing)
    return persist_command_proposal(run, routing)


def _start_propose_run(
    *,
    tid: str,
    task: dict,
    request_data,
    base_prompt: str,
    research_context: dict | None,
    cli_runner: Callable,
    cfg: dict,
    resolve_requested_model: Callable,
    invoke_cli_runner: Callable,
    coalesce_cli_output: Callable,
    deps: ProposeCollaborators,
) -> ProposeRun:
    task_kind = normalize_task_kind(None, base_prompt)
    required_capabilities = derive_required_capabilities(task, task_kind)
    research_specialization = derive_research_specialization(task, task_kind, required_capabilities)
    effective_backend, routing_reason = _resolve_effective_backend(cfg, task_kind, required_capabilities)
    workspace_context = get_worker_workspace_service().resolve_workspace_context(task=task)
    timeout = _task_propose_timeout(cfg, task_kind)
    proposal_model = resolve_requested_model(
        agent_cfg=cfg,
        requested_model=getattr(request_data, "model", None),
    )
    policy_version = (
        runtime_routing_config(cfg)["policy_version"]
        if isinstance(cfg, dict)
        else runtime_routing_config({})["policy_version"]
    )
    session_payload = deps.prepare_task_cli_session(
        tid=tid,
        task=task,
        backend=effective_backend,
        model=proposal_model,
        agent_cfg=cfg,
    )
    return ProposeRun(
        tid=tid,
        task=task,
        cfg=cfg,
        base_prompt=base_prompt,
        research_context=research_context,
        cli_runner=cli_runner,
        invoke_cli_runner=invoke_cli_runner,
        coalesce_cli_output=coalesce_cli_output,
        deps=deps,
        task_kind=task_kind,
        required_capabilities=required_capabilities,
        research_specialization=research_specialization,
        effective_backend=effective_backend,
        routing_reason=routing_reason,
        workspace_dir=str(workspace_context.workspace_dir),
        timeout=timeout,
        proposal_model=proposal_model,
        policy_version=policy_version,
        session_payload=session_payload,
        interactive_terminal_session=(
            effective_backend == "opencode" and _is_interactive_terminal_session(session_payload)
        ),
    )


def _resolve_effective_backend(cfg: dict, task_kind: str, required_capabilities) -> tuple[str, Any]:
    from agent.runtime_policy import resolve_cli_backend as _resolve_cli_backend_fn
    from agent.cli_backends.sgpt import SUPPORTED_CLI_BACKENDS

    _cfg_for_backend = cfg if isinstance(cfg, dict) else (current_app.config.get("AGENT_CONFIG", {}) or {})
    resolved = _resolve_cli_backend_fn(
        task_kind=task_kind,
        requested_backend=None,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=_cfg_for_backend,
        fallback_backend="sgpt",
        required_capabilities=required_capabilities,
    )
    return resolved[0], resolved[1]


def _task_propose_timeout(cfg: dict, task_kind: str) -> int:
    # resolve_task_propose_timeout logic (static, duplicated here to avoid circular)
    task_kind_policies = (
        cfg.get("task_kind_execution_policies") if isinstance(cfg.get("task_kind_execution_policies"), dict) else {}
    )
    task_kind_cfg = task_kind_policies.get(task_kind) if isinstance(task_kind_policies.get(task_kind), dict) else {}
    general_timeout = int(cfg.get("command_timeout", 60) or 60)
    kind_timeout = int(task_kind_cfg.get("command_timeout") or 0)
    proposal_timeout = int(cfg.get("task_propose_timeout_seconds") or 0)
    return max(60, general_timeout, kind_timeout, proposal_timeout)


def _is_interactive_terminal_session(sp: dict | None) -> bool:
    if not isinstance(sp, dict):
        return False
    session_metadata = sp.get("metadata") if isinstance(sp.get("metadata"), dict) else {}
    execution_mode = str(session_metadata.get("opencode_execution_mode") or "").strip().lower()
    return execution_mode == "interactive_terminal"


def _interactive_timeout_like_failure(*, rc: int, output: str, stderr: str) -> bool:
    if rc != 0 and not str(output or "").strip():
        return True
    combined = (str(output or "") + " " + str(stderr or "")).lower()
    return any(marker in combined for marker in _INTERACTIVE_TIMEOUT_MARKERS)


def _no_tool_definitions(*_args, **_kwargs) -> list:
    return []


def _prepare_prompt(run: ProposeRun, *, request_data, tool_definitions_resolver: Callable) -> None:
    deps, cfg = run.deps, run.cfg
    interactive = run.interactive_terminal_session
    interactive_context_profile = deps.resolve_interactive_context_profile(cfg, retry=False) if interactive else None
    run.effective_research_context = (
        deps.compact_research_context(run.research_context, profile=interactive_context_profile)
        if interactive
        else run.research_context
    )
    if interactive:
        run.timeout = deps.resolve_interactive_propose_timeout(cfg, fallback=run.timeout)
    run.prompt_for_cli, run.worker_context_meta = deps.build_task_propose_prompt(
        tid=run.tid,
        task=run.task,
        base_prompt=run.base_prompt,
        tool_definitions_resolver=_no_tool_definitions if interactive else tool_definitions_resolver,
        research_context=run.effective_research_context,
        interactive_terminal=interactive,
        context_profile=interactive_context_profile,
    )
    run.pipeline = new_pipeline_trace(
        pipeline="task_propose",
        task_kind=run.task_kind,
        policy_version=run.policy_version,
        metadata={"task_id": run.tid, "requested_backend": "auto", **run.worker_context_meta},
    )
    append_stage(
        run.pipeline,
        name="route",
        status="ok",
        metadata={"effective_backend": run.effective_backend, "reason": run.routing_reason},
    )
    run.requested_temperature = deps.normalize_temperature(getattr(request_data, "temperature", None))
    if run.requested_temperature is not None:
        run.prompt_for_cli = _with_sampling_hint(
            run.prompt_for_cli, run.requested_temperature, interactive_terminal=interactive
        )
    session_payload = run.session_payload
    if session_payload and not deps.has_native_opencode_runtime(session_payload) and not interactive:
        run.prompt_for_cli = (
            get_cli_session_service().build_prompt_with_history(
                session_id=session_payload["id"],
                prompt=run.prompt_for_cli,
                max_turns=int(session_payload.get("max_turns_per_session") or 40),
            )
            or run.prompt_for_cli
        )


def _with_sampling_hint(prompt: str, temperature: float, *, interactive_terminal: bool) -> str:
    return (
        f"{prompt}\n\n"
        f"[Sampling-Hinweis]\n"
        f"Ziel-Temperatur fuer diese Antwort: {temperature:.2f}\n"
        + (
            "Arbeite im sichtbaren OpenCode-Terminal direkt im Workspace."
            if interactive_terminal
            else "Behalte strikt das JSON-Output-Schema ein."
        )
    )


def _invoke_primary_cli(run: ProposeRun) -> dict:
    started_at = time.time()
    cli_kwargs = {
        "prompt": run.prompt_for_cli,
        "options": ["--no-interaction"],
        "timeout": run.timeout,
        "backend": run.effective_backend,
        "model": run.proposal_model,
        "routing_policy": {"mode": "adaptive", "task_kind": run.task_kind, "policy_version": run.policy_version},
        "session": run.session_payload,
        "workdir": run.workspace_dir,
    }
    if run.requested_temperature is not None:
        cli_kwargs["temperature"] = run.requested_temperature
    if run.effective_research_context:
        cli_kwargs["research_context"] = run.effective_research_context
    run.rc, run.cli_out, run.cli_err, run.backend_used = run.invoke_cli_runner(run.cli_runner, **cli_kwargs)
    run.latency_ms = int((time.time() - started_at) * 1000)
    run.raw_res, run.output_source = run.coalesce_cli_output(run.cli_out, run.cli_err)
    execute_ok = run.rc == 0 if run.interactive_terminal_session else (run.rc == 0 or bool(run.raw_res))
    append_stage(
        run.pipeline,
        name="execute",
        status="ok" if execute_ok else "error",
        metadata={
            "backend_used": run.backend_used,
            "returncode": run.rc,
            "latency_ms": run.latency_ms,
            "output_source": run.output_source,
        },
        started_at=started_at,
    )
    return cli_kwargs


def _run_interactive_retry(run: ProposeRun, cli_kwargs: dict) -> None:
    """Retry a timed-out interactive OpenCode session once with the compact retry context profile."""
    deps, cfg = run.deps, run.cfg
    retry_profile = deps.resolve_interactive_context_profile(cfg, retry=True)
    retry_research_context = deps.compact_research_context(run.research_context, profile=retry_profile)
    retry_prompt, retry_worker_meta = deps.build_task_propose_prompt(
        tid=run.tid,
        task=run.task,
        base_prompt=run.base_prompt,
        tool_definitions_resolver=_no_tool_definitions,
        research_context=retry_research_context,
        interactive_terminal=True,
        context_profile=retry_profile,
    )
    if run.requested_temperature is not None:
        retry_prompt = _with_sampling_hint(retry_prompt, run.requested_temperature, interactive_terminal=True)
    retry_timeout = deps.resolve_interactive_retry_timeout(cfg, fallback=run.timeout)
    retry_kwargs = {
        **cli_kwargs,
        "prompt": retry_prompt,
        "timeout": retry_timeout,
    }
    if retry_research_context:
        retry_kwargs["research_context"] = retry_research_context
    started_retry = time.time()
    retry_rc, retry_out, retry_err, retry_backend = run.invoke_cli_runner(run.cli_runner, **retry_kwargs)
    retry_latency_ms = int((time.time() - started_retry) * 1000)
    retry_raw, retry_source = run.coalesce_cli_output(retry_out, retry_err)
    run.interactive_retry_meta = {
        "attempted": True,
        "timeout": retry_timeout,
        "latency_ms": retry_latency_ms,
    }
    append_stage(
        run.pipeline,
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
    run.rc = retry_rc
    run.cli_err = retry_err
    run.cli_out = retry_out
    run.raw_res = retry_raw
    run.output_source = retry_source
    run.backend_used = retry_backend
    run.latency_ms += retry_latency_ms
    run.prompt_for_cli = retry_prompt
    run.worker_context_meta = retry_worker_meta
    run.effective_research_context = retry_research_context


def _repair_empty_cli_response(run: ProposeRun) -> Any:
    """Repair failed/empty non-interactive CLI output; return an error response when repair fails."""
    response_type = run.deps.route_response
    if not run.interactive_terminal_session and run.rc != 0 and not run.raw_res.strip():
        repaired = repair_proposal(
            run, bad_output=(run.cli_err or ""), validation_error="empty_or_failed_cli_response"
        )
        if not repaired:
            return response_type(
                status="error",
                message="llm_cli_failed",
                data={
                    "details": run.cli_err or f"backend '{run.backend_used}' failed with exit code {run.rc}",
                    "backend": run.backend_used,
                },
                code=502,
            )
        run.apply_repair(repaired)
    if not run.interactive_terminal_session and not run.raw_res:
        repaired = repair_proposal(run, bad_output=(run.cli_err or ""), validation_error="empty_cli_response")
        if not repaired:
            return response_type(status="error", message="llm_failed", data={}, code=502)
        run.apply_repair(repaired)
    return None


def _build_routing(run: ProposeRun) -> dict:
    routing = {
        "task_kind": run.task_kind,
        "effective_backend": run.effective_backend,
        "reason": run.routing_reason,
        "policy_classification_summary": run.policy_classification,
        "required_capabilities": run.required_capabilities,
        "research_specialization": run.research_specialization,
        **run.deps.routing_dimensions(
            backend_used=run.backend_used,
            model=run.proposal_model,
            temperature=run.requested_temperature,
            requested_backend="auto",
            agent_cfg=run.cfg,
            worker_profile=run.worker_context_meta.get("worker_profile"),
            profile_source=run.worker_context_meta.get("profile_source"),
        ),
    }
    if run.session_payload:
        _add_session_routing(run, routing)
    return routing


def _add_session_routing(run: ProposeRun, routing: dict) -> None:
    session_payload = run.session_payload
    routing["session_mode"] = "stateful"
    routing["session_id"] = session_payload["id"]
    routing["session_reused"] = bool(session_payload.get("session_reused"))
    session_metadata = session_payload.get("metadata") if isinstance(session_payload.get("metadata"), dict) else {}
    live_terminal_meta = (
        dict(session_metadata.get("opencode_live_terminal") or {})
        if isinstance(session_metadata.get("opencode_live_terminal"), dict)
        else {}
    )
    config_execution_mode = run.deps.resolve_opencode_execution_mode(run.cfg)
    terminal_modes = {"live_terminal", "interactive_terminal"}
    session_execution_mode = str(session_metadata.get("opencode_execution_mode") or "").strip().lower()
    if not (
        session_execution_mode in terminal_modes
        or (run.effective_backend == "opencode" and config_execution_mode in terminal_modes)
    ):
        return
    routing["execution_mode"] = (
        str(session_metadata.get("opencode_execution_mode") or config_execution_mode).strip().lower()
    )
    if not live_terminal_meta:
        verification_status = run.task.get("verification_status") or {}
        live_terminal_meta = (
            dict(verification_status.get("opencode_live_terminal") or {})
            if isinstance(verification_status.get("opencode_live_terminal"), dict)
            else {}
        )
    routing["live_terminal"] = live_terminal_meta


def _interactive_failure_response(run: ProposeRun) -> Any:
    if not run.interactive_opencode:
        return None
    timeout_like_failure = _interactive_timeout_like_failure(rc=run.rc, output=run.raw_res, stderr=run.cli_err)
    if run.rc == 0 and not timeout_like_failure:
        return None
    flow_metrics = propose_flow_metrics(
        run,
        run_id=str(run.session_payload.get("id") or "") if isinstance(run.session_payload, dict) else None,
        propose_ok=False,
        policy_classification=run.policy_classification,
    )
    run.deps.update_task_flow_metrics(tid=run.tid, task=run.task, flow_metrics=flow_metrics)
    return run.deps.route_response(
        status="error",
        message="llm_cli_failed",
        data={
            "details": run.cli_err or run.raw_res or f"backend '{run.backend_used}' failed with exit code {run.rc}",
            "backend": run.backend_used,
            "flow_metrics": flow_metrics,
            "retry": run.interactive_retry_meta,
        },
        code=502,
    )
