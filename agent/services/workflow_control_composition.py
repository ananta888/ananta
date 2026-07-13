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
import uuid
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
    authoritative_runtime_status,
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
from agent.services.workflow_control_command_verification import (
    HubSignedWorkflowCommandVerifier,
    HubVerifiedDurableCommandPort,
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

_FAILED_START_STATUSES = frozenset({"failed", "degraded", "unavailable", "not_found"})


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
    ) -> None:
        self._backend = backend
        self._bindings = bindings
        self._durable_runs = durable_runs
        self._commands = commands
        self._read_models = read_models
        self._authorization_grants = authorization_grants
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
            status = self._mapping(
                self._durable_runs.start(
                    {
                        "schema": DURABLE_RUN_START_SCHEMA,
                        "tenant_id": principal.tenant_id,
                        "workflow_id": plan.workflow_id,
                        "run_id": run_id,
                        "workflow_request": request.to_dict(),
                    }
                )
            )
        else:
            status = self._mapping(self._backend.start_workflow(request))
        status = authoritative_runtime_status(
            status,
            binding=binding,
            previous=None,
            runtime_id=self.selection_runtime_id,
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
            reason_code=str(status.get("reason") or ""),
        )

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]:
        binding = self._binding_for_run(run_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        self._assert_principal(binding, principal)
        status = self._bindings.last_status(binding.workflow_id)
        if status is None:
            raise LookupError("workflow_control_status_not_found")
        return dict(status)

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        if self._reconciler is None:
            return {"runtime_id": self.selection_runtime_id, "processed": 0, "failed": []}
        return self._reconciler.reconcile_active(limit=limit)

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        binding = self._require_command_binding(command, principal)
        if self._durable_runs is None:
            self._verify_local_command(command, binding)
        self._bindings.claim_command(
            binding.workflow_id,
            expected_revision=command.expected_revision,
            checkpoint_id=command.checkpoint_id,
            command_id=command.command_id,
        )
        try:
            if self._durable_runs is not None:
                status = self._mapping(
                    self._durable_runs.signal(
                        tenant_id=principal.tenant_id,
                        run_id=binding.workflow_id,
                        command={
                            "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                            "command": command.to_dict(),
                        },
                    )
                )
            else:
                self._restore_local_binding(binding)
                signal = WorkflowSignal(
                    name=command.command_type,
                    payload=dict(command.payload),
                    actor=command.actor_id,
                )
                status = self._mapping(
                    self._backend.signal_workflow(binding.workflow_id, signal)
                )
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
        self._bindings.claim_command(
            binding.workflow_id,
            expected_revision=command.expected_revision,
            checkpoint_id=command.checkpoint_id,
            command_id=command.command_id,
        )
        try:
            if self._durable_runs is not None:
                status = self._mapping(
                    self._durable_runs.signal(
                        tenant_id=principal.tenant_id,
                        run_id=binding.workflow_id,
                        command={
                            "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                            "command": command.to_dict(),
                        },
                    )
                )
            else:
                self._restore_local_binding(binding)
                status = self._mapping(
                    self._backend.cancel_workflow(binding.workflow_id, reason=reason)
                )
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
            projected_events = tuple(
                dict(event) for event in events if isinstance(event, dict)
            )
        else:
            events = self._backend.list_workflow_events(workflow_id)
            projected_events = tuple(
                dict(event) for event in events[offset:] if isinstance(event, dict)
            )
        return projected_events

    def _project(
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
        try:
            self._read_models.project(
                binding=binding,
                status=status,
                runtime=self.runtime_id,
                mode=mode or ("durable" if self.runtime_id == "temporal" else "live"),
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
        restore(binding.request, persisted)

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
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._registry = registry

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def bind(self, principal: WorkflowRoutePrincipal) -> "AuthorizedWorkflowBackend":
        return AuthorizedWorkflowBackend(
            control=self._control,
            bridge=self._bridge,
            bindings=self._bindings,
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

        return dict(self._registry.reconcile_active(limit=limit))


class AuthorizedWorkflowBackend:
    """Request-scoped compatibility view; it owns no orchestration state."""

    def __init__(
        self,
        *,
        control: WorkflowControlService,
        bridge: ConfiguredWorkflowBackendBridge,
        bindings: WorkflowControlBindingStore,
        principal: WorkflowPrincipal,
    ) -> None:
        self._control = control
        self._bridge = bridge
        self._bindings = bindings
        self._principal = principal

    @property
    def backend_id(self) -> str:
        return self._bridge.runtime_id

    def start_workflow(self, request: WorkflowRequest) -> dict[str, Any]:
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
        self._bindings.put(binding)
        try:
            self._control.start(
                principal=self._principal,
                plan=plan,
                run_id=run_id,
                authorization_envelope=self._route_authorization(binding),
                preferred_runtime=self._bridge.selection_runtime_id,
                allowed_runtimes=(self._bridge.selection_runtime_id,),
            )
        except Exception:
            self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise
        status = self._bindings.last_status(request.workflow_id)
        if status is None:
            self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
            raise RuntimeError("workflow_control_start_status_missing")
        if str(status.get("status") or "").lower() in _FAILED_START_STATUSES:
            self._bindings.discard(request.workflow_id, plan_hash=plan.plan_hash)
        return status

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        binding = self._bindings.get(workflow_id)
        run_id = binding.run_id if binding is not None else str(workflow_id)
        return dict(
            self._control.query(
                principal=self._principal,
                workflow_id=str(workflow_id),
                run_id=run_id,
            )
        )

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
        if self.backend_id == "temporal":
            self.get_workflow_status(workflow_id)
        command = self._command(
            binding,
            command_type=command_type,
            payload=dict(payload or {}),
            command_id=command_id,
        )
        return dict(self._control.command(principal=self._principal, command=command))

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
        step_id = str(payload.get("step_id") or "").strip()
        if not step_id:
            step_id = next(
                (node.node_id for node in self._plan(binding).nodes if node.gate_id),
                "__workflow__",
            )
        status = self._bindings.last_status(binding.workflow_id) or {}
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

    def _route_authorization(self, binding: WorkflowControlRunBinding) -> dict[str, str]:
        return {
            "schema": ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
            "tenant_id": self._principal.tenant_id,
            "subject_id": self._principal.subject_id,
            "workflow_id": binding.workflow_id,
            "run_id": binding.run_id,
        }

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
    command_key_ring: HmacKeyRing | None = None,
    command_replay_store: ReplayNonceStore | None = None,
    read_model_projector: WorkflowControlReadModelProjector | None = None,
    runtime_health: RuntimeHealthPort | None = None,
    runtime_selection_audit: RuntimeSelectionAuditPort | None = None,
    runtime_profiles: WorkflowRuntimeProfileService | None = None,
    rollout_policies: WorkflowRolloutPolicyService | None = None,
    authorization_grants: WorkflowAuthorizationGrantPort | None = None,
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
        WorkflowBackendDurableRunAdapter(backend, commands=command_port)
        if str(backend.backend_id) == "temporal"
        else None
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
            authorization_grants=(
                authorization_grants
                or InMemoryWorkflowAuthorizationGrantService()
            ),
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
            authorization_grants=(
                authorization_grants or _production_authorization_grants()
            ),
            read_models=resolved_read_models,
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
