"""Small role/capability policy for Hub-owned collaboration workspaces."""

from __future__ import annotations

from collections.abc import Mapping


class CollaborationPolicyDenied(PermissionError):
    pass


class CollaborationWorkspacePolicy:
    ROLE_CAPABILITIES = {
        "owner": frozenset(
            {"workspace.manage", "room.manage", "event.write", "event.read", "presence.write", "cursor.write"}
        ),
        "editor": frozenset({"event.write", "event.read", "presence.write", "cursor.write"}),
        "viewer": frozenset({"event.read", "presence.write", "cursor.write"}),
    }

    def require(self, membership: Mapping[str, object] | None, capability: str) -> None:
        if not membership or membership.get("status") != "active":
            raise CollaborationPolicyDenied("collaboration_membership_required")
        if capability not in self.ROLE_CAPABILITIES.get(str(membership.get("role") or ""), frozenset()):
            raise CollaborationPolicyDenied("collaboration_capability_denied")


__all__ = ["CollaborationPolicyDenied", "CollaborationWorkspacePolicy"]
