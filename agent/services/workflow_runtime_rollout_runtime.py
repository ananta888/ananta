"""Fail-closed production composition for workflow-runtime live promotion."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agent.services.workflow_backend_factory import (
    get_workflow_backend,
    get_workflow_backend_config,
)
from agent.services.workflow_control_production_composition import (
    production_release_admission,
    production_runtime_health,
)
from agent.services.workflow_runtime_performance_gate import (
    JsonWorkflowRolloutPerformanceEvidenceStore,
)
from agent.services.workflow_runtime_rollout_persistence import (
    SQLAlchemyWorkflowRolloutPolicyStore,
)
from agent.services.workflow_runtime_rollout_service import (
    ApprovalRequestWorkflowPromotionApproval,
    WorkflowRolloutPolicyService,
    WorkflowRuntimePromotionService,
)
from agent.services.workflow_runtime_selection_composition import (
    build_configured_workflow_runtime_selection,
)
from agent.services.workflow_shadow_comparison_service import (
    HubEventWorkflowShadowComparisonProducer,
    HubEventWorkflowShadowEvidenceService,
    JsonWorkflowShadowComparisonEvidenceStore,
    OwnerOnlyJsonWorkflowShadowEvidencePublisher,
    WorkflowShadowComparisonService,
)


class WorkflowRuntimeRolloutConfigurationError(RuntimeError):
    pass


_LOCK = threading.RLock()
_SERVICE: WorkflowRuntimePromotionService | None = None
_KEY: tuple[str, str, str, str] | None = None


def get_workflow_runtime_promotion_service() -> WorkflowRuntimePromotionService:
    global _KEY, _SERVICE
    evidence_path = _evidence_path()
    shadow_evidence_path = _shadow_evidence_path()
    source_revision = str(os.getenv("ANANTA_SOURCE_REVISION") or "").strip()
    if not source_revision:
        raise WorkflowRuntimeRolloutConfigurationError("workflow_runtime_source_revision_required")
    config = get_workflow_backend_config()
    key = (
        config.backend,
        str(evidence_path),
        str(shadow_evidence_path),
        source_revision,
    )
    if _SERVICE is not None and _KEY == key:
        return _SERVICE
    with _LOCK:
        if _SERVICE is None or _KEY != key:
            from agent.database import engine
            from agent.services.approval_request_service import (
                get_approval_request_service,
            )
            from agent.services.workflow_hub_task_gateway_runtime import (
                get_workflow_authorization_key_ring,
            )

            backend = get_workflow_backend(config)
            selection = build_configured_workflow_runtime_selection(
                backend,
                health=production_runtime_health(backend),
                release_evidence=production_release_admission(backend),
            )
            policies = WorkflowRolloutPolicyService(SQLAlchemyWorkflowRolloutPolicyStore(engine))
            key_ring = get_workflow_authorization_key_ring()
            _SERVICE = WorkflowRuntimePromotionService(
                policies=policies,
                selection=selection,
                performance=JsonWorkflowRolloutPerformanceEvidenceStore(
                    evidence_path,
                    expected_source_revision=source_revision,
                ),
                shadow_comparison=JsonWorkflowShadowComparisonEvidenceStore(
                    shadow_evidence_path,
                    key_ring=key_ring,
                    expected_source_revision=source_revision,
                ),
                approval=ApprovalRequestWorkflowPromotionApproval(get_approval_request_service()),
                evidence_keys=key_ring,
                expected_source_revision=source_revision,
            )
            _KEY = key
    return _SERVICE


def reset_workflow_runtime_promotion_service() -> None:
    global _KEY, _SERVICE
    with _LOCK:
        _SERVICE = None
        _KEY = None


def build_workflow_shadow_evidence_service() -> HubEventWorkflowShadowEvidenceService:
    """Compose the production Hub event store, signer, comparator and publisher."""

    from agent.database import engine
    from agent.services.workflow_hub_task_gateway_runtime import (
        get_workflow_authorization_key_ring,
    )
    from agent.services.workflow_runtime.sqlalchemy_event_stores import (
        SQLAlchemyEventStore,
    )

    key_ring = get_workflow_authorization_key_ring()
    producer = HubEventWorkflowShadowComparisonProducer(
        events=SQLAlchemyEventStore(engine, publish_to_outbox=False),
        comparison=WorkflowShadowComparisonService(key_ring=key_ring),
    )
    return HubEventWorkflowShadowEvidenceService(
        producer=producer,
        publisher=OwnerOnlyJsonWorkflowShadowEvidencePublisher(_shadow_evidence_path()),
    )


def _evidence_path() -> Path:
    raw = str(os.getenv("ANANTA_WORKFLOW_RUNTIME_PERFORMANCE_EVIDENCE_FILE") or "").strip()
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise WorkflowRuntimeRolloutConfigurationError("workflow_runtime_performance_evidence_absolute_path_required")
    return path


def _shadow_evidence_path() -> Path:
    raw = str(os.getenv("ANANTA_WORKFLOW_RUNTIME_SHADOW_EVIDENCE_FILE") or "").strip()
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise WorkflowRuntimeRolloutConfigurationError("workflow_runtime_shadow_evidence_absolute_path_required")
    return path


__all__ = [
    "WorkflowRuntimeRolloutConfigurationError",
    "build_workflow_shadow_evidence_service",
    "get_workflow_runtime_promotion_service",
    "reset_workflow_runtime_promotion_service",
]
