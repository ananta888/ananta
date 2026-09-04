"""Hub Evidence Registry binding for grounded collaboration projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort
from ananta_contracts.collaboration_workspace import require_id


class CollaborationEvidencePolicy:
    GROUNDED_EVENT_TYPES = frozenset(
        {"decision.recorded", "review.recorded", "task.projected", "workflow.projected", "git.projected"}
    )

    def __init__(self, registry: EvidenceIdentityRegistryPort | None) -> None:
        self._registry = registry

    def require_verified(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        source_refs: tuple[str, ...],
        run_refs: tuple[str, ...],
    ) -> None:
        if event_type not in self.GROUNDED_EVENT_TYPES:
            return
        if self._registry is None:
            raise PermissionError("collaboration_hub_evidence_registry_required")
        if len(run_refs) != 1:
            raise PermissionError("collaboration_single_run_evidence_required")
        required = {"project_id", "task_id", "repository_revision", "evidence_scope"}
        if not required.issubset(payload):
            raise PermissionError("collaboration_evidence_binding_incomplete")
        project_id = require_id(payload["project_id"], "project_id")
        task_id = require_id(payload["task_id"], "task_id")
        revision = str(payload["repository_revision"] or "").strip().lower()
        scope = str(payload["evidence_scope"] or "").strip()
        if scope not in {"local", "external", "production"}:
            raise PermissionError("collaboration_evidence_scope_invalid")
        verification = self._registry.verify_release_binding(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_refs[0],
            required_scope=scope,
            task_id=task_id,
            repository_revision=revision,
            source_ids=source_refs,
        )
        if not verification.verified:
            raise PermissionError(f"collaboration_evidence_unverified:{verification.reason_code}")
        if payload.get("workspace_id") not in {None, workspace_id}:
            raise PermissionError("collaboration_evidence_workspace_mismatch")


__all__ = ["CollaborationEvidencePolicy"]
