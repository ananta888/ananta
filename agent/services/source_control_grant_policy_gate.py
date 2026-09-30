"""Destination and Context Policy authorization for new grants.

Before a grant is persisted, the destination must resolve in the actor's
scope and the active Context Policy (matching the caller's ETag) must allow
the preset's operation and transformation. ``GrantCreationPolicyGate`` owns
exactly that decision (SRP) and depends only on the two ports (DIP).
"""

from __future__ import annotations

from agent.services.context_policy_lifecycle import (
    ContextPolicyLifecycleError,
    ContextPolicyVersion,
)
from agent.services.source_control_grant_admin_contracts import (
    ActiveContextPolicyPort,
    GrantAdminActor,
    GrantCreateRequest,
    GrantPreset,
    ScopedDestinationCatalogPort,
    SourceControlGrantAdminError,
)
from agent.services.source_control_grant_admin_rules import (
    policy_actor,
    preview_allows_transformation,
)
from ananta_contracts.source_control import DestinationDescriptor


class GrantCreationPolicyGate:
    """Resolves the scoped destination and the active, permitting policy."""

    def __init__(
        self,
        *,
        destinations: ScopedDestinationCatalogPort,
        policies: ActiveContextPolicyPort,
    ) -> None:
        self._destinations = destinations
        self._policies = policies

    def authorize(
        self,
        *,
        actor: GrantAdminActor,
        request: GrantCreateRequest,
        preset: GrantPreset,
        normalized_etag: str,
    ) -> tuple[DestinationDescriptor, ContextPolicyVersion]:
        destination = self._destinations.get(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            destination_id=request.destination_id,
        )
        if (
            destination is None
            or destination.destination_id != request.destination_id
        ):
            raise SourceControlGrantAdminError(
                "grant_resource_not_found", status_code=404
            )
        scoped_actor = policy_actor(actor)
        try:
            policy = self._policies.active(
                actor=scoped_actor, policy_id=request.policy_id
            )
        except ContextPolicyLifecycleError as exc:
            raise SourceControlGrantAdminError(
                "grant_active_policy_missing", status_code=409
            ) from exc
        if (
            policy.tenant_id != actor.tenant_id
            or policy.project_id != actor.project_id
            or policy.policy_id != request.policy_id
            or policy.state != "active"
        ):
            raise SourceControlGrantAdminError(
                "grant_active_policy_missing", status_code=409
            )
        if normalized_etag != policy.etag:
            raise SourceControlGrantAdminError(
                "grant_policy_version_conflict", status_code=412
            )
        try:
            preview = self._policies.preview(
                actor=scoped_actor,
                policy_id=policy.policy_id,
                version=policy.version,
                source_revision_id=request.source_revision_id,
                destination_id=destination.destination_id,
                operation=preset.operation,
                transformation=preset.transformation,
            )
        except ContextPolicyLifecycleError as exc:
            raise SourceControlGrantAdminError(
                "grant_policy_evaluation_failed", status_code=409
            ) from exc
        if preview.policy_digest != policy.policy_digest:
            raise SourceControlGrantAdminError(
                "grant_policy_snapshot_mismatch", status_code=409
            )
        if not preview_allows_transformation(
            decision=preview.decision,
            transformation=preset.transformation,
        ):
            reason = (
                "grant_policy_approval_required"
                if preview.decision == "approval_required"
                else "grant_policy_denied"
            )
            raise SourceControlGrantAdminError(reason, status_code=403)
        return destination, policy
