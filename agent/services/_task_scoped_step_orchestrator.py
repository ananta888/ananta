"""Task-scoped propose/execute routing, forwarding, and response assembly.

Compatibility remains in thin :class:`TaskScopedExecutionService` wrappers.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable

from flask import current_app, has_app_context

from agent.cli_backends.sgpt import SUPPORTED_CLI_BACKENDS
from agent.runtime_policy import normalize_task_kind
from agent.services import _task_scoped_recovery_outcome_cache, _task_scoped_vector_step_policy
from agent.services._task_scoped_adapters import (
    try_handler_execute,
    try_handler_propose,
)
from agent.services._task_scoped_domain_action import (
    execute_domain_action,
    execute_research_artifact,
    finalize_interactive_terminal_execution,
    propose_task_with_comparisons,
)
from agent.services._task_scoped_execute_workspace import run_execute_workspace_path
from agent.services._task_scoped_propose_orch import run_propose_orchestrator_path
from agent.services._task_scoped_propose_single import propose_single_task_step
from agent.services.propose_policy import get_task_kind_preset

# Import service getters through task_scoped_execution_service to preserve
# monkeypatch compatibility for tests that patch names on that module.
from agent.services.task_scoped_execution_service import (
    get_core_services,
    get_goal_config_runtime_service,
    get_research_context_bridge_service,
)
from agent.services.worker_execution_profile_service import (
    normalize_worker_execution_profile,
    resolve_worker_execution_profile,
)
from agent.services.worker_routing_policy_utils import derive_required_capabilities

_RECOVERY_OUTCOME_CACHE = _task_scoped_recovery_outcome_cache.RECOVERY_OUTCOME_CACHE
_RECOVERY_OUTCOME_CACHE_LOCK = _task_scoped_recovery_outcome_cache.RECOVERY_OUTCOME_CACHE_LOCK
_cache_recovery_outcome = _task_scoped_recovery_outcome_cache.cache_recovery_outcome
_cached_recovery_outcome = _task_scoped_recovery_outcome_cache.cached_recovery_outcome
_recovery_cache_key = _task_scoped_recovery_outcome_cache.recovery_cache_key
_vector_index_domain_binding_error = _task_scoped_vector_step_policy.vector_index_domain_binding_error
_vector_index_handler_unavailable = _task_scoped_vector_step_policy.vector_index_handler_unavailable
_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def _organization_research_hub_execution_guard(
    *,
    task: dict[str, Any],
    tid: str,
    phase: str,
):
    """Keep authoritative Organization research on its secure dispatch path."""

    from agent.config import settings
    from agent.services.organization_task_dispatch_gate_service import (
        organization_research_requires_secure_delegation,
    )
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    if (
        str(settings.role or "").strip().lower() != "hub"
        or not organization_research_requires_secure_delegation(task)
    ):
        return None
    reason_code = "organization_research_secure_delegation_required"
    return TaskScopedRouteResponse(
        data={
            "status": "denied",
            "reason_code": reason_code,
            "task_id": tid,
            "phase": phase,
        },
        status="denied",
        message=reason_code,
        code=409,
    )


def _knowledge_index_handler_unavailable(
    *,
    task: dict[str, Any],
    phase: str,
):
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "unavailable",
            "reason_code": "knowledge_index_worker_handler_unavailable",
            "task_id": str(task.get("id") or ""),
            "task_kind": "codecompass_index_build",
            "phase": str(phase),
        },
        status="error",
        message="Knowledge index worker handler unavailable",
        code=503,
    )


def _publish_recovery_artifact_receipts(
    *,
    task: dict[str, Any],
    outcome: Any,
    token: str | None,
    request_fingerprint: str,
) -> Any:
    """Replace Worker-local artifact refs with idempotent Hub receipts."""

    from agent.config import settings

    data = getattr(outcome, "data", None)
    artifacts = (
        data.get("artifacts")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(artifacts, list) or not artifacts:
        return outcome
    already_materialized = all(
        isinstance(value, dict)
        and str(value.get("artifact_id") or "").startswith(
            "recovery-artifact-"
        )
        and dict(value.get("provenance_summary") or {}).get(
            "authority"
        )
        == "hub"
        for value in artifacts
    )
    if already_materialized:
        return outcome
    role = str(settings.role or "").strip().lower()
    if role == "hub":
        from agent.services.recovery_trusted_local_artifact_adapter import (
            get_recovery_trusted_local_artifact_adapter,
        )

        data["artifacts"] = (
            get_recovery_trusted_local_artifact_adapter().materialize(
                task=task,
                artifacts=list(artifacts),
                lease_token=str(token or ""),
                request_fingerprint=str(
                    request_fingerprint or ""
                ),
            )
        )
        return outcome
    from agent.services.recovery_worker_artifact_publisher import (
        get_recovery_worker_artifact_publisher,
    )

    data["artifacts"] = (
        get_recovery_worker_artifact_publisher().publish(
            task=task,
            artifacts=list(artifacts),
            lease_token=str(token or ""),
            request_fingerprint=str(
                request_fingerprint or ""
            ),
        )
    )
    return outcome


def _dispatch_admission_error(*, tid: str, phase: str, decision):
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "skipped",
            "reason": decision.reason_code,
            "task_id": tid,
            "phase": phase,
            "source_task_id": decision.source_task_id,
            "plan_id": decision.plan_id,
            "release_epoch": decision.release_epoch,
        },
        status="skipped",
        message="Recovery dispatch admission denied",
        code=409,
    )


def _apply_request_run_evidence_context(
    *,
    task: dict[str, Any],
    request_data: Any,
) -> None:
    """Apply a lease-validated context only to this request-local Task."""

    value = getattr(
        request_data,
        "recovery_run_evidence_context",
        None,
    )
    if value is None:
        return
    details = dict(task.get("status_reason_details") or {})
    details["recovery_tool_run_context"] = dict(value)
    task["status_reason_details"] = details


def _admit_task_scoped_dispatch(
    *,
    tid: str,
    task: dict,
    request_data,
    phase: str,
):
    """Issue on the Hub and consume on the Worker one recovery dispatch lease."""

    from agent.config import settings
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateDecision,
        get_recovery_dispatch_gate_service,
        recovery_dispatch_request_fingerprint,
    )

    gate = get_recovery_dispatch_gate_service()
    provided_token = str(
        getattr(request_data, "dispatch_lease_token", None) or ""
    ).strip()
    role = str(settings.role or "").strip().lower()
    recovery_child = gate.is_recovery_child(task)
    run_evidence_context = getattr(
        request_data,
        "recovery_run_evidence_context",
        None,
    )
    if recovery_child and role == "hub":
        from agent.services.recovery_hub_run_evidence_service import (
            RecoveryHubRunEvidenceError,
            get_recovery_hub_run_evidence_service,
        )

        try:
            request_data.recovery_run_evidence_context = (
                get_recovery_hub_run_evidence_service()
                .bind_request_context(
                    task=task,
                    value=run_evidence_context,
                )
            )
        except RecoveryHubRunEvidenceError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
    elif recovery_child and run_evidence_context is not None:
        from ananta_contracts.recovery_run_evidence import (
            RecoveryRunEvidenceContractError,
            validate_recovery_tool_run_context,
        )

        try:
            request_data.recovery_run_evidence_context = (
                validate_recovery_tool_run_context(
                    run_evidence_context,
                    task_id=tid,
                )
            )
        except RecoveryRunEvidenceContractError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
    elif not recovery_child and run_evidence_context is not None:
        decision = RecoveryDispatchGateDecision(
            False,
            "recovery_tool_run_context_unexpected",
        )
        return {
            "error": _dispatch_admission_error(
                tid=tid,
                phase=phase,
                decision=decision,
            ),
            "token": provided_token or None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": False,
            "request_fingerprint": "",
        }
    if (
        role == "hub"
        and str(phase or "").strip().lower() == "execute"
        and recovery_child
    ):
        from agent.services.recovery_worker_result_service import (
            RecoveryWorkerResultError,
            get_recovery_worker_result_service,
        )

        try:
            bound_context = (
                get_recovery_worker_result_service()
                .bind_execute_proposal_context(
                    task=task,
                    value=getattr(
                        request_data,
                        "recovery_proposal_context",
                        None,
                    ),
                )
            )
        except RecoveryWorkerResultError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
        request_data.recovery_proposal_context = bound_context
    request_fingerprint = recovery_dispatch_request_fingerprint(
        phase,
        request_data,
    )
    if role != "hub":
        decision = gate.admit_incoming_dispatch(
            task=task,
            token=provided_token or None,
            phase=phase,
            request_fingerprint=request_fingerprint,
        )
        return {
            "error": (
                None
                if decision.allowed
                else _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                )
            ),
            "token": provided_token or None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": (
                decision.reason_code
                == "recovery_dispatch_worker_readmitted"
            ),
            "request_fingerprint": request_fingerprint,
        }

    if provided_token:
        local_url = str(
            settings.agent_url or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        assigned_url = str(
            task.get("assigned_agent_url") or local_url
        ).strip().rstrip("/")
        decision = (
            gate.admit_dispatch_lease(
                tid,
                token=provided_token,
                phase=phase,
                worker_url=local_url,
                request_fingerprint=request_fingerprint,
                trusted_local=True,
            )
            if assigned_url == local_url
            else gate.validate_dispatch_lease(
                tid,
                token=provided_token,
                phase=phase,
                request_fingerprint=request_fingerprint,
            )
        )
        return {
            "error": (
                None
                if decision.allowed
                else _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                )
            ),
            "token": provided_token,
            "worker_url": assigned_url,
            "guard_local_result": assigned_url == local_url,
            "replayed": (
                decision.reason_code
                == "recovery_dispatch_worker_readmitted"
            ),
            "request_fingerprint": request_fingerprint,
        }
    if not gate.is_recovery_child(task):
        if gate.is_recovery_source(task):
            decision = gate.evaluate_task(task)
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": request_fingerprint,
            }
        return {
            "error": None,
            "token": None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": False,
            "request_fingerprint": request_fingerprint,
        }

    # Recovery capabilities are minted only by the Hub claim/dispatcher
    # path.  An authenticated API caller cannot create its own permit.
    return {
        "error": _dispatch_admission_error(
            tid=tid,
            phase=phase,
            decision=RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_missing",
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            ),
        ),
        "token": None,
        "worker_url": None,
        "guard_local_result": False,
        "replayed": False,
        "request_fingerprint": request_fingerprint,
    }


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
    admission = _admit_task_scoped_dispatch(
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
    from worker.retrieval.knowledge_index_task_snapshot import (
        hydrate_knowledge_index_task_snapshot,
    )

    hydrate_knowledge_index_task_snapshot(
        task_id=tid,
        request_data=request_data,
        expected_phase="execute",
    )
    task = service._require_task(tid)
    if guard := _organization_research_hub_execution_guard(
        task=task,
        tid=tid,
        phase="execute",
    ):
        return guard
    if (
        vector_binding_error := _vector_index_domain_binding_error(task)
    ) is not None:
        return vector_binding_error
    from agent.services.recovery_dispatch_gate_service import (
        get_recovery_dispatch_gate_service,
    )

    if (
        get_recovery_dispatch_gate_service().is_recovery_child(
            task
        )
        and not getattr(
            request_data,
            "recovery_proposal_context",
            None,
        )
    ):
        from agent.services.recovery_worker_result_service import (
            get_recovery_worker_result_service,
        )

        request_data.recovery_proposal_context = (
            get_recovery_worker_result_service()
            .proposal_context_for_task(tid)
        )
    admission = _admit_task_scoped_dispatch(
        tid=tid,
        task=task,
        request_data=request_data,
        phase="execute",
    )
    if not admission.get("request_fingerprint"):
        from agent.services.recovery_dispatch_gate_service import (
            recovery_dispatch_request_fingerprint,
        )

        admission["request_fingerprint"] = (
            recovery_dispatch_request_fingerprint(
                "execute",
                request_data,
            )
        )
    if admission["error"] is not None:
        return admission["error"]
    if admission["replayed"]:
        cached = _cached_recovery_outcome(
            getattr(request_data, "dispatch_lease_token", None),
            task_id=tid,
            phase="execute",
            request_fingerprint=admission[
                "request_fingerprint"
            ],
        )
        if cached is not None:
            cached = _publish_recovery_artifact_receipts(
                task=task,
                outcome=cached,
                token=admission["token"],
                request_fingerprint=admission[
                    "request_fingerprint"
                ],
            )
            _cache_recovery_outcome(
                admission["token"],
                cached,
                task_id=tid,
                phase="execute",
                request_fingerprint=admission[
                    "request_fingerprint"
                ],
            )
            return cached
        from agent.services.recovery_dispatch_gate_service import (
            RecoveryDispatchGateDecision,
        )

        return _dispatch_admission_error(
            tid=tid,
            phase="execute",
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
            phase="execute",
        )
    with boundary as deferred_boundary:
        outcome = _run_execute_step_admitted(
            service,
            tid,
            request_data,
            forwarder=forwarder,
            cli_runner=cli_runner,
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
        # Cache the completed Worker computation before the network ingress.
        # If the ingress outcome is unknown, a lease-bound replay can publish
        # this same artifact manifest again without re-executing the task.
        _cache_recovery_outcome(
            admission["token"],
            outcome,
            task_id=tid,
            phase="execute",
            request_fingerprint=admission[
                "request_fingerprint"
            ],
        )
        outcome = _publish_recovery_artifact_receipts(
            task=task,
            outcome=outcome,
            token=admission["token"],
            request_fingerprint=admission[
                "request_fingerprint"
            ],
        )
    if admission["guard_local_result"]:
        from agent.services.recovery_dispatch_gate_service import (
            get_recovery_dispatch_gate_service,
            recovery_dispatch_request_fingerprint,
        )

        with get_recovery_dispatch_gate_service().result_guard(
            tid,
            token=admission["token"],
            phase="execute",
            request_fingerprint=(
                recovery_dispatch_request_fingerprint(
                    "execute",
                    request_data,
                )
            ),
            worker_url=admission["worker_url"],
        ) as decision:
            if not decision.allowed:
                return _dispatch_admission_error(
                    tid=tid,
                    phase="execute",
                    decision=decision,
                )
            service._persist_forwarded_execution(
                tid=tid,
                response=outcome.data,
                task=task,
                request_data=request_data,
            )
    _cache_recovery_outcome(
        admission["token"],
        outcome,
        task_id=tid,
        phase="execute",
        request_fingerprint=admission["request_fingerprint"],
    )
    return outcome


def _run_execute_step_admitted(
    service,
    tid: str,
    request_data,
    *,
    forwarder: Callable,
    cli_runner: Callable | None = None,
    tool_definitions_resolver: Callable | None = None,
):
    """Execute an already admitted execute request."""
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    task = service._require_task(tid)
    if guard := _organization_research_hub_execution_guard(
        task=task,
        tid=tid,
        phase="execute",
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
    from agent.services.recovery_dispatch_gate_service import (
        get_recovery_dispatch_gate_service,
    )
    from agent.services.recovery_worker_result_service import (
        get_recovery_worker_result_service,
    )

    proposal_context = getattr(
        request_data,
        "recovery_proposal_context",
        None,
    )
    recovery_child = (
        get_recovery_dispatch_gate_service().is_recovery_child(
            task
        )
    )
    if proposal_context is not None and not recovery_child:
        raise ValueError("recovery_proposal_context_unexpected")
    if recovery_child:
        get_recovery_worker_result_service().apply_proposal_context(
            task=task,
            value=proposal_context,
        )
    terminal_guard = service._terminal_parent_goal_guard(tid=tid, task=task, phase="execute")
    if terminal_guard is not None:
        return terminal_guard
    forwarded = service._forward_task_request_if_remote(
        tid=tid,
        task=task,
        endpoint=f"/tasks/{tid}/step/execute",
        payload=request_data.model_dump(),
        forwarder=forwarder,
        on_success=lambda response, loaded_task, *, transport_deadline=None: service._persist_forwarded_execution(
            tid=tid,
            response=response,
            task=loaded_task,
            request_data=request_data,
            transport_deadline=transport_deadline,
        ),
    )
    if forwarded is not None:
        return forwarded

    from agent.config import settings

    if str(settings.role or "").strip().lower() == "worker":
        from worker.runtime.workflow_adapter_task_execution import (
            consume_delegated_workflow_task,
        )

        delegated_workflow_result = consume_delegated_workflow_task(task)
        if delegated_workflow_result is not None:
            return TaskScopedRouteResponse(data=delegated_workflow_result)

    authoritative_task_kind = str(
        task.get("task_kind") or ""
    ).strip().lower()
    requested_task_kind = str(
        getattr(request_data, "task_kind", None) or ""
    ).strip().lower()
    routed_task_kind = str(
        (
            (task.get("last_proposal", {}) or {}).get(
                "routing"
            )
            or {}
        ).get("task_kind")
        or ""
    ).strip().lower()
    if authoritative_task_kind == "vector_index_operation":
        for candidate in (
            requested_task_kind,
            routed_task_kind,
        ):
            if candidate and candidate != authoritative_task_kind:
                return TaskScopedRouteResponse(
                    data={
                        "status": "denied",
                        "reason_code": (
                            "vector_index_task_kind_override_forbidden"
                        ),
                        "task_id": tid,
                    },
                    status="denied",
                    message=(
                        "Vector index task kind is Hub-authoritative"
                    ),
                    code=403,
                )
    explicit_task_kind = (
        authoritative_task_kind
        or requested_task_kind
        or routed_task_kind
    )
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
    if authoritative_task_kind == "vector_index_operation":
        return _vector_index_handler_unavailable(
            task=task,
            phase="execute",
        )

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
