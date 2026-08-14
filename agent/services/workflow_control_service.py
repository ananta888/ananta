"""Single Hub-owned control service for every workflow runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import contains_sensitive_keys
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.execution_plan import ExecutionPlan

CONTROL_COMMAND_SCHEMA = "ananta.workflow_control_command.v1"
CONTROL_COMMAND_TYPES = frozenset(
    {
        "approve",
        "reject",
        "edit",
        "request_changes",
        "pause",
        "resume",
        "retry",
        "cancel",
        "parameter_update",
    }
)


@dataclass(frozen=True)
class WorkflowPrincipal:
    tenant_id: str
    subject_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSelection:
    runtime_id: str
    capabilities: frozenset[str]
    mode: str
    reason_code: str
    rejected: tuple[dict[str, str], ...] = ()
    profile_id: str = ""
    audit_ref: str = ""
    runtime_version: str = ""
    runtime_build: str = ""


@dataclass(frozen=True)
class WorkflowRunHandle:
    tenant_id: str
    workflow_id: str
    run_id: str
    runtime_id: str
    status: str
    task_ref: str
    reason_code: str = ""
    schema: str = "ananta.workflow_run_handle.v1"


@dataclass(frozen=True)
class WorkflowControlCommand:
    command_id: str
    command_type: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    checkpoint_id: str
    expected_revision: int
    plan_hash: str
    policy_version: str
    authorization_envelope: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)
    schema: str = CONTROL_COMMAND_SCHEMA

    def validate(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.schema != CONTROL_COMMAND_SCHEMA:
            issues.append("control_command_schema_unsupported")
        if self.command_type not in CONTROL_COMMAND_TYPES:
            issues.append("control_command_type_unsupported")
        for name in (
            "command_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "checkpoint_id",
            "plan_hash",
            "policy_version",
        ):
            if not str(getattr(self, name) or "").strip():
                issues.append(f"{name}_required")
        if not self.authorization_envelope:
            issues.append("control_command_authorization_required")
        if isinstance(self.expected_revision, bool) or self.expected_revision < 0:
            issues.append("control_command_revision_invalid")
        if contains_sensitive_keys(self.payload):
            issues.append("control_command_embedded_secret_denied")
        return tuple(issues)


class WorkflowControlAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        principal: WorkflowPrincipal,
        action: str,
        workflow_id: str,
        run_id: str = "",
    ) -> str: ...


class RuntimeSelectionPort(Protocol):
    def select(
        self,
        *,
        plan: ExecutionPlan,
        preferred_runtime: str,
        allowed_runtimes: tuple[str, ...],
        profile: Any | None = None,
        context: Any | None = None,
    ) -> RuntimeSelection: ...


class RuntimeSelectionProfileResolverPort(Protocol):
    """Resolve immutable Hub-owned runtime profiles by stable identifier."""

    def resolve(self, profile_id: str) -> Any: ...


class WorkflowCommandIssuerPort(Protocol):
    """Issue the immutable Hub decision after authentication and policy checks."""

    def issue(
        self,
        *,
        command_id: str,
        command_type: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        actor_id: str,
        actor_roles: tuple[str, ...],
        payload: dict[str, Any],
    ) -> SignedWorkflowCommand: ...


class HubWorkflowTaskBridge(Protocol):
    """Only this Hub-owned port may create and control runtime tasks."""

    def start(
        self,
        *,
        principal: WorkflowPrincipal,
        plan: ExecutionPlan,
        run_id: str,
        selection: RuntimeSelection,
        authorization_envelope: dict[str, Any],
    ) -> WorkflowRunHandle: ...

    def query(self, *, principal: WorkflowPrincipal, run_id: str) -> dict[str, Any]: ...

    def signal(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]: ...

    def cancel(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]: ...

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]: ...


class WorkflowControlService:
    """Authenticate/authorize/select/delegate without importing a worker runtime."""

    def __init__(
        self,
        *,
        authorization: WorkflowControlAuthorizationPort,
        selection: RuntimeSelectionPort,
        bridge: HubWorkflowTaskBridge,
        runtime_profiles: RuntimeSelectionProfileResolverPort | None = None,
        command_issuer: WorkflowCommandIssuerPort | None = None,
    ) -> None:
        self._authorization = authorization
        self._selection = selection
        self._bridge = bridge
        self._runtime_profiles = runtime_profiles
        self._command_issuer = command_issuer

    def start(
        self,
        *,
        principal: WorkflowPrincipal,
        plan: ExecutionPlan,
        run_id: str,
        authorization_envelope: dict[str, Any],
        preferred_runtime: str = "",
        allowed_runtimes: tuple[str, ...] = (),
        runtime_profile_id: str = "",
    ) -> WorkflowRunHandle:
        plan.assert_valid()
        self._require_binding(principal, tenant_id=plan.tenant_id)
        reason = self._authorization.authorize(
            principal=principal,
            action="start",
            workflow_id=plan.workflow_id,
            run_id=run_id,
        )
        if reason != "allowed":
            raise PermissionError(reason or "workflow_start_denied")
        profile_id = str(runtime_profile_id or "").strip()
        if profile_id:
            if preferred_runtime or allowed_runtimes:
                raise ValueError("runtime_profile_override_denied")
            if self._runtime_profiles is None:
                raise RuntimeError("runtime_profile_resolver_not_configured")
            try:
                profile = self._runtime_profiles.resolve(profile_id)
            except KeyError as exc:
                raise ValueError("runtime_selection_profile_not_found") from exc
            selected = self._selection.select(
                plan=plan,
                preferred_runtime="",
                allowed_runtimes=(),
                profile=profile,
                context=None,
            )
        else:
            # Keep the established explicit-selection call shape compatible for
            # existing adapters while profiles are rolled out incrementally.
            selected = self._selection.select(
                plan=plan,
                preferred_runtime=preferred_runtime,
                allowed_runtimes=allowed_runtimes,
            )
        if selected.mode == "incompatible" and any(
            "runtime_capabilities_missing:" in str(rejected.get("detail") or "") for rejected in selected.rejected
        ):
            # Preserve the established, actionable compatibility error for
            # callers while the common selector retains the richer audited
            # rejection details.  Safety/health/release failures remain
            # selection failures and cannot be mistaken for capability drift.
            missing = sorted(set(plan.capabilities))
            if missing:
                raise RuntimeError("workflow_runtime_incompatible:" + ",".join(missing))
        if selected.mode in {"blocked", "incompatible"}:
            raise RuntimeError(
                f"workflow_runtime_selection_{selected.mode}:{selected.reason_code or 'runtime_selection_failed'}"
            )
        missing = set(plan.capabilities) - set(selected.capabilities)
        if missing:
            raise RuntimeError("workflow_runtime_incompatible:" + ",".join(sorted(missing)))
        if selected.mode not in {"live", "durable"}:
            raise RuntimeError("workflow_runtime_not_executable")
        return self._bridge.start(
            principal=principal,
            plan=plan,
            run_id=str(run_id),
            selection=selected,
            authorization_envelope=dict(authorization_envelope),
        )

    def query(self, *, principal: WorkflowPrincipal, workflow_id: str, run_id: str) -> dict[str, Any]:
        self._authorize_bound(principal, "query", workflow_id, run_id)
        return self._bridge.query(principal=principal, run_id=run_id)

    def history(
        self,
        *,
        principal: WorkflowPrincipal,
        workflow_id: str,
        run_id: str,
        after_sequence: int = 0,
    ) -> tuple[dict[str, Any], ...]:
        self._authorize_bound(principal, "history", workflow_id, run_id)
        return self._bridge.history(
            principal=principal,
            run_id=run_id,
            after_sequence=max(0, int(after_sequence)),
        )

    def command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: WorkflowControlCommand,
    ) -> dict[str, Any]:
        signed = self.prepare_command(principal=principal, command=command)
        return self.dispatch_command(principal=principal, command=signed)

    def prepare_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: WorkflowControlCommand,
    ) -> SignedWorkflowCommand:
        """Validate, authorize and sign without crossing the mutation boundary."""

        issues = command.validate()
        if issues:
            raise ValueError(";".join(issues))
        self._require_binding(principal, tenant_id=command.tenant_id)
        self._authorize_bound(principal, command.command_type, command.workflow_id, command.run_id)
        if self._command_issuer is None:
            raise RuntimeError("workflow_command_issuer_not_configured")
        return self._command_issuer.issue(
            command_id=command.command_id,
            command_type=command.command_type,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            step_id=command.step_id,
            checkpoint_id=command.checkpoint_id,
            expected_revision=command.expected_revision,
            plan_hash=command.plan_hash,
            policy_version=command.policy_version,
            actor_id=principal.subject_id,
            actor_roles=principal.roles,
            payload=dict(command.payload),
        )

    def dispatch_command(
        self,
        *,
        principal: WorkflowPrincipal,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        """Dispatch one already admitted signed command to its bound runtime."""

        if command.command_type == "cancel":
            return self._bridge.cancel(principal=principal, command=command)
        return self._bridge.signal(principal=principal, command=command)

    def _authorize_bound(
        self,
        principal: WorkflowPrincipal,
        action: str,
        workflow_id: str,
        run_id: str,
    ) -> None:
        reason = self._authorization.authorize(
            principal=principal,
            action=action,
            workflow_id=workflow_id,
            run_id=run_id,
        )
        if reason != "allowed":
            raise PermissionError(reason or f"workflow_{action}_denied")

    @staticmethod
    def _require_binding(principal: WorkflowPrincipal, *, tenant_id: str) -> None:
        if not principal.tenant_id or principal.tenant_id != str(tenant_id):
            raise PermissionError("workflow_tenant_binding_mismatch")
        if not principal.subject_id:
            raise PermissionError("workflow_subject_required")
