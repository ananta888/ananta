from __future__ import annotations

from agent.repositories.semantic_media_capability_grant_repository import (
    InMemorySemanticMediaCapabilityGrantRepository,
)
from agent.services.semantic_compute_consent import (
    CapabilityGrantComputeConsentAuthority,
    ComputeConsentContext,
)
from agent.services.semantic_media_permission_service import SemanticMediaPermissionService


def test_current_compute_capability_grant_is_revalidated_after_revoke() -> None:
    now = [1_000.0]
    permissions = SemanticMediaPermissionService(
        b"semantic-compute-consent-test-key" * 2,
        repository=InMemorySemanticMediaCapabilityGrantRepository(),
        clock=lambda: now[0],
    )
    grant = permissions.issue(
        authorised_capabilities={"compute"},
        owner_id="owner-a",
        tenant_id="tenant-a",
        subject_id="peer-a",
        subject_role="participant",
        capability="compute",
        scope_kind="room",
        scope_id="room-a",
        direction="egress",
        data_type="application/vnd.ananta.semantic-media-control+json",
        purpose="semantic_media_control",
        epoch=1,
        expires_at=1_300.0,
        idempotency_key="compute-consent-grant-a",
    )
    authority = CapabilityGrantComputeConsentAuthority(permissions)
    context = ComputeConsentContext(
        tenant_id="tenant-a",
        owner_subject="owner-a",
        contract_id="contract-a",
        contract_digest="a" * 64,
        session_id="session-a",
        room_id="room-a",
        epoch=1,
        candidate_id="peer-a",
        task_type="visual_extract",
        role="primary",
    )

    assert authority.authorized(context) is True
    permissions.revoke(grant.grant_id, tenant_id="tenant-a", actor_id="peer-a")
    assert authority.authorized(context) is False
