"""Read port for scoped Hub Git authorization registrations."""

from __future__ import annotations

from typing import Protocol

from agent.models.hub_git_authorization import RegisteredGitAuthorization
from agent.sources.git_source_connector_common import GitSourceScope


class HubGitAuthorizationRegistryPort(Protocol):
    def list_authorizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
    ) -> tuple[RegisteredGitAuthorization, ...]: ...

    def resolve_registered_remote(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_connection(
        self,
        *,
        scope: GitSourceScope,
        connection_ref: str,
        repository_identifier: str | None = None,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_github(
        self,
        *,
        scope: GitSourceScope,
        authorization_ref: str,
        repository: str,
    ) -> RegisteredGitAuthorization | None: ...

    def resolve_generic(
        self,
        *,
        scope: GitSourceScope,
        remote_id: str,
    ) -> RegisteredGitAuthorization | None: ...


__all__ = ["HubGitAuthorizationRegistryPort"]
