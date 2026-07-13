"""Release-admission contracts and the deprecated configured selector."""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.workflow_backend import WorkflowBackend
from agent.services.workflow_control_service import RuntimeSelection
from agent.services.workflow_runtime.execution_plan import ExecutionPlan

PROTECTED_RUNTIME_CAPABILITIES = frozenset(
    {"audit", "authorization", "policy", "side_effect_guard"}
)


class WorkflowRuntimeReleaseAdmissionPort(Protocol):
    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        runtime_id: str,
        runtime_version: str,
        required_capabilities: frozenset[str],
    ) -> tuple[bool, str]: ...


class UnavailableWorkflowRuntimeReleaseAdmission:
    """Fail-closed admission used when immutable release evidence is absent."""

    def evaluate(self, **_: Any) -> tuple[bool, str]:
        return False, "runtime_release_evidence_unavailable"


class ConfiguredBackendRuntimeSelection:
    """Compatibility-only selector retained for callers during migration.

    Production composition uses ``WorkflowRuntimeSelectionService``.  Keeping
    this adapter separate makes that migration explicit and prevents new Hub
    code from depending on a configured-backend shortcut.
    """

    def __init__(
        self,
        backend: WorkflowBackend,
        *,
        release_admission: WorkflowRuntimeReleaseAdmissionPort | None = None,
    ) -> None:
        self._runtime_id = str(backend.backend_id)
        self._release_admission = release_admission

    def select(
        self,
        *,
        plan: ExecutionPlan,
        preferred_runtime: str,
        allowed_runtimes: tuple[str, ...],
        profile: Any | None = None,
        context: Any | None = None,
    ) -> RuntimeSelection:
        del profile, context
        allowed = set(allowed_runtimes or (self._runtime_id,))
        if preferred_runtime not in {"", self._runtime_id} or self._runtime_id not in allowed:
            return RuntimeSelection(
                runtime_id="",
                capabilities=frozenset(),
                mode="blocked",
                reason_code="configured_backend_selection_mismatch",
            )
        required_capabilities = frozenset(plan.capabilities) | PROTECTED_RUNTIME_CAPABILITIES
        if self._runtime_id == "temporal":
            admission = (
                self._release_admission
                or UnavailableWorkflowRuntimeReleaseAdmission()
            )
            try:
                admitted, reason_code = admission.evaluate(
                    plan=plan,
                    runtime_id="temporal",
                    runtime_version="1.0.0",
                    required_capabilities=required_capabilities,
                )
            except Exception:
                admitted, reason_code = (
                    False,
                    "runtime_release_evidence_unavailable",
                )
            if not admitted:
                return RuntimeSelection(
                    runtime_id="",
                    capabilities=frozenset(),
                    mode="blocked",
                    reason_code=str(
                        reason_code or "runtime_release_evidence_invalid"
                    ),
                )
            capabilities = required_capabilities
        else:
            capabilities = PROTECTED_RUNTIME_CAPABILITIES
        return RuntimeSelection(
            runtime_id=self._runtime_id,
            capabilities=capabilities,
            mode="durable" if self._runtime_id == "temporal" else "live",
            reason_code="configured_backend_selected",
        )


__all__ = [
    "ConfiguredBackendRuntimeSelection",
    "PROTECTED_RUNTIME_CAPABILITIES",
    "UnavailableWorkflowRuntimeReleaseAdmission",
    "WorkflowRuntimeReleaseAdmissionPort",
]
