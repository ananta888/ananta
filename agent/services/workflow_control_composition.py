"""Production composition for the Hub-owned workflow control boundary.

The visual-process API predates the runtime-neutral ``ExecutionPlan`` contract
and still exposes the small ``WorkflowBackend`` interface.  This module keeps
that API compatible while ensuring that callers never receive a Local or
Temporal backend directly: every operation is authorized and dispatched by one
process-wide :class:`WorkflowControlService` instance.

The configured backend is an infrastructure adapter only.  It cannot become a
second control plane and this module deliberately has no worker imports.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from secrets import token_bytes
from typing import Any

from agent.common.audit import log_audit
from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_backend import (
    WORKFLOW_STATUS_SCHEMA,
    WorkflowBackend,
    WorkflowRequest,
    WorkflowSignal,
)
from agent.services.workflow_backend_durable_run_adapter import (
    DURABLE_RUN_SIGNAL_SCHEMA,
    DURABLE_RUN_START_SCHEMA,
    WorkflowBackendDurableRunAdapter,
)
from agent.services.workflow_backend_factory import (
    WorkflowBackendConfig,
    get_workflow_backend,
    get_workflow_backend_config,
)
from agent.services.workflow_configured_bridge_reconciler import (
    ConfiguredBridgeReconciler,
)
from agent.services.workflow_control_authorization_helpers import (
    ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
    assert_route_control_envelope,
    register_bound_authorization_grants,
)
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlBindingOwnerResolver,
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
    WorkflowRouteControlAuthorization,
)
from agent.services.workflow_control_command_receipt_persistence import (
    InMemoryWorkflowControlCommandReceiptStore,
    SQLAlchemyWorkflowControlCommandReceiptStore,
)
from agent.services.workflow_control_command_receipts import (
    COMMAND_RECEIPT_COMPLETED,
    COMMAND_RECEIPT_REJECTED,
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptError,
    WorkflowControlCommandReceiptReconciler,
    WorkflowControlCommandReceiptStore,
    WorkflowControlCommandRejectedError,
    admitted_receipt_command,
    assert_stable_receipt_retry,
    validate_persisted_public_status,
)
from agent.services.workflow_control_command_receipts import (
    status_revision as command_receipt_status_revision,
)
from agent.services.workflow_control_command_verification import (
    HubSignedWorkflowCommandVerifier,
    HubVerifiedDurableCommandPort,
)
from agent.services.workflow_control_dispatch_intents import (
    WorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_dispatch_persistence import (
    InMemoryWorkflowControlDispatchIntentStore,
    SQLAlchemyWorkflowControlDispatchIntentStore,
)
from agent.services.workflow_control_dispatch_service import (
    START_OBSERVATION_PENDING,
    WorkflowControlDispatchService,
)
from agent.services.workflow_control_production_composition import (
    production_authorization_grants as _production_authorization_grants,
)
from agent.services.workflow_control_production_composition import (
    production_binding_store as _production_binding_store,
)
from agent.services.workflow_control_production_composition import (
    production_command_key_ring as _production_command_key_ring,
)
from agent.services.workflow_control_production_composition import (
    production_command_replay_store as _production_command_replay_store,
)
from agent.services.workflow_control_production_composition import (
    production_command_transition_runtime as _production_command_transition_runtime,
)
from agent.services.workflow_control_production_composition import (
    production_dispatch_intent_store as _production_dispatch_intent_store,
)
from agent.services.workflow_control_production_composition import (
    production_read_model_projector as _production_read_model_projector,
)
from agent.services.workflow_control_production_composition import (
    production_release_admission as _production_release_admission,
)
from agent.services.workflow_control_production_composition import (
    production_rollout_policies as _production_rollout_policies,
)
from agent.services.workflow_control_production_composition import (
    production_runtime_health as _production_runtime_health,
)
from agent.services.workflow_control_production_composition import (
    production_runtime_profiles as _production_runtime_profiles,
)
from agent.services.workflow_control_production_composition import (
    production_terminal_trace_runtime as _production_terminal_trace_runtime,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_control_release_selection import (
    UnavailableWorkflowRuntimeReleaseAdmission,
    WorkflowRuntimeReleaseAdmissionPort,
)
from agent.services.workflow_control_service import (
    CONTROL_COMMAND_TYPES,
    RuntimeSelection,
    WorkflowControlCommand,
    WorkflowControlService,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRouteAuthorizationService,
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)
from agent.services.workflow_run_history_paging import page_workflow_run_history
from agent.services.workflow_runtime.commands import (
    SignedWorkflowCommand,
    WorkflowCommandIssuer,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.execution_plan import (
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)
from agent.services.workflow_runtime.ports import DurableRunInfrastructurePort
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    InMemoryReplayNonceStore,
    ReplayNonceStore,
    SignatureSigningKeyRingPort,
)
from agent.services.workflow_runtime_bridge_registry import (
    WorkflowRuntimeBridgeRegistry,
)
from agent.services.workflow_runtime_rollout_service import (
    RolloutAwareRuntimeSelection,
    WorkflowRolloutPolicyService,
)
from agent.services.workflow_runtime_selection_composition import (
    build_configured_workflow_runtime_selection,
    configured_runtime_id,
)
from agent.services.workflow_runtime_selection_service import (
    RuntimeHealthPort,
    RuntimeSelectionAuditPort,
    WorkflowRuntimeProfileService,
)
from agent.services.workflow_runtime_status_projection import authoritative_runtime_status
from agent.services.workflow_terminal_trace_reconciliation import (
    WorkflowTerminalTraceReconciler,
    WorkflowTerminalTraceStatePort,
    is_terminal_status,
    status_revision,
)
from agent.services.workflow_transition_native_composition import WorkflowCommandTransitionRuntime
from agent.services.workflow_transition_public_projection import canonical_workflow_public_status
from ananta_contracts.temporal_workflow import (
    COMMAND_RESULT_SCHEMA,
)
from ananta_contracts.temporal_workflow import (
    STATUS_SCHEMA as TEMPORAL_STATUS_SCHEMA,
)

_FAILED_START_STATUSES = frozenset({"degraded", "unavailable", "not_found"})
# One attributed command plans three effects, so four bounded drive attempts
# leave room for a single retry without ever becoming an unbounded wait.
_TRANSITION_DRIVE_ATTEMPTS = 4
_TEMPORAL_COMMAND_RESULT_KEYS = frozenset({"schema", "command_id", "accepted", "revision", "status", "reason_code"})
_TEMPORAL_COMMAND_STATUSES = frozenset(
    {"created", "running", "paused", "waiting_approval", "completed", "failed", "cancelled"}
)


def _canonical_public_status(
    binding: WorkflowControlRunBinding,
    status: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    if configured_runtime_id(binding.runtime_id) == "temporal":
        return dict(status)
    return canonical_workflow_public_status(binding, status, previous=previous)


def _start_request_id(raw: Any, *, workflow_id: str) -> str:
    explicit = str(raw or "").strip()
    if explicit:
        return explicit
    return f"start-{uuid.uuid5(uuid.NAMESPACE_URL, f'ananta:{workflow_id}').hex}"


def _authoritative_projection_binding(
    bindings: WorkflowControlBindingStore,
    candidate: WorkflowControlRunBinding,
) -> WorkflowControlRunBinding:
    authoritative = bindings.get(candidate.workflow_id)
    if authoritative is None:
        raise RuntimeError("workflow_control_binding_not_found")
    if replace(candidate, runtime_id=authoritative.runtime_id) != authoritative or candidate.runtime_id not in {
        "pending",
        authoritative.runtime_id,
    }:
        raise ValueError("workflow_control_public_status_binding_mismatch")
    if authoritative.runtime_id == "pending":
        raise ValueError("workflow_control_public_status_runtime_unbound")
    return authoritative


class ConfiguredWorkflowBackendBridge:
    """Infrastructure bridge used only by the Hub ``WorkflowControlService``."""

    def __init__(
        self,
        backend: WorkflowBackend,
        bindings: WorkflowControlBindingStore,
        *,
        durable_runs: DurableRunInfrastructurePort | None = None,
        commands: HubVerifiedDurableCommandPort | None = None,
        read_models: WorkflowControlReadModelProjector | None = None,
        authorization_grants: WorkflowAuthorizationGrantPort | None = None,
        dispatch_intents: WorkflowControlDispatchIntentStore | None = None,
        trace_state: WorkflowTerminalTraceStatePort | None = None,
    ) -> None:
        self._backend = backend
        self._bindings = bindings
        self._trace_state = trace_state
        self._durable_runs = durable_runs
        self._commands = commands
        self._read_models = read_models
        self._authorization_grants = authorization_grants
        self._dispatcher = (
            WorkflowControlDispatchService(
                runtime_id=self.selection_runtime_id,
                bindings=bindings,
                intents=dispatch_intents,
                durable_runs=durable_runs,
                commands=commands,
                project=self._project_strict,
            )
            if durable_runs is not None and commands is not None and dispatch_intents is not None
            else None
        )
        self._reconciler = (
            ConfiguredBridgeReconciler(
                runtime_id=self.selection_runtime_id,
                bindings=bindings,
                durable_runs=durable_runs,
                project=self._project,
            )
            if durable_runs is not None
            else None
        )
        if self.runtime_id == "temporal" and self._durable_runs is None:
            raise ValueError("temporal_durable_run_port_required")
        if self.runtime_id == "temporal" and self._dispatcher is None:
            raise ValueError("temporal_dispatch_intent_store_required")
        if self.runtime_id != "temporal" and self._durable_runs is not None:
            raise ValueError("durable_run_port_requires_temporal_backend")

    @property
    def runtime_id(self) -> str:
        return str(self._backend.backend_id)

    @property
    def selection_runtime_id(self) -> str:
        return configured_runtime_id(self.runtime_id)

    def start(
        self,
        *,
        principal: WorkflowPrincipal,
        plan: ExecutionPlan,
        run_id: str,
        selection: RuntimeSelection,
        authorization_envelope: dict[str, Any],
    ) -> WorkflowRunHandle:
        binding = self._require_binding(plan.workflow_id)
        self._assert_binding(
            binding,
            principal=principal,
            run_id=run_id,
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
        )
        assert_route_control_envelope(
            authorization_envelope,
            principal=principal,
            workflow_id=plan.workflow_id,
            run_id=run_id,
        )
        if selection.runtime_id != self.selection_runtime_id:
            raise ValueError("workflow_control_runtime_binding_mismatch")

        request = replace(
            binding.request,
            requested_by=principal.subject_id,
            metadata={
                **dict(binding.request.metadata),
                "tenant_id": principal.tenant_id,
                "run_id": run_id,
                "plan_hash": plan.plan_hash,
                "policy_version": plan.policy_version,
            },
        )
        register_bound_authorization_grants(
            self._authorization_grants,
            request=request,
            plan=plan,
        )
        if self._durable_runs is not None:
            if self._dispatcher is None:
                raise RuntimeError("workflow_control_dispatcher_required")
            start_command = {
                "schema": DURABLE_RUN_START_SCHEMA,
                "tenant_id": principal.tenant_id,
                "workflow_id": plan.workflow_id,
                "run_id": run_id,
                "workflow_request": request.to_dict(),
            }
            status = self._dispatcher.stage_start(
                binding=binding,
                start_command=start_command,
                request_id=_start_request_id(
                    authorization_envelope.get("start_request_id"),
                    workflow_id=binding.workflow_id,
                ),
                pending_status=_initial_start_pending_status(
                    binding=binding,
                    runtime_id=self.selection_runtime_id,
                ),
            )
        else:
            status = self._mapping(self._backend.start_workflow(request))
            status = authoritative_runtime_status(
                status,
                binding=binding,
                previous=None,
                runtime_id=self.selection_runtime_id,
                allow_initial_ack=True,
            )
            self._bindings.record_status(plan.workflow_id, status)
            self._project(
                binding,
                status,
                mode=selection.mode,
                capabilities=tuple(sorted(selection.capabilities)),
            )
        runtime_ref = str((status.get("temporal") or {}).get("run_id") or plan.workflow_id)
        return WorkflowRunHandle(
            tenant_id=principal.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=run_id,
            runtime_id=selection.runtime_id,
            status=str(status.get("status") or "unknown"),
            task_ref=runtime_ref,
            reason_code=str(status.get("reason_code") or status.get("reason") or ""),
        )

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]:
        binding = self._binding_for_run(run_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        self._assert_principal(binding, principal)
        if self._dispatcher is not None:
            self._dispatcher.reconcile_workflow(binding.workflow_id)
        status = self._bindings.last_status(binding.workflow_id)
        if status is None:
            raise LookupError("workflow_control_status_not_found")
        return dict(status)

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        dispatch = (
            self._dispatcher.drain(limit=limit)
            if self._dispatcher is not None
            else {"runtime_id": self.selection_runtime_id, "processed": 0, "failed": []}
        )
        if self._reconciler is None:
            return dispatch
        observation = self._reconciler.reconcile_active(limit=limit)
        if not dispatch["processed"] and not dispatch["failed"]:
            return observation
        return {
            "runtime_id": self.selection_runtime_id,
            "processed": int(dispatch["processed"]) + int(observation["processed"]),
            "failed": [*dispatch["failed"], *observation["failed"]],
            "reports": [dispatch, observation],
        }

    def retry_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command_id: str,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._dispatcher is None:
            return None
        return self._dispatcher.retry_command(
            binding=binding,
            command_id=command_id,
            command_type=command_type,
            payload=payload,
        )

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        binding = self._require_command_binding(command, principal)
        if self._durable_runs is None:
            self._verify_local_command(command, binding)
        if self._durable_runs is not None:
            if self._dispatcher is None:
                raise RuntimeError("workflow_control_dispatcher_required")
            return self._dispatcher.stage_command(binding=binding, command=command)
        self._bindings.claim_command(
            binding.workflow_id,
            expected_revision=command.expected_revision,
            checkpoint_id=command.checkpoint_id,
            command_id=command.command_id,
        )
        try:
            self._restore_local_binding(binding)
            signal = WorkflowSignal(
                name=command.command_type,
                payload=dict(command.payload),
                actor=command.actor_id,
            )
            status = self._mapping(self._backend.signal_workflow(binding.workflow_id, signal))
        except (PermissionError, ValueError) as exc:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        except Exception:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise
        status = authoritative_runtime_status(
            status,
            binding=binding,
            previous=self._bindings.last_status(binding.workflow_id),
            runtime_id=self.selection_runtime_id,
        )
        self._bindings.finish_command(
            binding.workflow_id,
            command_id=command.command_id,
            status=status,
        )
        self._project(binding, status)
        return status

    def cancel(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        binding = self._require_command_binding(command, principal)
        reason = str(command.payload.get("reason") or "")[:1000]
        if self._durable_runs is None:
            self._verify_local_command(command, binding)
        if self._durable_runs is not None:
            if self._dispatcher is None:
                raise RuntimeError("workflow_control_dispatcher_required")
            return self._dispatcher.stage_command(binding=binding, command=command)
        self._bindings.claim_command(
            binding.workflow_id,
            expected_revision=command.expected_revision,
            checkpoint_id=command.checkpoint_id,
            command_id=command.command_id,
        )
        try:
            self._restore_local_binding(binding)
            status = self._mapping(self._backend.cancel_workflow(binding.workflow_id, reason=reason))
        except (PermissionError, ValueError) as exc:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        except Exception:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise
        status = authoritative_runtime_status(
            status,
            binding=binding,
            previous=self._bindings.last_status(binding.workflow_id),
            runtime_id=self.selection_runtime_id,
        )
        self._bindings.finish_command(
            binding.workflow_id,
            command_id=command.command_id,
            status=status,
        )
        self._project(binding, status)
        return status

    def recover_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        """Resume a persisted synchronous receipt without consuming its nonce."""

        if self._durable_runs is not None or self._commands is None:
            raise RuntimeError("workflow_control_command_recovery_unsupported")
        binding = self._require_command_binding(command, principal)
        self._commands.verify_persisted(
            tenant_id=binding.tenant_id,
            run_id=binding.workflow_id,
            command={
                "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                "command": command.to_dict(),
            },
        )
        self._bindings.claim_command(
            binding.workflow_id,
            expected_revision=command.expected_revision,
            checkpoint_id=command.checkpoint_id,
            command_id=command.command_id,
        )
        try:
            observed = self._mapping(self._backend.get_workflow_status(binding.workflow_id))
            if str(observed.get("status") or "").lower() == "not_found":
                self._restore_local_binding(binding)
                observed = self._mapping(self._backend.get_workflow_status(binding.workflow_id))
            observed_revision = int(observed.get("revision", 0))
            if observed_revision <= command.expected_revision:
                if command.command_type == "cancel":
                    observed = self._mapping(
                        self._backend.cancel_workflow(
                            binding.workflow_id,
                            reason=str(command.payload.get("reason") or "")[:1000],
                        )
                    )
                else:
                    observed = self._mapping(
                        self._backend.signal_workflow(
                            binding.workflow_id,
                            WorkflowSignal(
                                name=command.command_type,
                                payload=dict(command.payload),
                                actor=command.actor_id,
                            ),
                        )
                    )
            status = authoritative_runtime_status(
                observed,
                binding=binding,
                previous=self._bindings.last_status(binding.workflow_id),
                runtime_id=self.selection_runtime_id,
            )
            self._bindings.finish_command(
                binding.workflow_id,
                command_id=command.command_id,
                status=status,
            )
        except (PermissionError, ValueError) as exc:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        except Exception:
            self._bindings.release_command(
                binding.workflow_id,
                command_id=command.command_id,
            )
            raise
        self._project(binding, status)
        return status

    def _dispatch_durable_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        if self._durable_runs is None:
            raise RuntimeError("durable_run_port_required")
        try:
            # Claim ambiguity before crossing the infrastructure boundary.  A
            # failed persistence write must prevent dispatch; after dispatch,
            # every response (including a malformed/rejected ACK) is reconciled
            # through an authoritative describe instead of replaying mutation.
            self._bindings.mark_command_observation_pending(
                binding.workflow_id,
                command_id=command.command_id,
                minimum_revision=command.expected_revision + 1,
                reconciliation_ready=False,
            )
        except Exception as exc:
            self._audit_command_observation_pending(
                binding=binding,
                command=command,
                stage="dispatch_persistence",
                cause=exc,
            )
            raise RuntimeError("workflow_control_command_observation_pending") from exc
        try:
            response = self._durable_runs.signal(
                tenant_id=principal.tenant_id,
                run_id=binding.workflow_id,
                command={
                    "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                    "command": command.to_dict(),
                },
            )
        except Exception as exc:
            self._make_command_reconcilable(
                binding=binding,
                command=command,
                stage="dispatch",
                cause=exc,
            )
            raise AssertionError("unreachable") from exc

        acknowledged_revision: int | None = None
        acknowledged_status = ""
        try:
            acknowledgement = self._mapping(response)
            acknowledged_revision, acknowledged_status = _validate_temporal_command_ack(
                acknowledgement,
                command=command,
            )
            self._bindings.mark_command_observation_pending(
                binding.workflow_id,
                command_id=command.command_id,
                minimum_revision=acknowledged_revision,
                expected_status=acknowledged_status,
                reconciliation_ready=False,
            )
            observed = self._mapping(
                self._durable_runs.describe(
                    tenant_id=principal.tenant_id,
                    run_id=binding.workflow_id,
                )
            )
            status = authoritative_runtime_status(
                observed,
                binding=binding,
                previous=self._bindings.last_status(binding.workflow_id),
                runtime_id=self.selection_runtime_id,
            )
            _assert_acknowledged_observation(
                status,
                acknowledged_revision=acknowledged_revision,
                acknowledged_status=acknowledged_status,
            )
            self._bindings.finish_command(
                binding.workflow_id,
                command_id=command.command_id,
                status=status,
            )
        except Exception as exc:
            self._make_command_reconcilable(
                binding=binding,
                command=command,
                stage="acknowledge_or_describe",
                cause=exc,
                minimum_revision=acknowledged_revision,
                expected_status=acknowledged_status,
            )
        self._project(binding, status)
        return status

    def _make_command_reconcilable(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
        stage: str,
        cause: Exception,
        minimum_revision: int | None = None,
        expected_status: str = "",
    ) -> None:
        try:
            self._bindings.mark_command_observation_pending(
                binding.workflow_id,
                command_id=command.command_id,
                minimum_revision=(minimum_revision if minimum_revision is not None else command.expected_revision + 1),
                expected_status=expected_status,
                reconciliation_ready=True,
            )
        except Exception as pending_exc:
            self._audit_command_observation_pending(
                binding=binding,
                command=command,
                stage=f"{stage}_reconciliation_persistence",
                cause=pending_exc,
            )
            raise RuntimeError("workflow_control_command_observation_pending") from cause
        self._raise_command_observation_pending(
            binding=binding,
            command=command,
            stage=stage,
            cause=cause,
        )

    def _raise_command_observation_pending(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
        stage: str,
        cause: Exception,
    ) -> None:
        self._audit_command_observation_pending(
            binding=binding,
            command=command,
            stage=stage,
            cause=cause,
        )
        raise RuntimeError("workflow_control_command_observation_pending") from cause

    def _audit_command_observation_pending(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
        stage: str,
        cause: Exception,
    ) -> None:
        log_audit(
            "workflow_control_command_observation_pending",
            {
                "tenant_id": binding.tenant_id,
                "workflow_id": binding.workflow_id,
                "run_id": binding.run_id,
                "command_id": command.command_id,
                "runtime": self.runtime_id,
                "stage": stage,
                "error_type": type(cause).__name__,
            },
        )

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        binding = self._binding_for_run(run_id)
        workflow_id = binding.workflow_id if binding is not None else str(run_id)
        if binding is not None:
            self._assert_principal(binding, principal)
        offset = max(0, int(after_sequence))
        if self._durable_runs is not None:
            page = self._durable_runs.history(
                tenant_id=principal.tenant_id,
                run_id=workflow_id,
                after_cursor=str(offset),
            )
            events = page.get("events") if isinstance(page, dict) else None
            if not isinstance(events, list):
                raise TypeError("durable_run_history_invalid_response")
            projected_events = tuple(dict(event) for event in events if isinstance(event, dict))
        else:
            # Anchor on the events' own identity rather than slicing by list
            # position, and bound the page: a reconciler must be able to resume
            # a long run exactly, without ever reading it whole.
            events = self._backend.list_workflow_events(workflow_id)
            anchor = "" if offset <= 0 else str(offset)
            projected_events = page_workflow_run_history(
                [event for event in events if isinstance(event, dict)],
                after_cursor=anchor,
            ).events
        return projected_events

    def _mark_terminal_trace(
        self,
        binding: WorkflowControlRunBinding,
        status: Mapping[str, Any],
    ) -> None:
        """Record that a terminal run still owes a projected trace.

        This is deliberately durable rather than best effort: the projection
        below may fail, and without a pending marker that failure would silently
        cost the run its final trace.  Marking cannot fail the caller either —
        a run must not be blocked because its bookkeeping was unavailable.
        """

        if self._trace_state is None or not is_terminal_status(status):
            return
        try:
            self._trace_state.mark_pending(
                binding.workflow_id,
                revision=status_revision(status),
            )
        except Exception as exc:
            log_audit(
                "workflow_terminal_trace_mark_failed",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "error_type": type(exc).__name__,
                },
            )

    def _project(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
        *,
        mode: str = "",
        capabilities: tuple[str, ...] = (),
        events: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self._mark_terminal_trace(binding, status)
        if self._read_models is None:
            return
        try:
            self._project_strict(
                binding,
                status,
                mode=mode,
                capabilities=capabilities,
                events=events,
            )
        except Exception as exc:
            log_audit(
                "workflow_runtime_read_model_projection_failed",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "runtime": self.runtime_id,
                    "error_type": type(exc).__name__,
                },
            )

    def _project_strict(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
        *,
        mode: str = "",
        capabilities: tuple[str, ...] = (),
        events: tuple[dict[str, Any], ...] = (),
    ) -> None:
        if self._read_models is None:
            return
        self._read_models.project(
            binding=binding,
            status=status,
            runtime=self.runtime_id,
            mode=mode or ("durable" if self.runtime_id == "temporal" else "live"),
            capabilities=capabilities,
            events=events,
        )

    def _binding_for_run(self, run_id: str) -> WorkflowControlRunBinding | None:
        return self._bindings.get_by_run_id(run_id)

    def _require_binding(self, workflow_id: str) -> WorkflowControlRunBinding:
        binding = self._bindings.get(workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return binding

    def _require_command_binding(
        self,
        command: SignedWorkflowCommand,
        principal: WorkflowPrincipal,
    ) -> WorkflowControlRunBinding:
        binding = self._require_binding(command.workflow_id)
        self._assert_binding(
            binding,
            principal=principal,
            run_id=command.run_id,
            plan_hash=command.plan_hash,
            policy_version=command.policy_version,
        )
        status = self._bindings.last_status(binding.workflow_id) or {}
        checkpoint_id = str(status.get("checkpoint_ref") or binding.checkpoint_id)
        if checkpoint_id != command.checkpoint_id:
            raise PermissionError("workflow_control_checkpoint_binding_mismatch")
        try:
            current_revision = int(status.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise PermissionError("workflow_control_revision_binding_invalid") from exc
        if current_revision != command.expected_revision:
            raise PermissionError("workflow_control_revision_binding_mismatch")
        return binding

    def _verify_local_command(
        self,
        command: SignedWorkflowCommand,
        binding: WorkflowControlRunBinding,
    ) -> None:
        if self._commands is None:
            raise PermissionError("workflow_hub_verified_command_required")
        self._commands.verify(
            tenant_id=binding.tenant_id,
            run_id=binding.workflow_id,
            command={
                "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                "command": command.to_dict(),
            },
        )

    def _restore_local_binding(self, binding: WorkflowControlRunBinding) -> None:
        restore = getattr(self._backend, "restore_workflow", None)
        if not callable(restore):
            return
        current = self._mapping(self._backend.get_workflow_status(binding.workflow_id))
        if str(current.get("status") or "").lower() != "not_found":
            return
        persisted = self._bindings.last_status(binding.workflow_id)
        if persisted is None:
            raise LookupError("workflow_control_status_not_found")
        request = replace(
            binding.request,
            requested_by=binding.subject_id,
            metadata={
                **dict(binding.request.metadata),
                "tenant_id": binding.tenant_id,
                "run_id": binding.run_id,
                "plan_hash": binding.plan_hash,
                "policy_version": binding.policy_version,
            },
        )
        restore(request, persisted)

    @staticmethod
    def _assert_binding(
        binding: WorkflowControlRunBinding,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        plan_hash: str,
        policy_version: str,
    ) -> None:
        ConfiguredWorkflowBackendBridge._assert_principal(binding, principal)
        if binding.run_id != str(run_id):
            raise PermissionError("workflow_control_run_binding_mismatch")
        if binding.plan_hash != str(plan_hash):
            raise PermissionError("workflow_control_plan_binding_mismatch")
        if binding.policy_version != str(policy_version):
            raise PermissionError("workflow_control_policy_binding_mismatch")

    @staticmethod
    def _assert_principal(binding: WorkflowControlRunBinding, principal: WorkflowPrincipal) -> None:
        if binding.tenant_id != principal.tenant_id or binding.subject_id != principal.subject_id:
            raise PermissionError("workflow_control_principal_binding_mismatch")

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("workflow_backend_invalid_response")
        return dict(value)


class WorkflowBackendControlFacade:
    """Bind legacy backend-shaped callers to one Hub control service."""

    def __init__(
        self,
        *,
        control: WorkflowControlService,
        bridge: ConfiguredWorkflowBackendBridge,
        bindings: WorkflowControlBindingStore,
        registry: WorkflowRuntimeBridgeRegistry,
        command_receipts: WorkflowControlCommandReceiptStore,
        transitions: WorkflowCommandTransitionRuntime | None = None,
        trace_reconciler: WorkflowTerminalTraceReconciler | None = None,
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._registry = registry
        self._command_receipts = command_receipts
        self._transitions = transitions
        self._trace_reconciler = trace_reconciler
        receipt_reconciler_owner = f"receipt-reconciler:{uuid.uuid4().hex}"
        self._receipt_reconciler = WorkflowControlCommandReceiptReconciler(
            receipts=command_receipts,
            bindings=bindings,
            project=self._project_public_status,
            recover=self._recover_command_receipt_runtime,
            owner_id=receipt_reconciler_owner,
        )

    def _project_public_status(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        binding = _authoritative_projection_binding(self._bindings, binding)
        previous = self._bindings.last_public_status(binding.workflow_id)
        projected = _canonical_public_status(
            binding,
            status,
            previous=previous,
        )
        self._bindings.record_public_status(binding.workflow_id, projected)
        persisted = self._bindings.last_public_status(binding.workflow_id)
        if persisted is None:
            raise RuntimeError("workflow_control_public_status_missing")
        return persisted

    def _recover_command_receipt_runtime(
        self,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> dict[str, Any]:
        command = admitted_receipt_command(receipt)
        return dict(
            self._registry.recover_command(
                principal=WorkflowPrincipal(
                    tenant_id=receipt.tenant_id,
                    subject_id=receipt.actor_id,
                    roles=command.actor_roles,
                ),
                command=command,
            )
        )

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def bind(self, principal: WorkflowRoutePrincipal) -> "AuthorizedWorkflowBackend":
        return AuthorizedWorkflowBackend(
            control=self._control,
            bridge=self._bridge,
            bindings=self._bindings,
            command_receipts=self._command_receipts,
            receipt_reconciler=self._receipt_reconciler,
            registry=self._registry,
            project_public_status=self._project_public_status,
            transitions=self._transitions,
            principal=WorkflowPrincipal(
                tenant_id=principal.tenant_id,
                subject_id=principal.subject,
                roles=principal.roles,
            ),
        )

    @property
    def control_service(self) -> WorkflowControlService:
        return self._control

    @property
    def bindings(self) -> WorkflowControlBindingStore:
        return self._bindings

    @property
    def registry(self) -> WorkflowRuntimeBridgeRegistry:
        return self._registry

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        """Advance active runs only from the Hub background reconciliation path."""

        # Transitions are driven before receipts so a transition that finalizes
        # here is already terminal when the receipt reconciler reads it, rather
        # than being observed mid-flight and deferred a whole cycle.
        transitions = self._transitions.driver.tick() if self._transitions is not None else None
        receipts = self._receipt_reconciler.drain(limit=limit)
        runtime = dict(self._registry.reconcile_active(limit=limit))
        # Traces are drained last: a run that finalized earlier in this same
        # pass is already terminal here, so its trace is projected without
        # waiting a whole cycle.
        traces = self._trace_reconciler.drain(limit=limit) if self._trace_reconciler is not None else None
        driven = transitions.processed if transitions is not None else 0
        projected = traces.projected if traces is not None else 0
        if not receipts["processed"] and not receipts["failed"] and not driven and not projected:
            return runtime
        reports: list[dict[str, Any]] = [receipts, runtime]
        if transitions is not None:
            reports.insert(0, transitions.to_dict())
        if traces is not None:
            reports.append(traces.to_dict())
        return {
            **runtime,
            "processed": int(runtime.get("processed") or 0) + int(receipts["processed"]) + driven + projected,
            "failed": [*list(runtime.get("failed") or ()), *receipts["failed"], *(traces.failed if traces else ())],
            "reports": reports,
        }


class AuthorizedWorkflowBackend:
    """Request-scoped compatibility view; it owns no orchestration state."""

    def __init__(
        self,
        *,
        control: WorkflowControlService,
        bridge: ConfiguredWorkflowBackendBridge,
        bindings: WorkflowControlBindingStore,
        command_receipts: WorkflowControlCommandReceiptStore,
        receipt_reconciler: WorkflowControlCommandReceiptReconciler,
        registry: WorkflowRuntimeBridgeRegistry,
        project_public_status: Any,
        principal: WorkflowPrincipal,
        transitions: WorkflowCommandTransitionRuntime | None = None,
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._command_receipts = command_receipts
        self._receipt_reconciler = receipt_reconciler
        self._registry = registry
        self._project_public_status = project_public_status
        self._principal = principal
        self._transitions = transitions

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def start_workflow(
        self,
        request: WorkflowRequest,
        *,
        command_id: str = "",
    ) -> dict[str, Any]:
        policy_version = str(
            request.metadata.get("policy_version")
            or request.policy_scope.get("policy_version")
            or "legacy-workflow-policy-v1"
        ).strip()
        plan = WorkflowRequestExecutionPlanAdapter.adapt(
            request,
            tenant_id=self._principal.tenant_id,
            policy_version=policy_version,
        )
        run_id = str(request.metadata.get("run_id") or request.workflow_id).strip()
        binding = WorkflowControlRunBinding(
            tenant_id=self._principal.tenant_id,
            subject_id=self._principal.subject_id,
            workflow_id=request.workflow_id,
            run_id=run_id,
            runtime_id="pending",
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            checkpoint_id=f"legacy-current:{plan.plan_hash[:24]}",
            request=request,
            execution_plan=plan.to_dict(),
        )
        created_binding = False
        existing = self._bindings.get(request.workflow_id)
        if existing is None:
            try:
                self._bindings.put(binding)
                created_binding = True
            except RuntimeError as exc:
                if str(exc) != "workflow_control_binding_already_exists":
                    raise
                existing = self._bindings.get(request.workflow_id)
                if existing is None:
                    raise
        if existing is not None:
            _assert_restart_safe_start_adoption(existing, binding)
            binding = existing
            persisted = self._bindings.last_status(request.workflow_id)
            if persisted is not None and str(persisted.get("status") or "").lower() != "pending":
                return self._public_status(binding, persisted)
        try:
            self._control.start(
                principal=self._principal,
                plan=plan,
                run_id=run_id,
                authorization_envelope=self._route_authorization(
                    binding,
                    start_request_id=str(command_id or ""),
                ),
                preferred_runtime=self._bridge.selection_runtime_id,
                allowed_runtimes=(self._bridge.selection_runtime_id,),
            )
        except Exception as exc:
            if created_binding and self.backend_id != "temporal" and str(exc) != START_OBSERVATION_PENDING:
                self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise
        binding = self._bindings.get(request.workflow_id) or binding
        status = self._bindings.last_status(request.workflow_id)
        if status is None:
            if created_binding and self.backend_id != "temporal":
                self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise RuntimeError("workflow_control_start_status_missing")
        if (
            created_binding
            and self.backend_id != "temporal"
            and str(status.get("status") or "").lower() in _FAILED_START_STATUSES
        ):
            self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
        return self._public_status(binding, status)

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self._receipt_reconciler.reconcile_workflow(workflow_id)
        binding = self._bindings.get(workflow_id)
        run_id = binding.run_id if binding is not None else str(workflow_id)
        status = dict(
            self._control.query(
                principal=self._principal,
                workflow_id=str(workflow_id),
                run_id=run_id,
            )
        )
        return self._public_status(binding, status) if binding is not None else status

    def cancel_workflow(self, workflow_id: str, reason: str = "") -> dict[str, Any]:
        return self.command_workflow(
            workflow_id,
            command_type="cancel",
            payload={"reason": str(reason)},
        )

    def signal_workflow(self, workflow_id: str, signal: WorkflowSignal) -> dict[str, Any]:
        if signal.name not in CONTROL_COMMAND_TYPES - {"cancel"}:
            raise ValueError("workflow_control_command_type_unsupported")
        return self.command_workflow(
            workflow_id,
            command_type=signal.name,
            payload=dict(signal.payload),
        )

    def command_workflow(
        self,
        workflow_id: str,
        *,
        command_type: str,
        payload: dict[str, Any] | None = None,
        command_id: str = "",
    ) -> dict[str, Any]:
        """Submit one canonical command through the sole Hub control service."""

        binding = self._bindings.get(workflow_id)
        if binding is None:
            return self._not_found(workflow_id)
        normalized_command_id = str(command_id or "").strip()
        runtime_id = configured_runtime_id(binding.runtime_id)
        if command_type in {"edit", "request_changes"}:
            raise WorkflowControlCommandRejectedError("workflow_plan_edit_rebind_required")
        if normalized_command_id and runtime_id != "temporal":
            existing_receipt = self._command_receipts.get(normalized_command_id)
            if existing_receipt is not None:
                try:
                    assert_stable_receipt_retry(
                        existing_receipt,
                        binding=binding,
                        actor_id=self._principal.subject_id,
                        command_type=command_type,
                        payload=dict(payload or {}),
                    )
                except WorkflowControlCommandReceiptError as exc:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                recovered = self._recover_command_receipt(existing_receipt)
                if recovered is not None:
                    return recovered
        if normalized_command_id and runtime_id == "temporal":
            try:
                repeated = self._registry.retry_command(
                    binding=binding,
                    command_id=normalized_command_id,
                    command_type=command_type,
                    payload=dict(payload or {}),
                )
            except RuntimeError as exc:
                if str(exc) == "workflow_control_dispatch_stage_conflict":
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                raise
            if repeated is not None:
                return dict(repeated)
        if runtime_id == "temporal":
            self.get_workflow_status(workflow_id)
        command = self._command(
            binding,
            command_type=command_type,
            payload=dict(payload or {}),
            command_id=normalized_command_id,
        )
        try:
            signed_command = self._control.prepare_command(
                principal=self._principal,
                command=command,
            )
        except (PermissionError, ValueError) as exc:
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        receipt: WorkflowControlCommandReceipt | None = None
        if normalized_command_id and runtime_id != "temporal":
            try:
                receipt = self._command_receipts.stage(
                    binding=binding,
                    command_id=normalized_command_id,
                    actor_id=self._principal.subject_id,
                    command_type=command.command_type,
                    request_payload=self._command_receipt_request(
                        command,
                        admitted=signed_command,
                    ),
                    expected_revision=command.expected_revision,
                    checkpoint_ref=command.checkpoint_id,
                )
            except WorkflowControlCommandReceiptError as exc:
                if str(exc) in {
                    "workflow_control_command_receipt_conflict",
                    "workflow_control_command_receipt_stage_conflict",
                    "workflow_control_command_receipt_replay_detected",
                }:
                    raise WorkflowControlCommandRejectedError("workflow_control_command_id_conflict") from exc
                raise
            if receipt.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(receipt.rejection_reason)
            if self._transitions is not None and not receipt.transition_id:
                # Admission attributes the receipt row itself under CAS, so the
                # transition and its command become one durable fact before any
                # effect runs.  A failure here must not fall through to the
                # unattributed dispatch path.
                self._transitions.admission.stage_or_adopt(receipt=receipt, binding=binding)
                receipt = self._command_receipts.get(receipt.command_id) or receipt
            recovered = self._recover_command_receipt(receipt)
            if recovered is not None:
                return recovered
        try:
            result = dict(
                self._control.dispatch_command(
                    principal=self._principal,
                    command=signed_command,
                )
            )
            result = self._public_status(binding, result)
        except WorkflowControlCommandRejectedError as exc:
            if receipt is not None:
                # Explicit-ID synchronous commands are dispatched from
                # ``_recover_command_receipt`` under a receipt lease.
                raise RuntimeError("workflow_control_command_receipt_lease_missing") from exc
            log_audit(
                "workflow_control_command_rejected",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "command_id": command.command_id,
                    "reason_code": exc.reason_code,
                },
            )
            raise
        except Exception:
            if receipt is not None:
                recovered = self._recover_command_receipt(receipt)
                if recovered is not None:
                    return recovered
            raise
        if receipt is None:
            return result
        raise RuntimeError("workflow_control_command_receipt_lease_missing")

    def _drive_pending_transition(
        self,
        command_id: str,
        binding: WorkflowControlRunBinding,
    ) -> dict[str, Any]:
        """Drive an attributed command to its terminal receipt, or fail closed.

        A receipt that carries a transition is owned by the transition runner,
        never by the synchronous dispatch path: claiming it here would race a
        live effect against its own fencing.  Driving is therefore the only
        legitimate move, and a transition that does not terminate within the
        bounded budget stays pending rather than reporting a status nothing
        has finalized.
        """

        if self._transitions is None:
            raise RuntimeError("workflow_control_command_transition_pending")
        for _ in range(_TRANSITION_DRIVE_ATTEMPTS):
            self._transitions.driver.tick()
            current = self._command_receipts.get(command_id)
            if current is None:
                raise RuntimeError("workflow_control_command_receipt_missing")
            if current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                validate_persisted_public_status(current, binding, persisted)
                return persisted
            if current.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(current.rejection_reason)
        raise RuntimeError("workflow_control_command_transition_pending")

    def _recover_command_receipt(
        self,
        receipt: WorkflowControlCommandReceipt,
    ) -> dict[str, Any] | None:
        binding = self._bindings.get(receipt.workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        if receipt.state == COMMAND_RECEIPT_COMPLETED:
            persisted = dict(receipt.result_status or {})
            validate_persisted_public_status(receipt, binding, persisted)
            return persisted
        if receipt.state == COMMAND_RECEIPT_REJECTED:
            raise WorkflowControlCommandRejectedError(receipt.rejection_reason)
        if receipt.transition_id:
            return self._drive_pending_transition(receipt.command_id, binding)
        owner_id = f"receipt-request:{uuid.uuid4().hex}"
        claimed = self._command_receipts.claim(
            receipt.command_id,
            owner_id=owner_id,
        )
        deadline = time.monotonic() + 5.0
        while claimed is None and time.monotonic() < deadline:
            current = self._command_receipts.get(receipt.command_id)
            if current is None:
                raise RuntimeError("workflow_control_command_receipt_missing")
            if current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                validate_persisted_public_status(current, binding, persisted)
                return persisted
            if current.state == COMMAND_RECEIPT_REJECTED:
                raise WorkflowControlCommandRejectedError(current.rejection_reason)
            time.sleep(0.01)
            claimed = self._command_receipts.claim(
                receipt.command_id,
                owner_id=owner_id,
            )
        if claimed is None:
            raise RuntimeError("workflow_control_command_observation_pending")
        receipt = claimed
        status = self._bindings.last_status(receipt.workflow_id)
        if status is None or command_receipt_status_revision(status) <= receipt.expected_revision:
            command = admitted_receipt_command(receipt)
            try:
                status = dict(
                    self._registry.recover_command(
                        principal=WorkflowPrincipal(
                            tenant_id=receipt.tenant_id,
                            subject_id=receipt.actor_id,
                            roles=command.actor_roles,
                        ),
                        command=command,
                    )
                )
            except WorkflowControlCommandRejectedError as exc:
                self._command_receipts.reject(
                    receipt.command_id,
                    reason_code=exc.reason_code,
                    owner_id=owner_id,
                    dispatch_generation=receipt.dispatch_generation,
                )
                raise
        status = self._public_status(binding, status)
        receipt = self._command_receipts.heartbeat(
            receipt.command_id,
            owner_id=owner_id,
            dispatch_generation=receipt.dispatch_generation,
        )
        try:
            completed = self._command_receipts.complete(
                receipt.command_id,
                status=status,
                owner_id=owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
        except Exception:
            current = self._command_receipts.get(receipt.command_id)
            if current is not None and current.state == COMMAND_RECEIPT_COMPLETED:
                persisted = dict(current.result_status or {})
                validate_persisted_public_status(current, binding, persisted)
                return persisted
            # The runtime observation and canonical public status are already
            # durable. Releasing only this receipt lease makes a retry adopt
            # that state without replaying the runtime mutation.
            self._command_receipts.release(
                receipt.command_id,
                owner_id=owner_id,
                dispatch_generation=receipt.dispatch_generation,
            )
            raise
        return dict(completed.result_status or status)

    def _command_receipt_request(
        self,
        command: WorkflowControlCommand,
        *,
        admitted: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        return {
            "actor_roles": sorted(self._principal.roles),
            "admitted_command": admitted.to_dict(),
            "payload": dict(command.payload),
            "step_id": command.step_id,
        }

    def _public_status(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        return self._project_public_status(binding, status)

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        binding = self._bindings.get(workflow_id)
        run_id = binding.run_id if binding is not None else str(workflow_id)
        return list(
            self._control.history(
                principal=self._principal,
                workflow_id=str(workflow_id),
                run_id=run_id,
            )
        )

    def _command(
        self,
        binding: WorkflowControlRunBinding,
        *,
        command_type: str,
        payload: dict[str, Any],
        command_id: str = "",
    ) -> WorkflowControlCommand:
        status = self._bindings.last_status(binding.workflow_id) or {}
        step_id = str(payload.get("step_id") or "").strip()
        if not step_id:
            step_id = str(status.get("current_step_id") or "").strip()
        if not step_id:
            open_gates = status.get("open_gates")
            if isinstance(open_gates, list) and open_gates:
                step_id = str(open_gates[0] or "").strip()
        if not step_id:
            step_id = binding.request.steps[0].step_id
        try:
            expected_revision = int(status.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("workflow_control_revision_invalid") from exc
        checkpoint_id = str(status.get("checkpoint_ref") or binding.checkpoint_id)
        return WorkflowControlCommand(
            command_id=str(command_id or f"legacy-control-{uuid.uuid4().hex}"),
            command_type=command_type,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            step_id=step_id,
            checkpoint_id=checkpoint_id,
            expected_revision=expected_revision,
            plan_hash=binding.plan_hash,
            policy_version=binding.policy_version,
            authorization_envelope=self._route_authorization(binding),
            payload=dict(payload),
        )

    def _plan(self, binding: WorkflowControlRunBinding) -> ExecutionPlan:
        return WorkflowRequestExecutionPlanAdapter.adapt(
            binding.request,
            tenant_id=binding.tenant_id,
            policy_version=binding.policy_version,
        )

    def _route_authorization(
        self,
        binding: WorkflowControlRunBinding,
        *,
        start_request_id: str = "",
    ) -> dict[str, str]:
        envelope = {
            "schema": ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
            "tenant_id": self._principal.tenant_id,
            "subject_id": self._principal.subject_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
        }
        if start_request_id:
            envelope["start_request_id"] = start_request_id
        return envelope

    @staticmethod
    def _not_found(workflow_id: str) -> dict[str, Any]:
        return {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": "hub-control",
            "workflow_id": str(workflow_id),
            "status": "not_found",
            "events": [],
        }


def build_workflow_backend_control_facade(
    backend: WorkflowBackend,
    *,
    ownership: WorkflowRouteAuthorizationService = workflow_route_authorization_service,
    bindings: WorkflowControlBindingStore | None = None,
    release_admission: WorkflowRuntimeReleaseAdmissionPort | None = None,
    command_key_ring: SignatureSigningKeyRingPort | None = None,
    command_replay_store: ReplayNonceStore | None = None,
    read_model_projector: WorkflowControlReadModelProjector | None = None,
    runtime_health: RuntimeHealthPort | None = None,
    runtime_selection_audit: RuntimeSelectionAuditPort | None = None,
    runtime_profiles: WorkflowRuntimeProfileService | None = None,
    rollout_policies: WorkflowRolloutPolicyService | None = None,
    authorization_grants: WorkflowAuthorizationGrantPort | None = None,
    dispatch_intents: WorkflowControlDispatchIntentStore | None = None,
    command_receipts: WorkflowControlCommandReceiptStore | None = None,
    command_transitions: WorkflowCommandTransitionRuntime | None = None,
    trace_state: WorkflowTerminalTraceStatePort | None = None,
    trace_reconciler: WorkflowTerminalTraceReconciler | None = None,
    register_all_runtimes: bool = False,
    temporal_backend: WorkflowBackend | None = None,
) -> WorkflowBackendControlFacade:
    """Compose focused adapters around one Hub-owned control service."""

    binding_store = bindings or InMemoryWorkflowControlBindingStore()
    ownership.set_owner_resolver(WorkflowControlBindingOwnerResolver(binding_store))
    key_ring = command_key_ring or HmacKeyRing(
        {"process-local-control": token_bytes(32)},
        active_key_id="process-local-control",
    )
    replay_store = command_replay_store or InMemoryReplayNonceStore()
    command_port = HubSignedWorkflowCommandVerifier(
        WorkflowCommandVerifier(
            key_ring,
            replay_store,
        )
    )
    durable_runs = (
        WorkflowBackendDurableRunAdapter(
            backend,
            commands=command_port,
            command_issuer=WorkflowCommandIssuer(key_ring),
        )
        if str(backend.backend_id) == "temporal"
        else None
    )
    dispatch_store = dispatch_intents
    if dispatch_store is None and (durable_runs is not None or register_all_runtimes):
        dispatch_store = (
            InMemoryWorkflowControlDispatchIntentStore(
                binding_store,
                replay_store=replay_store,
            )
            if isinstance(binding_store, InMemoryWorkflowControlBindingStore)
            else SQLAlchemyWorkflowControlDispatchIntentStore(binding_store.engine)
        )
    receipt_store = command_receipts or (
        InMemoryWorkflowControlCommandReceiptStore(
            binding_store,
            replay_store=replay_store,
        )
        if isinstance(binding_store, InMemoryWorkflowControlBindingStore)
        else SQLAlchemyWorkflowControlCommandReceiptStore(binding_store.engine)
    )
    resolved_read_models = read_model_projector or _production_read_model_projector()
    from agent.services.local_workflow_backend import LocalWorkflowBackend

    if isinstance(backend, LocalWorkflowBackend):
        from agent.database import engine
        from agent.services.native_graph_production_composition import (
            build_native_graph_workflow_control_bridge,
        )
        from agent.services.workflow_authorization_grant_service import (
            InMemoryWorkflowAuthorizationGrantService,
        )

        bridge: Any = build_native_graph_workflow_control_bridge(
            engine=getattr(binding_store, "engine", engine),
            bindings=binding_store,
            key_ring=key_ring,
            replay_store=replay_store,
            authorization_grants=(authorization_grants or InMemoryWorkflowAuthorizationGrantService()),
            read_models=resolved_read_models,
        )
    else:
        bridge = ConfiguredWorkflowBackendBridge(
            backend,
            binding_store,
            durable_runs=durable_runs,
            commands=command_port,
            read_models=resolved_read_models,
            authorization_grants=authorization_grants,
            dispatch_intents=dispatch_store,
            trace_state=trace_state,
        )
    registry = WorkflowRuntimeBridgeRegistry(binding_store)
    if register_all_runtimes:
        if temporal_backend is None:
            raise ValueError("temporal_runtime_bridge_required")
        from agent.services.workflow_control_runtime_registry_composition import (
            register_production_runtime_bridges,
        )

        register_production_runtime_bridges(
            registry=registry,
            configured_bridge=bridge,
            temporal_backend=temporal_backend,
            configured_bridge_factory=ConfiguredWorkflowBackendBridge,
            bindings=binding_store,
            key_ring=key_ring,
            replay_store=replay_store,
            authorization_grants=(authorization_grants or _production_authorization_grants()),
            read_models=resolved_read_models,
            dispatch_intents=(dispatch_intents or dispatch_store),
        )
    else:
        registry.register(
            bridge.selection_runtime_id,
            bridge,
            aliases=(bridge.runtime_id,),
        )
    capability_catalog = None
    if register_all_runtimes:
        from agent.services.workflow_runtime_capability_service import (
            default_workflow_runtime_capability_service,
        )

        capability_catalog = default_workflow_runtime_capability_service()
    selection: Any = build_configured_workflow_runtime_selection(
        backend,
        health=runtime_health,
        release_evidence=release_admission,
        audit=runtime_selection_audit,
        native_production=isinstance(backend, LocalWorkflowBackend),
        registered_runtime_ids=registry.runtime_ids,
        capability_catalog=capability_catalog,
    )
    registry.freeze()
    if rollout_policies is not None:
        selection = RolloutAwareRuntimeSelection(
            policies=rollout_policies,
            selection=selection,
        )
    control = WorkflowControlService(
        authorization=WorkflowRouteControlAuthorization(ownership),
        selection=selection,
        bridge=registry,
        runtime_profiles=runtime_profiles,
        command_issuer=WorkflowCommandIssuer(key_ring),
    )
    return WorkflowBackendControlFacade(
        control=control,
        bridge=bridge,
        bindings=binding_store,
        registry=registry,
        command_receipts=receipt_store,
        transitions=command_transitions,
        trace_reconciler=trace_reconciler,
    )


_COMPOSITION_LOCK = threading.RLock()
_COMPOSITION_KEY: tuple[str, ...] | None = None
_COMPOSITION: WorkflowBackendControlFacade | None = None


def get_workflow_backend_control_facade(
    config: WorkflowBackendConfig | None = None,
) -> WorkflowBackendControlFacade:
    """Return the single active Hub workflow-control composition."""

    global _COMPOSITION, _COMPOSITION_KEY
    resolved = config or get_workflow_backend_config()
    key = _config_key(resolved)
    if _COMPOSITION is not None and _COMPOSITION_KEY == key:
        return _COMPOSITION
    with _COMPOSITION_LOCK:
        if _COMPOSITION is None or _COMPOSITION_KEY != key:
            backend = get_workflow_backend(resolved)
            temporal_backend = (
                backend
                if backend.backend_id == "temporal"
                else get_workflow_backend(replace(resolved, backend="temporal"))
            )
            trace_runtime = _production_terminal_trace_runtime()
            _COMPOSITION = build_workflow_backend_control_facade(
                backend,
                release_admission=_production_release_admission(backend),
                runtime_health=_production_runtime_health(backend),
                runtime_profiles=_production_runtime_profiles(),
                rollout_policies=_production_rollout_policies(),
                authorization_grants=_production_authorization_grants(),
                command_key_ring=_production_command_key_ring(backend),
                command_replay_store=_production_command_replay_store(),
                bindings=_production_binding_store(),
                dispatch_intents=_production_dispatch_intent_store(),
                trace_state=(trace_runtime.state if trace_runtime is not None else None),
                trace_reconciler=(trace_runtime.reconciler if trace_runtime is not None else None),
                command_transitions=_production_command_transition_runtime(backend),
                register_all_runtimes=True,
                temporal_backend=temporal_backend,
            )
            _COMPOSITION_KEY = key
    return _COMPOSITION


def reset_workflow_backend_control_facade() -> None:
    """Test/process lifecycle hook; rebuilding never widens authorization."""

    global _COMPOSITION, _COMPOSITION_KEY
    with _COMPOSITION_LOCK:
        _COMPOSITION = None
        _COMPOSITION_KEY = None
        workflow_route_authorization_service.set_owner_resolver(None)
    from agent.services.workflow_adapter_control_facade import (
        reset_workflow_adapter_control_facade,
    )

    reset_workflow_adapter_control_facade()


def _config_key(config: WorkflowBackendConfig) -> tuple[str, ...]:
    return (
        config.backend,
        config.temporal_address,
        config.temporal_namespace,
        config.temporal_task_queue,
        config.temporal_workflow_type,
        config.temporal_ui_url,
    )


def _initial_start_pending_status(
    *,
    binding: WorkflowControlRunBinding,
    runtime_id: str,
) -> dict[str, Any]:
    """Create a source-grounded Hub snapshot before Temporal dispatch.

    The snapshot asserts no Worker activity.  It only records the immutable
    binding already owned by the Hub and keeps every requested step pending.
    """

    return authoritative_runtime_status(
        {
            "schema": WORKFLOW_STATUS_SCHEMA,
            "backend": runtime_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
            "plan_hash": binding.plan_hash,
            "status": "pending",
        },
        binding=binding,
        previous=None,
        runtime_id=runtime_id,
        allow_initial_ack=True,
    )


def _assert_restart_safe_start_adoption(
    existing: WorkflowControlRunBinding,
    requested: WorkflowControlRunBinding,
) -> None:
    if any(
        (
            existing.tenant_id != requested.tenant_id,
            existing.subject_id != requested.subject_id,
            existing.workflow_id != requested.workflow_id,
            existing.run_id != requested.run_id,
            existing.plan_hash != requested.plan_hash,
            existing.policy_version != requested.policy_version,
            existing.request.to_dict() != requested.request.to_dict(),
            existing.execution_plan != requested.execution_plan,
        )
    ):
        raise RuntimeError("workflow_control_binding_already_exists")


def _validate_temporal_command_ack(
    raw: dict[str, Any],
    *,
    command: SignedWorkflowCommand,
) -> tuple[int, str]:
    if frozenset(raw) != _TEMPORAL_COMMAND_RESULT_KEYS:
        raise ValueError("workflow_control_command_ack_shape_invalid")
    if raw.get("schema") != COMMAND_RESULT_SCHEMA:
        raise ValueError("workflow_control_command_ack_schema_invalid")
    if raw.get("command_id") != command.command_id:
        raise ValueError("workflow_control_command_ack_identity_mismatch")
    if raw.get("accepted") is not True:
        raise ValueError("workflow_control_command_ack_rejected")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= command.expected_revision:
        raise ValueError("workflow_control_command_ack_revision_invalid")
    status = _bounded_command_ack_text(
        raw.get("status"),
        field_name="status",
        maximum=64,
    ).lower()
    if status not in _TEMPORAL_COMMAND_STATUSES:
        raise ValueError("workflow_control_command_ack_status_invalid")
    _bounded_command_ack_text(
        raw.get("reason_code"),
        field_name="reason_code",
        maximum=512,
        allow_empty=True,
    )
    return revision, status


def _assert_acknowledged_observation(
    status: dict[str, Any],
    *,
    acknowledged_revision: int,
    acknowledged_status: str,
) -> None:
    source = status.get("source_observation")
    if not isinstance(source, dict) or source.get("schema") != TEMPORAL_STATUS_SCHEMA:
        raise ValueError("workflow_control_command_observation_schema_invalid")
    observed_revision = source.get("revision")
    if (
        isinstance(observed_revision, bool)
        or not isinstance(observed_revision, int)
        or observed_revision < acknowledged_revision
    ):
        raise ValueError("workflow_control_command_observation_revision_stale")
    if observed_revision == acknowledged_revision and source.get("status") != acknowledged_status:
        raise ValueError("workflow_control_command_observation_status_conflict")


def _bounded_command_ack_text(
    raw: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(raw, str) or raw != raw.strip() or len(raw) > maximum:
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    if not raw and not allow_empty:
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    if any(not character.isprintable() or character in {"\x00", "\x7f"} for character in raw):
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    return raw


__all__ = [
    "AuthorizedWorkflowBackend",
    "ConfiguredWorkflowBackendBridge",
    "DURABLE_RUN_SIGNAL_SCHEMA",
    "DURABLE_RUN_START_SCHEMA",
    "HubVerifiedDurableCommandPort",
    "InMemoryWorkflowControlBindingStore",
    "ROUTE_CONTROL_AUTHORIZATION_SCHEMA",
    "UnavailableWorkflowRuntimeReleaseAdmission",
    "WorkflowBackendControlFacade",
    "WorkflowBackendDurableRunAdapter",
    "WorkflowControlBindingStore",
    "WorkflowControlBindingOwnerResolver",
    "WorkflowControlRunBinding",
    "WorkflowRuntimeReleaseAdmissionPort",
    "build_workflow_backend_control_facade",
    "get_workflow_backend_control_facade",
    "reset_workflow_backend_control_facade",
]
