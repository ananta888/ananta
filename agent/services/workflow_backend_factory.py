"""Factory for the active WorkflowBackend."""
from __future__ import annotations

import os
from dataclasses import dataclass

from agent.services.local_workflow_backend import local_workflow_backend
from agent.services.temporal_workflow_backend import TemporalWorkflowBackend
from agent.services.workflow_backend import WorkflowBackend
from agent.services.workflow_runtime_fallback_policy import (
    RuntimeFallbackDecision,
    RuntimeFallbackRequest,
    workflow_runtime_fallback_policy,
)

SUPPORTED_WORKFLOW_BACKENDS = frozenset({"local", "temporal"})


class WorkflowBackendConfigurationError(RuntimeError):
    """Raised when backend selection cannot be honored safely."""

    def __init__(
        self,
        *,
        backend: str,
        reason_code: str,
        fallback_decision: RuntimeFallbackDecision | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.backend = backend
        self.reason_code = reason_code
        self.fallback_decision = fallback_decision


@dataclass(frozen=True)
class WorkflowBackendConfig:
    backend: str = "local"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "ananta-workflows"
    temporal_workflow_type: str = "AnantaWorkflow"
    temporal_ui_url: str = "http://localhost:8233"

    @classmethod
    def from_env(cls) -> "WorkflowBackendConfig":
        backend = os.environ.get("ANANTA_ORCHESTRATION_BACKEND") or os.environ.get("ANANTA_WORKFLOW_BACKEND")
        return cls(
            backend=str(backend or "local").strip().lower() or "local",
            temporal_address=str(os.environ.get("ANANTA_TEMPORAL_ADDRESS") or "localhost:7233").strip(),
            temporal_namespace=str(os.environ.get("ANANTA_TEMPORAL_NAMESPACE") or "default").strip() or "default",
            temporal_task_queue=(
                str(os.environ.get("ANANTA_TEMPORAL_TASK_QUEUE") or "ananta-workflows").strip()
                or "ananta-workflows"
            ),
            temporal_workflow_type=(
                str(os.environ.get("ANANTA_TEMPORAL_WORKFLOW_TYPE") or "AnantaWorkflow").strip()
                or "AnantaWorkflow"
            ),
            temporal_ui_url=str(os.environ.get("ANANTA_TEMPORAL_UI_URL") or "http://localhost:8233").strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "temporal": {
                "address": self.temporal_address,
                "namespace": self.temporal_namespace,
                "task_queue": self.temporal_task_queue,
                "workflow_type": self.temporal_workflow_type,
                "ui_url": self.temporal_ui_url,
            },
        }


def get_workflow_backend_config() -> WorkflowBackendConfig:
    return WorkflowBackendConfig.from_env()


def get_workflow_backend(config: WorkflowBackendConfig | None = None) -> WorkflowBackend:
    cfg = config or get_workflow_backend_config()
    if cfg.backend not in SUPPORTED_WORKFLOW_BACKENDS:
        fallback = workflow_runtime_fallback_policy.evaluate(
            RuntimeFallbackRequest.create(
                source_runtime=cfg.backend or "unknown",
                target_runtime="local",
                reason_code="workflow_backend_unknown",
                semantic_class="incompatible",
                source_capabilities=(),
                target_capabilities=("policy", "audit"),
                explicitly_enabled=False,
            )
        )
        raise WorkflowBackendConfigurationError(
            backend=cfg.backend,
            reason_code="workflow_backend_unknown",
            fallback_decision=fallback,
        )
    if cfg.backend == "temporal":
        return TemporalWorkflowBackend(
            address=cfg.temporal_address,
            namespace=cfg.temporal_namespace,
            task_queue=cfg.temporal_task_queue,
            workflow_type=cfg.temporal_workflow_type,
        )
    if cfg.backend == "local":
        return local_workflow_backend
    raise WorkflowBackendConfigurationError(
        backend=cfg.backend,
        reason_code="workflow_backend_selection_unreachable",
    )
