"""Generic Git connector restricted to scoped, registered remote identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.services.git_remote_policy_service import (
    GitRemoteAccessPolicyPort,
    GitTransportAuthorization,
)
from agent.sources.git_source_connector_common import (
    GitCommitResolution,
    GitInventoryProviderPort,
    GitRefreshProviderPort,
    GitRemoteEndpoint,
    GitRepositoryBudgets,
    GitSourceConnectorBase,
    GitSourceScope,
    validate_requested_ref,
)
from agent.sources.source_connectors import SourceConnectorError


_REMOTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class RegisteredGitRemoteRequest:
    scope: GitSourceScope
    remote_id: str


@dataclass(frozen=True)
class GenericGitCommitResolutionRequest:
    scope: GitSourceScope
    remote_id: str
    requested_ref: str
    transport_authorization: GitTransportAuthorization


class RegisteredGitRemotePort(Protocol):
    """Resolve a tenant/project/owner-scoped remote registration."""

    def get_registered_remote(
        self,
        request: RegisteredGitRemoteRequest,
    ) -> GitRemoteEndpoint | None: ...


class GenericGitCommitResolverPort(Protocol):
    """Resolve refs using only a registered remote ID, never a supplied URL."""

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def resolve_commit(
        self,
        request: GenericGitCommitResolutionRequest,
    ) -> GitCommitResolution: ...


class GenericGitConnector(GitSourceConnectorBase):
    connector_type = "generic_git"
    required_scopes = frozenset({"repository:read"})

    def __init__(
        self,
        *,
        remote_registry: RegisteredGitRemotePort,
        commit_resolver: GenericGitCommitResolverPort,
        remote_policy: GitRemoteAccessPolicyPort,
        inventory_provider: GitInventoryProviderPort,
        refresh_provider: GitRefreshProviderPort,
        budgets: GitRepositoryBudgets | None = None,
    ) -> None:
        super().__init__(
            remote_policy=remote_policy,
            inventory_provider=inventory_provider,
            refresh_provider=refresh_provider,
            budgets=budgets,
        )
        self._remote_registry = remote_registry
        self._commit_resolver = commit_resolver

    def _connector_validation_errors(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if _REMOTE_ID.fullmatch(self._connection_ref(descriptor)) is None:
            errors.append("registered_remote_id_invalid")
        if not validate_requested_ref(self._requested_ref(descriptor)):
            errors.append("git_ref_invalid")
        return tuple(errors)

    def _connection_ref(self, descriptor: Mapping[str, Any]) -> str:
        return str(descriptor.get("remote_id") or "").strip()

    def _repository_identifier(
        self,
        descriptor: Mapping[str, Any],
    ) -> None:
        del descriptor
        return None

    def _requested_ref(self, descriptor: Mapping[str, Any]) -> str:
        return str(descriptor.get("ref") or "").strip()

    def _resolve_endpoint(
        self,
        scope: GitSourceScope,
        descriptor: Mapping[str, Any],
    ) -> GitRemoteEndpoint:
        endpoint = self._remote_registry.get_registered_remote(
            RegisteredGitRemoteRequest(
                scope=scope,
                remote_id=self._connection_ref(descriptor),
            )
        )
        if endpoint is None:
            raise SourceConnectorError("registered_remote_not_found")
        return endpoint

    def _resolve_commit(
        self,
        scope: GitSourceScope,
        descriptor: Mapping[str, Any],
        transport_authorization: GitTransportAuthorization,
    ) -> GitCommitResolution:
        self._require_transport_support(
            self._commit_resolver,
            transport_authorization,
        )
        return self._commit_resolver.resolve_commit(
            GenericGitCommitResolutionRequest(
                scope=scope,
                remote_id=self._connection_ref(descriptor),
                requested_ref=self._requested_ref(descriptor),
                transport_authorization=transport_authorization,
            )
        )

    def _public_metadata(
        self,
        descriptor: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {"remote_id": self._connection_ref(descriptor)}


__all__ = [
    "GenericGitCommitResolutionRequest",
    "GenericGitCommitResolverPort",
    "GenericGitConnector",
    "RegisteredGitRemotePort",
    "RegisteredGitRemoteRequest",
]
