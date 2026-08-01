from __future__ import annotations

from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectCapability,
)


class AllowProjectAccess:
    """Explicit project catalog double for route-boundary tests."""

    def __init__(self, *, role: str = "owner") -> None:
        self.role = role

    def require(self, **kwargs) -> AuthorizedProjectScope:
        return AuthorizedProjectScope(
            tenant_id=kwargs["tenant_id"],
            project_id=kwargs["project_id"],
            team_id=kwargs["project_id"],
            subject_id=kwargs["subject_id"],
            role=self.role,
            status="active",
            capability=kwargs.get("capability", ProjectCapability.READ),
            lock_version=1,
        )


__all__ = ["AllowProjectAccess"]
