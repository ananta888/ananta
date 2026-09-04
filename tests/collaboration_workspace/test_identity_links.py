from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.collaboration_workspace_store import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_digest
from tests.collaboration_workspace.helpers import actor, service


def test_external_identity_link_rotation_and_unlink_are_cas_bound(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Identity",
        owner=actor(),
        workspace_id="workspace-a",
    )
    external = actor("external-a", kind="external_actor", authority="bridge", subject="bridge-registration-a")
    workspaces.put_membership(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor=external,
        role="guest",
        status="active",
    )
    linked = workspaces.put_external_identity(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor_binding_id="external-a",
        provider="nostr",
        external_subject="npub-a",
        key_fingerprint=canonical_digest("public-key-a"),
        status="active",
        expected_revision=0,
    )
    assert (linked["revision"], linked["reason_code"]) == (1, "identity_linked")
    replay = workspaces.put_external_identity(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor_binding_id="external-a",
        provider="nostr",
        external_subject="npub-a",
        key_fingerprint=canonical_digest("public-key-a"),
        status="active",
        expected_revision=1,
    )
    assert replay["replayed"] is True
    with pytest.raises(CollaborationStoreConflict, match="revision_conflict"):
        workspaces.put_external_identity(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            actor_binding_id="external-a",
            provider="nostr",
            external_subject="npub-a",
            key_fingerprint=canonical_digest("public-key-b"),
            status="active",
            expected_revision=0,
        )
    rotated = workspaces.put_external_identity(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor_binding_id="external-a",
        provider="nostr",
        external_subject="npub-a",
        key_fingerprint=canonical_digest("public-key-b"),
        status="active",
        expected_revision=1,
    )
    assert rotated["reason_code"] == "identity_key_rotated"
    unlinked = workspaces.put_external_identity(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_actor_id="human-user-a",
        actor_binding_id="external-a",
        provider="nostr",
        external_subject="npub-a",
        key_fingerprint=canonical_digest("public-key-b"),
        status="revoked",
        expected_revision=2,
    )
    assert (unlinked["revision"], unlinked["reason_code"]) == (3, "identity_unlinked")


def test_external_identity_cannot_be_linked_to_two_actors(tmp_path: Path) -> None:
    workspaces = service(tmp_path / "collaboration.sqlite3")
    workspaces.create_workspace(
        tenant_id="tenant-a",
        principal_id="user-a",
        title="Identity",
        owner=actor(),
        workspace_id="workspace-a",
    )
    for actor_id in ("external-a", "external-b"):
        workspaces.put_membership(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            principal_actor_id="human-user-a",
            actor=actor(actor_id, kind="external_actor", authority="bridge", subject=f"registration-{actor_id}"),
            role="guest",
            status="active",
        )
    common = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "principal_actor_id": "human-user-a",
        "provider": "nostr",
        "external_subject": "npub-shared",
        "key_fingerprint": canonical_digest("shared-key"),
        "status": "active",
        "expected_revision": 0,
    }
    workspaces.put_external_identity(actor_binding_id="external-a", **common)
    with pytest.raises(CollaborationStoreConflict, match="external_identity_conflict"):
        workspaces.put_external_identity(actor_binding_id="external-b", **common)
