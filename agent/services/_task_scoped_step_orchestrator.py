"""Step-level propose/execute orchestration for task-scoped execution.

Extracted from ``agent.services.task_scoped_execution_service`` as sub-split
SPLIT-113. Owns the routing logic for ``propose_task_step`` and
``execute_task_step``: task lookup, forwarding guard, config resolution,
strategy dispatch, and response assembly.

Backwards compatibility preserved via thin delegating wrappers in
:class:`TaskScopedExecutionService` (12-month deprecation window).
"""

from __future__ import annotations

from typing import Callable

from flask import current_app, has_app_context

from agent.cli_backends.sgpt import SUPPORTED_CLI_BACKENDS
from agent.services.worker_routing_policy_utils import derive_required_capabilities
from agent.runtime_policy import normalize_task_kind
from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)
from agent.services.propose_policy import get_task_kind_preset
from agent.services._task_scoped_propose_single import propose_single_task_step
from agent.services._task_scoped_domain_action import (
    propose_task_with_comparisons,
    execute_domain_action,
    finalize_interactive_terminal_execution,
    execute_research_artifact,
    register_goal_artifact_outputs,
)
from agent.services._task_scoped_adapters import (
    try_handler_propose,
    try_handler_execute,
)
from agent.services._task_scoped_propose_orch import run_propose_orchestrator_path
from agent.services._task_scoped_execute_workspace import run_execute_workspace_path

# Import service getters through task_scoped_execution_service to preserve
# monkeypatch compatibility for tests that patch names on that module.
from agent.services.task_scoped_execution_service import (
    get_goal_config_runtime_service,
    get_research_context_bridge_service,
    get_core_services,
)


_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def run_propose_step(
    service,
    tid: str,
    request_data,
    *,
    cli_runner: Callable,
    forwarder: Callable,
    tool_definitions_resolver: Callable,
):
    """Route a propose request to the appropriate strategy."""
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    task = service._require_task(tid)
    terminal_guard = service._terminal_parent_goal_guard(tid=tid, task=task, phase="propose")
    if terminal_guard is not None:
        return terminal_guard
    forwarded = service._forward_task_request_if_remote(
        tid=tid,
        task=task,
        endpoint=f"/tasks/{tid}/step/propose",
        payload=request_data.model_dump(),
        forwarder=forwarder,
        on_success=lambda response, loaded_task: service._persist_forwarded_proposal(
            response,
            loaded_task,
            request_payload=request_data.model_dump(),
        ),
    )
    if forwarded is not None:
        return forwarded

    scoped_resolution = get_goal_config_runtime_service().get_effective_config(
        goal_id=str(task.get("goal_id") or "").strip() or None,
        task_id=tid,
    )
    base_cfg = {}
    if has_app_context():
        base_cfg = dict((current_app.config.get("AGENT_CONFIG") or {}))
    scoped_cfg = dict(scoped_resolution.config or {})
    cfg = {**base_cfg, **{k: v for k, v in scoped_cfg.items() if v is not None}}
    base_prompt = request_data.prompt or task.get("description") or task.get("prompt") or f"Bearbeite Task {tid}"
    source_catalog = service._build_source_catalog_from_execution_context(
        tid=tid,
        task=task,
        llm_scope="local_only",
    )
    if not isinstance(source_catalog, dict):
        existing_source_catalog = dict((task.get("verification_status") or {}).get("source_catalog") or {})
        if existing_source_catalog:
            source_catalog = {
                "catalog_id": existing_source_catalog.get("source_catalog_id"),
                "catalog_hash": existing_source_catalog.get("source_catalog_hash"),
                "sources": list(existing_source_catalog.get("sources") or []),
            }
    citation_contract = service._render_citation_contract_prompt(source_catalog)
    explicit_task_kind = str(task.get("task_kind") or "").strip().lower()
    task_kind = explicit_task_kind or normalize_task_kind(None, base_prompt)
    rc_input = getattr(request_data, "research_context", None)
    if rc_input is None:
        stored = dict((task or {}).get("worker_execution_context") or {}).get("research_context_input")
        if stored:
            rc_input = stored
    research_context_summary = get_research_context_bridge_service().build_context(
        task=task,
        research_context=rc_input,
        query=base_prompt,
    )
    if task_kind == "research" and not str(getattr(request_data, "strategy_mode", "") or "").strip():
        return propose_single_task_step(
            tid=tid,
            task=task,
            request_data=request_data,
            base_prompt=base_prompt,
            research_context=research_context_summary,
            cli_runner=cli_runner,
            cfg=cfg,
            tool_definitions_resolver=tool_definitions_resolver,
            allow_legacy_path=True,
            resolve_requested_model=service._resolve_requested_model,
            invoke_cli_runner=service._invoke_cli_runner,
            coalesce_cli_output=service._coalesce_cli_output,
        )
    explicit_task_kind = str(
        task.get("task_kind")
        or getattr(request_data, "task_kind", "")
        or ""
    ).strip().lower()
    legacy_cli_task_kinds = {
        "generic",
        "analysis",
        "coding",
        "implementation",
        "ops",
        "testing",
        "doc",
        "review",
    }
    if explicit_task_kind and task_kind in legacy_cli_task_kinds and not str(getattr(request_data, "strategy_mode", "") or "").strip():
        routed_backend, _routing_reason = service._resolve_cli_backend(
            task_kind,
            requested_backend="auto",
            agent_cfg=cfg,
            required_capabilities=derive_required_capabilities(task, task_kind),
        )
        if routed_backend in SUPPORTED_CLI_BACKENDS:
            return propose_single_task_step(
                tid=tid,
                task=task,
                request_data=request_data,
                base_prompt=base_prompt,
                research_context=research_context_summary,
                cli_runner=cli_runner,
                cfg=cfg,
                tool_definitions_resolver=tool_definitions_resolver,
                allow_legacy_path=True,
                resolve_requested_model=service._resolve_requested_model,
                invoke_cli_runner=service._invoke_cli_runner,
                coalesce_cli_output=service._coalesce_cli_output,
            )
    strategy_mode = str(getattr(request_data, "strategy_mode", "") or "").strip().lower()
    if not strategy_mode:
        if list(getattr(request_data, "providers", None) or []):
            worker_profile, profile_source = resolve_worker_execution_profile(
                worker_execution_context=(task.get("worker_execution_context") or {}),
                agent_cfg=cfg,
            )
            return propose_task_with_comparisons(
                tid=tid,
                task=task,
                request_data=request_data,
                prompt=base_prompt,
                base_prompt=base_prompt,
                worker_context_meta={
                    "worker_profile": worker_profile,
                    "profile_source": profile_source,
                },
                research_context=research_context_summary,
                cli_runner=cli_runner,
                cfg=cfg,
                resolve_requested_model=service._resolve_requested_model,
                invoke_cli_runner=service._invoke_cli_runner,
                coalesce_cli_output=service._coalesce_cli_output,
                resolve_task_propose_timeout=service._resolve_task_propose_timeout,
            )
        if explicit_task_kind and not get_task_kind_preset(explicit_task_kind):
            handler_response = try_handler_propose(
                tid=tid,
                task=task,
                task_kind=explicit_task_kind,
                request_data=request_data,
                base_prompt=base_prompt,
                cli_runner=cli_runner,
                forwarder=forwarder,
                tool_definitions_resolver=tool_definitions_resolver,
                service=service,
                build_review_state=service._build_review_state,
            )
            if handler_response is not None:
                return handler_response
        legacy_enabled = bool(((cfg.get("task_scoped_execution") or {}).get("allow_legacy_single_step_path", False)))
        if legacy_enabled and task_kind in legacy_cli_task_kinds and has_app_context():
            return propose_single_task_step(
                tid=tid,
                task=task,
                request_data=request_data,
                base_prompt=base_prompt,
                research_context=research_context_summary,
                cli_runner=cli_runner,
                cfg=cfg,
                tool_definitions_resolver=tool_definitions_resolver,
                allow_legacy_path=True,
                resolve_requested_model=service._resolve_requested_model,
                invoke_cli_runner=service._invoke_cli_runner,
                coalesce_cli_output=service._coalesce_cli_output,
            )
    return run_propose_orchestrator_path(
        tid=tid,
        task=task,
        request_data=request_data,
        base_prompt=base_prompt,
        research_context_summary=research_context_summary,
        task_kind=task_kind,
        citation_contract=citation_contract,
        cfg=cfg,
        source_catalog=source_catalog,
        scoped_resolution_source=scoped_resolution.source,
        cli_runner=cli_runner,
        tool_definitions_resolver=tool_definitions_resolver,
        resolve_cli_backend=service._resolve_cli_backend,
        resolve_task_propose_timeout=service._resolve_task_propose_timeout,
        invoke_cli_runner=service._invoke_cli_runner,
        coalesce_cli_output=service._coalesce_cli_output,
        get_system_prompt_for_task=service._get_system_prompt_for_task,
        allow_synthetic_llm_profile_fallback=service._allow_synthetic_llm_profile_fallback,
    )


def run_execute_step(
    service,
    tid: str,
    request_data,
    *,
    forwarder: Callable,
    cli_runner: Callable | None = None,
    tool_definitions_resolver: Callable | None = None,
):
    """Route an execute request to the appropriate strategy."""
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    task = service._require_task(tid)
    terminal_guard = service._terminal_parent_goal_guard(tid=tid, task=task, phase="execute")
    if terminal_guard is not None:
        return terminal_guard
    forwarded = service._forward_task_request_if_remote(
        tid=tid,
        task=task,
        endpoint=f"/tasks/{tid}/step/execute",
        payload=request_data.model_dump(),
        forwarder=forwarder,
        on_success=lambda response, loaded_task: service._persist_forwarded_execution(
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
    handler_response = try_handler_execute(
        tid=tid,
        task=task,
        task_kind=task_kind,
        request_data=request_data,
        forwarder=forwarder,
        service=service,
    )
    if handler_response is not None:
        return handler_response

    requested_backend = str(getattr(request_data, "requested_backend", None) or "").strip().lower()
    if requested_backend == "hermes":
        return TaskScopedRouteResponse(
            data={
                "status": "denied",
                "reason": "hermes_phase1_no_execute_mutation",
                "task_id": tid,
                "task_kind": task_kind,
                "backend": "hermes",
            },
            status="denied",
            message="Hermes cannot execute mutation tasks in phase 1",
            code=403,
        )

    scoped_resolution = get_goal_config_runtime_service().get_effective_config(
        goal_id=str(task.get("goal_id") or "").strip() or None,
        task_id=tid,
    )
    agent_cfg = dict(scoped_resolution.config or {})
    execution_policy = get_core_services().task_execution_service.resolve_policy(
        request_data,
        agent_cfg=agent_cfg,
        source="task_execute",
    )

    command = request_data.command
    tool_calls = request_data.tool_calls
    reason = "Direkte Ausführung"
    used_last_proposal = False
    proposal_meta = dict(task.get("last_proposal") or {})
    proposal_routing = dict(proposal_meta.get("routing") or {})
    proposal_worker_context = dict(proposal_meta.get("worker_context") or {})
    worker_profile = normalize_worker_execution_profile(
        proposal_worker_context.get("worker_profile") or proposal_routing.get("worker_profile")
    )
    profile_source = str(
        proposal_worker_context.get("profile_source") or proposal_routing.get("profile_source") or "agent_default"
    ).strip().lower() or "agent_default"
    policy_classification_summary = str(
        proposal_routing.get("policy_classification_summary") or proposal_routing.get("reason") or ""
    ).strip().lower() or None

    if not command and not tool_calls:
        proposal = task.get("last_proposal")
        if not proposal:
            from agent.common.errors import TaskConflictError
            raise TaskConflictError("no_proposal")
        research_artifact = proposal.get("research_artifact") if isinstance(proposal, dict) else None
        if isinstance(research_artifact, dict):
            return execute_research_artifact(
                tid=tid,
                task=task,
                proposal=proposal,
                research_artifact=research_artifact,
                execution_policy=execution_policy,
            )
        try:
            from worker.core.propose import validate_executable_proposal
            command, tool_calls, _reason = validate_executable_proposal(proposal)
            reason = _reason or proposal.get("reason", "ExecutableProposal executed")
        except (ValueError, TypeError) as ve:
            return TaskScopedRouteResponse(
                data={
                    "status": "denied",
                    "reason": "invalid_executable_proposal_format",
                    "task_id": tid,
                    "proposal_preview": str(proposal)[:200],
                    "validation_errors": [str(ve)],
                },
                status="denied",
                message="ExecutableProposal validation failed",
                code=400,
            )
        used_last_proposal = True

    if task_kind == "domain_action":
        return execute_domain_action(
            tid=tid,
            task=task,
            task_kind=task_kind,
            request_data=request_data,
            command=command,
            reason=reason,
            execution_policy=execution_policy,
        )

    if command == _INTERACTIVE_TERMINAL_FINALIZE_COMMAND:
        return finalize_interactive_terminal_execution(
            tid=tid,
            task=task,
            reason=reason,
            execution_policy=execution_policy,
        )

    return run_execute_workspace_path(
        tid=tid,
        task=task,
        command=command,
        tool_calls=tool_calls,
        reason=reason,
        used_last_proposal=used_last_proposal,
        task_kind=task_kind,
        proposal_meta=proposal_meta,
        worker_profile=worker_profile,
        profile_source=profile_source,
        policy_classification_summary=policy_classification_summary,
        agent_cfg=agent_cfg,
        execution_policy=execution_policy,
        cli_runner=cli_runner,
        tool_definitions_resolver=tool_definitions_resolver,
        rewrite_runtime_command_for_workspace_tools=service._rewrite_runtime_command_for_workspace_tools,
        attempt_repaired_execute_after_meta_block=service._attempt_repaired_execute_after_meta_block,
        register_goal_artifact_outputs=service._register_goal_artifact_outputs,
    )
