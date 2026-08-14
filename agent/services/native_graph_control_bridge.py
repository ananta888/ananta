"""Hub control bridge for the production Ananta Native graph runtime.

The bridge adapts the legacy visual-process API shape to the real Hub-owned
Native orchestrator.  It contains no worker implementation and every executable
node is delegated by :class:`NativeGraphOrchestrator` through ``HubTaskQueuePort``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from agent.common.audit import log_audit
from agent.services.native_graph_models import NativeGraphRequest, NativeGraphResult
from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
from agent.services.workflow_control_bindings import (
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandRejectedError,
)
from agent.services.workflow_control_read_model_projector import (
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_control_service import (
    RuntimeSelection,
    WorkflowPrincipal,
    WorkflowRunHandle,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import (
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)

NATIVE_COMPATIBILITY_BACKEND_ID = "local"
NATIVE_RUNTIME_ID = "ananta-native"
_MAX_STATUS_EVENTS = 256


class NativeGraphWorkflowControlBridge:
    """Translate Hub control operations to one persistent Native orchestrator."""

    runtime_id = NATIVE_COMPATIBILITY_BACKEND_ID
    selection_runtime_id = NATIVE_RUNTIME_ID

    def __init__(
        self,
        *,
        orchestrator: NativeGraphOrchestrator,
        bindings: WorkflowControlBindingStore,
        read_models: WorkflowControlReadModelProjector | None = None,
        reconciler_id: str = "",
    ) -> None:
        self._orchestrator = orchestrator
        self._bindings = bindings
        self._read_models = read_models
        self._reconciler_id = str(reconciler_id or f"native-reconciler-{uuid.uuid4().hex}")

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
        self._assert_route_envelope(
            authorization_envelope,
            principal=principal,
            workflow_id=plan.workflow_id,
            run_id=run_id,
        )
        if selection.runtime_id != self.selection_runtime_id:
            raise ValueError("workflow_control_runtime_binding_mismatch")

        result = self._orchestrator.start(self._request(binding, plan=plan))
        status = self._status(result)
        self._bindings.record_status(binding.workflow_id, status)
        self._project(
            binding,
            status,
            mode=selection.mode,
            capabilities=tuple(sorted(selection.capabilities)),
        )
        return WorkflowRunHandle(
            tenant_id=principal.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=run_id,
            runtime_id=self.selection_runtime_id,
            status=result.status,
            task_ref=result.control_task_id,
            reason_code=result.reason_code,
        )

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]:
        binding = self._require_run_binding(run_id)
        self._assert_principal(binding, principal)
        status = self._bindings.last_status(binding.workflow_id)
        if status is None:
            raise LookupError("workflow_control_status_not_found")
        return dict(status)

    def reconcile_active(self, *, limit: int = 100) -> dict[str, Any]:
        """Tick persisted active runs from a Hub-owned background service."""

        processed = 0
        failed: list[dict[str, str]] = []
        claimed = list(
            self._bindings.claim_reconcilable(
                runtime_id=self.selection_runtime_id,
                owner_id=self._reconciler_id,
                lease_seconds=30.0,
                limit=limit,
            )
        )
        if len(claimed) < limit and self.runtime_id != self.selection_runtime_id:
            claimed.extend(
                self._bindings.claim_reconcilable(
                    runtime_id=self.runtime_id,
                    owner_id=self._reconciler_id,
                    lease_seconds=30.0,
                    limit=limit - len(claimed),
                )
            )
        for binding in claimed:
            try:
                previous = self._bindings.last_status(binding.workflow_id) or {}
                expected_revision = int(previous.get("revision") or 0)
                expected_checkpoint = str(previous.get("checkpoint_ref") or binding.checkpoint_id)
                result = self._orchestrator.advance(self._request(binding))
                status = self._status(result)
                self._bindings.finish_reconciliation(
                    binding.workflow_id,
                    owner_id=self._reconciler_id,
                    expected_revision=expected_revision,
                    expected_checkpoint_ref=expected_checkpoint,
                    status=status,
                )
                self._project(binding, status)
                processed += 1
            except Exception as exc:  # one run cannot stop reconciliation of others
                self._bindings.release_reconciliation(
                    binding.workflow_id,
                    owner_id=self._reconciler_id,
                )
                failed.append(
                    {
                        "workflow_id": binding.workflow_id,
                        "error_type": type(exc).__name__,
                    }
                )
        return {
            "runtime_id": self.selection_runtime_id,
            "processed": processed,
            "failed": failed,
        }

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        return self._apply_command(principal=principal, command=command)

    def cancel(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        if command.command_type != "cancel":
            raise ValueError("native_cancel_command_required")
        return self._apply_command(principal=principal, command=command)

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        binding = self._require_run_binding(run_id)
        self._assert_principal(binding, principal)
        events = self._orchestrator.stream(
            self._request(binding),
            after_sequence=max(0, int(after_sequence)),
        )
        return tuple(event.to_dict() for event in events)

    def _apply_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
        admitted_replay: bool = False,
    ) -> dict[str, Any]:
        try:
            binding = self._require_command_binding(command, principal)
            self._bindings.claim_command(
                binding.workflow_id,
                expected_revision=command.expected_revision,
                checkpoint_id=command.checkpoint_id,
                command_id=command.command_id,
            )
            result = self._orchestrator.resume(
                self._request(binding),
                command=command,
                admitted_replay=admitted_replay,
            )
            status = self._status(result)
            self._bindings.finish_command(
                binding.workflow_id,
                command_id=command.command_id,
                status=status,
            )
        except (PermissionError, ValueError) as exc:
            self._bindings.release_command(
                command.workflow_id,
                command_id=command.command_id,
            )
            raise WorkflowControlCommandRejectedError(str(exc)) from exc
        except Exception:
            self._bindings.release_command(
                command.workflow_id,
                command_id=command.command_id,
            )
            raise
        self._project(binding, status)
        return status

    def recover_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        """Adopt or replay one Hub-admitted command from its durable checkpoint."""

        return self._apply_command(
            principal=principal,
            command=command,
            admitted_replay=True,
        )

    def _request(
        self,
        binding: WorkflowControlRunBinding,
        *,
        plan: ExecutionPlan | None = None,
    ) -> NativeGraphRequest:
        metadata = dict(binding.request.metadata)
        input_candidate = metadata.get("input_data")
        if not isinstance(input_candidate, Mapping):
            input_candidate = metadata.get("parameters")
        input_data = dict(input_candidate) if isinstance(input_candidate, Mapping) else {}
        raw_secret_refs = metadata.get("secret_refs")
        secret_refs = (
            tuple(sorted({str(value).strip() for value in raw_secret_refs if str(value).strip()}))
            if isinstance(raw_secret_refs, (list, tuple))
            else ()
        )
        if plan is not None:
            resolved_plan = plan
        elif binding.execution_plan:
            resolved_plan = ExecutionPlan.from_mapping(dict(binding.execution_plan))
        else:
            resolved_plan = WorkflowRequestExecutionPlanAdapter.adapt(
                binding.request,
                tenant_id=binding.tenant_id,
                policy_version=binding.policy_version,
            )
        return NativeGraphRequest(
            plan=resolved_plan,
            run_id=binding.run_id,
            control_task_id=self._control_task_id(binding.run_id),
            input_data=input_data,
            secret_refs=secret_refs,
            tenant_parallel_limit=self._parallel_limit(metadata.get("tenant_parallel_limit"), default=4),
            worker_parallel_limit=self._parallel_limit(metadata.get("worker_parallel_limit"), default=4),
        )

    def _status(self, result: NativeGraphResult) -> dict[str, Any]:
        runtime = dict(result.checkpoint.state.runtime_metadata)
        completed = set(runtime.get("completed") or ())
        skipped = set(runtime.get("skipped") or ())
        failed = {str(key): str(value) for key, value in dict(runtime.get("failed") or {}).items()}
        running = set(dict(runtime.get("running") or {}))
        gate_nodes = set(dict(runtime.get("open_gates") or {}).values())
        plan = result.effective_plan or ExecutionPlan.from_mapping(
            dict(result.checkpoint.state.business_data.get("effective_plan") or {})
        )
        steps = []
        for node in plan.nodes:
            if node.node_id in completed:
                state = "completed"
            elif node.node_id in skipped:
                state = "skipped"
            elif node.node_id in failed:
                state = "failed"
            elif node.node_id in running:
                state = "running"
            elif node.node_id in gate_nodes:
                state = "waiting_for_approval"
            else:
                state = "pending"
            steps.append(
                {
                    "id": node.node_id,
                    "step_id": node.node_id,
                    "task_kind": node.task_kind,
                    "status": state,
                    "reason_code": failed.get(node.node_id, ""),
                    "consumes": list(node.input_artifacts),
                    "produces": list(node.output_artifacts),
                }
            )
        events = self._orchestrator.stream(
            NativeGraphRequest(
                plan=plan,
                run_id=result.run_id,
                control_task_id=result.control_task_id,
                input_data=dict(result.checkpoint.state.business_data.get("input_data") or {}),
                secret_refs=tuple(result.checkpoint.state.secret_refs),
                tenant_parallel_limit=int(runtime.get("tenant_parallel_limit") or 1),
                worker_parallel_limit=int(runtime.get("worker_parallel_limit") or 1),
            ),
            after_sequence=max(0, result.event_cursor - _MAX_STATUS_EVENTS),
            limit=_MAX_STATUS_EVENTS,
        )
        return {
            "schema": "ananta.workflow_backend_status.v1",
            # Preserve the public compatibility backend while exposing the real
            # runtime explicitly.  No execution is performed by LocalWorkflowBackend.
            "backend": self.runtime_id,
            "runtime_id": result.runtime_id,
            "runtime_version": result.runtime_version,
            "workflow_id": result.workflow_id,
            "run_id": result.run_id,
            "status": result.status,
            "reason": result.reason_code,
            "reason_code": result.reason_code,
            "revision": result.checkpoint.revision,
            "checkpoint_ref": result.checkpoint.checkpoint_id,
            "plan_hash": result.checkpoint.plan_hash,
            "event_cursor": result.event_cursor,
            "hub_task_id": result.control_task_id,
            "steps": steps,
            "open_gates": list(result.open_gates),
            "artifact_refs": dict(result.artifact_refs),
            "events": [event.to_dict() for event in events],
            "updated_at": result.checkpoint.created_at,
        }

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
        if str(status.get("checkpoint_ref") or binding.checkpoint_id) != command.checkpoint_id:
            raise PermissionError("workflow_control_checkpoint_binding_mismatch")
        try:
            revision = int(status.get("revision", 0))
        except (TypeError, ValueError) as exc:
            raise PermissionError("workflow_control_revision_binding_invalid") from exc
        if revision != command.expected_revision:
            raise PermissionError("workflow_control_revision_binding_mismatch")
        return binding

    def _project(
        self,
        binding: WorkflowControlRunBinding,
        status: dict[str, Any],
        *,
        mode: str = "live",
        capabilities: tuple[str, ...] = (),
        events: tuple[dict[str, Any], ...] = (),
    ) -> None:
        if self._read_models is None:
            return
        try:
            self._read_models.project_canonical(
                binding=binding,
                status=status,
                runtime=self.selection_runtime_id,
                mode=mode,
                capabilities=capabilities,
            )
        except Exception as exc:  # projection is rebuildable from canonical events
            log_audit(
                "workflow_runtime_read_model_projection_failed",
                {
                    "tenant_id": binding.tenant_id,
                    "workflow_id": binding.workflow_id,
                    "run_id": binding.run_id,
                    "runtime": self.selection_runtime_id,
                    "error_type": type(exc).__name__,
                },
            )

    def _require_binding(self, workflow_id: str) -> WorkflowControlRunBinding:
        binding = self._bindings.get(workflow_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return binding

    def _require_run_binding(self, run_id: str) -> WorkflowControlRunBinding:
        binding = self._bindings.get_by_run_id(run_id)
        if binding is None:
            raise LookupError("workflow_control_binding_not_found")
        return binding

    @staticmethod
    def _assert_binding(
        binding: WorkflowControlRunBinding,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        plan_hash: str,
        policy_version: str,
    ) -> None:
        NativeGraphWorkflowControlBridge._assert_principal(binding, principal)
        if binding.run_id != str(run_id):
            raise PermissionError("workflow_control_run_binding_mismatch")
        if binding.plan_hash != str(plan_hash):
            raise PermissionError("workflow_control_plan_binding_mismatch")
        if binding.policy_version != str(policy_version):
            raise PermissionError("workflow_control_policy_binding_mismatch")

    @staticmethod
    def _assert_principal(
        binding: WorkflowControlRunBinding,
        principal: WorkflowPrincipal,
    ) -> None:
        if binding.tenant_id != principal.tenant_id or binding.subject_id != principal.subject_id:
            raise PermissionError("workflow_control_principal_binding_mismatch")

    @staticmethod
    def _assert_route_envelope(
        envelope: dict[str, Any],
        *,
        principal: WorkflowPrincipal,
        workflow_id: str,
        run_id: str,
    ) -> None:
        expected = {
            "schema": "ananta.workflow_route_control.v1",
            "tenant_id": principal.tenant_id,
            "subject_id": principal.subject_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
        }
        if any(str(envelope.get(key) or "") != value for key, value in expected.items()):
            raise PermissionError("workflow_control_authorization_binding_mismatch")

    @staticmethod
    def _control_task_id(run_id: str) -> str:
        return f"native-control:{str(run_id).strip()}"

    @staticmethod
    def _parallel_limit(value: Any, *, default: int) -> int:
        try:
            parsed = int(value if value is not None else default)
        except (TypeError, ValueError) as exc:
            raise ValueError("native_graph_parallel_limit_invalid") from exc
        if parsed < 1 or parsed > 64:
            raise ValueError("native_graph_parallel_limit_invalid")
        return parsed


__all__ = [
    "NATIVE_COMPATIBILITY_BACKEND_ID",
    "NATIVE_RUNTIME_ID",
    "NativeGraphWorkflowControlBridge",
]
