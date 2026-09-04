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
        "maintainer": frozenset({"room.manage", "event.write", "event.read", "presence.write", "cursor.write"}),
        "member": frozenset({"event.write", "event.read", "presence.write", "cursor.write"}),
        "guest": frozenset({"event.write", "event.read", "presence.write", "cursor.write"}),
        "observer": frozenset({"event.read", "presence.write", "cursor.write"}),
        "editor": frozenset({"event.write", "event.read", "presence.write", "cursor.write"}),
        "viewer": frozenset({"event.read", "presence.write", "cursor.write"}),
    }

    def require(self, membership: Mapping[str, object] | None, capability: str) -> None:
        if not membership or membership.get("status") != "active":
            raise CollaborationPolicyDenied("collaboration_membership_required")
        effective = membership.get("effective_capabilities")
        capabilities = (
            frozenset(str(value) for value in effective)
            if isinstance(effective, (list, tuple, set, frozenset))
            else self.ROLE_CAPABILITIES.get(str(membership.get("role") or ""), frozenset())
        )
        if capability not in capabilities:
            raise CollaborationPolicyDenied("collaboration_capability_denied")


__all__ = ["CollaborationPolicyDenied", "CollaborationWorkspacePolicy"]
