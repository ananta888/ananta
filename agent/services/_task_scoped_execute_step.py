"""Task-scoped execute step: admission, routing, recovery receipts and response assembly.

Split out of ``_task_scoped_step_orchestrator`` (SRP). ``run_execute_step``
stays importable from ``_task_scoped_step_orchestrator``. Dispatch admission and
the admitted execute runner are explicit ``step_ports``
(:class:`StepOrchestrationPorts`); when omitted they come from the documented
``_task_scoped_forwarding_dependencies`` seam.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Callable

from agent.runtime_policy import normalize_task_kind
from agent.services._task_scoped_adapters import try_handler_execute
from agent.services._task_scoped_domain_action import (
    execute_domain_action,
    execute_research_artifact,
    finalize_interactive_terminal_execution,
)
from agent.services._task_scoped_execute_workspace import run_execute_workspace_path
from agent.services._task_scoped_step_policies import (
    _INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
    _apply_request_run_evidence_context,
    _cache_recovery_outcome,
    _cached_recovery_outcome,
    _dispatch_admission_error,
    _organization_research_hub_execution_guard,
    _vector_index_domain_binding_error,
    _vector_index_handler_unavailable,
)
from agent.services.task_scoped_execution_service import get_core_services, get_goal_config_runtime_service
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile

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


def run_execute_step(
    service,
    tid: str,
    request_data,
    *,
    forwarder: Callable,
    cli_runner: Callable | None = None,
    tool_definitions_resolver: Callable | None = None,
    step_ports: "StepOrchestrationPorts | None" = None,
):
    """Route an execute request to the appropriate strategy."""
    ports = _resolve_step_ports(step_ports)
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
    admission = ports.admit_dispatch(
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
        outcome = ports.run_execute_admitted(
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
