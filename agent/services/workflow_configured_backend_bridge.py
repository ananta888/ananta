"""Bridge from the Hub control boundary to the configured workflow runtime backend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from agent.common.audit import log_audit
from agent.services.workflow_authorization_grant_service import WorkflowAuthorizationGrantPort
from agent.services.workflow_backend import WorkflowBackend, WorkflowSignal
from agent.services.workflow_backend_durable_run_adapter import DURABLE_RUN_SIGNAL_SCHEMA, DURABLE_RUN_START_SCHEMA
from agent.services.workflow_configured_bridge_reconciler import ConfiguredBridgeReconciler
from agent.services.workflow_control_authorization_helpers import (
    assert_route_control_envelope,
    register_bound_authorization_grants,
)
from agent.services.workflow_control_bindings import WorkflowControlBindingStore, WorkflowControlRunBinding
from agent.services.workflow_control_command_guards import _initial_start_pending_status, _start_request_id
from agent.services.workflow_control_command_receipts import WorkflowControlCommandRejectedError
from agent.services.workflow_control_command_verification import HubVerifiedDurableCommandPort
from agent.services.workflow_control_dispatch_intents import WorkflowControlDispatchIntentStore
from agent.services.workflow_control_dispatch_service import WorkflowControlDispatchService
from agent.services.workflow_control_read_model_projector import WorkflowControlReadModelProjector
from agent.services.workflow_control_service import RuntimeSelection, WorkflowPrincipal, WorkflowRunHandle
from agent.services.workflow_run_history_paging import page_workflow_run_history
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.ports import DurableRunInfrastructurePort
from agent.services.workflow_runtime_selection_composition import configured_runtime_id
from agent.services.workflow_runtime_status_projection import authoritative_runtime_status
from agent.services.workflow_temporal_command_ack import (
    assert_acknowledged_observation as _assert_acknowledged_observation,
)
from agent.services.workflow_temporal_command_ack import validate_temporal_command_ack as _validate_temporal_command_ack
from agent.services.workflow_terminal_trace_reconciliation import (
    WorkflowTerminalTraceStatePort,
    is_terminal_status,
    status_revision,
)


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
        expected_revision: int | None = None,
        step_id: str | None = None,
        checkpoint_ref: str | None = None,
    ) -> dict[str, Any] | None:
        if self._dispatcher is None:
            return None
        return self._dispatcher.retry_command(
            binding=binding,
            command_id=command_id,
            command_type=command_type,
            payload=payload,
            expected_revision=expected_revision,
            step_id=step_id,
            checkpoint_ref=checkpoint_ref,
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
