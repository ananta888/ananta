"""Task-scoped propose step: admission, routing and response assembly.

Split out of ``_task_scoped_step_orchestrator`` (SRP). ``run_propose_step``
stays importable from ``_task_scoped_step_orchestrator``. Dispatch admission is
an explicit ``step_ports`` argument (:class:`StepOrchestrationPorts`); when
omitted it comes from the documented ``_task_scoped_forwarding_dependencies``
seam.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Callable

from flask import current_app, has_app_context

from agent.cli_backends.sgpt import SUPPORTED_CLI_BACKENDS
from agent.runtime_policy import normalize_task_kind
from agent.services._task_scoped_adapters import try_handler_propose
from agent.services._task_scoped_domain_action import propose_task_with_comparisons
from agent.services._task_scoped_propose_orch import run_propose_orchestrator_path
from agent.services._task_scoped_propose_single import propose_single_task_step
from agent.services._task_scoped_step_policies import (
    HANDLER_ONLY_TASK_KINDS,
    _apply_request_run_evidence_context,
    _cache_recovery_outcome,
    _cached_recovery_outcome,
    _dispatch_admission_error,
    _handler_only_unavailable,
    _knowledge_index_handler_unavailable,
    _organization_research_hub_execution_guard,
    _vector_index_domain_binding_error,
    _vector_index_handler_unavailable,
)
from agent.services.propose_policy import get_task_kind_preset
from agent.services.task_scoped_execution_service import (
    get_goal_config_runtime_service,
    get_research_context_bridge_service,
)
from agent.services.worker_execution_profile_service import resolve_worker_execution_profile
from agent.services.worker_routing_policy_utils import derive_required_capabilities

if TYPE_CHECKING:
    from agent.services._task_scoped_forwarding_dependencies import StepOrchestrationPorts


def _resolve_step_ports(
    step_ports: "StepOrchestrationPorts | None",
) -> "StepOrchestrationPorts":
    if step_ports is not None:
        return step_ports
    from agent.services._task_scoped_forwarding_dependencies import (
        current_task_scoped_forwarding_dependencies,
    )

    return current_task_scoped_forwarding_dependencies().steps


def run_propose_step(
    service,
    tid: str,
    request_data,
    *,
    cli_runner: Callable,
    forwarder: Callable,
    tool_definitions_resolver: Callable,
    step_ports: "StepOrchestrationPorts | None" = None,
):
    """Route a propose request to the appropriate strategy."""
    ports = _resolve_step_ports(step_ports)
    from worker.retrieval.knowledge_index_task_snapshot import (
        hydrate_knowledge_index_task_snapshot,
    )

    hydrate_knowledge_index_task_snapshot(
        task_id=tid,
        request_data=request_data,
        expected_phase="propose",
    )
    task = service._require_task(tid)
    if guard := _organization_research_hub_execution_guard(
        task=task,
        tid=tid,
        phase="propose",
    ):
        return guard
    if (
        vector_binding_error := _vector_index_domain_binding_error(task)
    ) is not None:
        return vector_binding_error
    admission = ports.admit_dispatch(
        tid=tid,
        task=task,
        request_data=request_data,
        phase="propose",
    )
    if not admission.get("request_fingerprint"):
        from agent.services.recovery_dispatch_gate_service import (
            recovery_dispatch_request_fingerprint,
        )

        admission["request_fingerprint"] = (
            recovery_dispatch_request_fingerprint(
                "propose",
                request_data,
            )
        )
    if admission["error"] is not None:
        return admission["error"]
    if admission["replayed"]:
        cached = _cached_recovery_outcome(
            getattr(request_data, "dispatch_lease_token", None),
            task_id=tid,
            phase="propose",
            request_fingerprint=admission[
                "request_fingerprint"
            ],
        )
        if cached is not None:
            return cached
        from agent.services.recovery_dispatch_gate_service import (
            RecoveryDispatchGateDecision,
        )

        return _dispatch_admission_error(
            tid=tid,
            phase="propose",
            decision=RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_invocation_in_progress",
            ),
        )
    from agent.config import settings

    defer_local_writes = bool(admission["token"]) and (
        admission["guard_local_result"]
        or str(settings.role or "").strip().lower() != "hub"
    )
    boundary = contextlib.nullcontext()
    if defer_local_writes:
        from agent.services.recovery_result_write_boundary import (
            defer_recovery_task_writes,
        )

        boundary = defer_recovery_task_writes(
            task_id=tid,
            phase="propose",
        )
    with boundary as deferred_boundary:
        outcome = _run_propose_step_admitted(
            service,
            tid,
            request_data,
            cli_runner=cli_runner,
            forwarder=forwarder,
            tool_definitions_resolver=tool_definitions_resolver,
        )
    if deferred_boundary is not None:
        from agent.services.recovery_worker_result_service import (
            get_recovery_worker_result_service,
        )

        get_recovery_worker_result_service().attach(
            boundary=deferred_boundary,
            response=outcome.data,
        )
    if admission["guard_local_result"]:
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
            recovery_dispatch_request_fingerprint,
        )

        with get_recovery_dispatch_gate_service().result_guard(
            tid,
            token=admission["token"],
            phase="propose",
            request_fingerprint=(
                recovery_dispatch_request_fingerprint(
                    "propose",
                    request_data,
                )
            ),
            worker_url=admission["worker_url"],
        ) as decision:
            if not decision.allowed:
                return _dispatch_admission_error(
                    tid=tid,
                    phase="propose",
                    decision=decision,
                )
            service._persist_forwarded_proposal(
                outcome.data,
                task,
                request_payload=request_data.model_dump(),
            )
    _cache_recovery_outcome(
        admission["token"],
        outcome,
        task_id=tid,
        phase="propose",
        request_fingerprint=admission["request_fingerprint"],
    )
    return outcome


def _run_propose_step_admitted(
    service,
    tid: str,
    request_data,
    *,
    cli_runner: Callable,
    forwarder: Callable,
    tool_definitions_resolver: Callable,
):
    """Execute an already admitted propose request."""

    task = service._require_task(tid)
    if guard := _organization_research_hub_execution_guard(
        task=task,
        tid=tid,
        phase="propose",
    ):
        return guard
    if (
        vector_binding_error := _vector_index_domain_binding_error(task)
    ) is not None:
        return vector_binding_error
    _apply_request_run_evidence_context(
        task=task,
        request_data=request_data,
    )
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
    run_evidence_context = dict(
        (task.get("status_reason_details") or {}).get(
            "recovery_tool_run_context"
        )
        or {}
    )
    citation_contract = service._render_citation_contract_prompt(
        source_catalog,
        run_evidence_context=run_evidence_context or None,
    )
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
    if task_kind == "vector_index_operation":
        handler_response = try_handler_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
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
        return _vector_index_handler_unavailable(
            task=task,
            phase="propose",
        )
    if task_kind == "codecompass_index_build":
        handler_response = try_handler_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
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
        return _knowledge_index_handler_unavailable(
            task=task,
            phase="propose",
        )
    if task_kind in HANDLER_ONLY_TASK_KINDS:
        # Deterministic Worker handlers: an LLM proposal (e.g. the autopilot's
        # model comparisons) must never replace them.
        handler_response = try_handler_propose(
            tid=tid,
            task=task,
            task_kind=task_kind,
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
        return _handler_only_unavailable(task=task, task_kind=task_kind, phase="propose")
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
    if (
        explicit_task_kind
        and task_kind in legacy_cli_task_kinds
        and not str(
            getattr(request_data, "strategy_mode", "") or ""
        ).strip()
    ):
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
