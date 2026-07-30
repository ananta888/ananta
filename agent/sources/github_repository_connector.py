"""GitHub repository connector using server-side authorization references only."""

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


_AUTHORIZATION_REF = re.compile(
    r"^github-(?:installation|oauth):[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"
)
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)


@dataclass(frozen=True)
class GitHubRepositoryEndpointRequest:
    scope: GitSourceScope
    authorization_ref: str
    repository: str


@dataclass(frozen=True)
class GitHubCommitResolutionRequest:
    scope: GitSourceScope
    authorization_ref: str
    repository: str
    requested_ref: str
    transport_authorization: GitTransportAuthorization


class GitHubRepositoryEndpointPort(Protocol):
    """Resolve an authorized clone endpoint without exposing provider tokens."""

    def resolve_repository(
        self,
        request: GitHubRepositoryEndpointRequest,
    ) -> GitRemoteEndpoint: ...


class GitHubCommitResolverPort(Protocol):
    """Resolve a mutable GitHub ref to an immutable server-side commit."""

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def resolve_commit(
        self,
        request: GitHubCommitResolutionRequest,
    ) -> GitCommitResolution: ...


class GitHubRepositoryConnector(GitSourceConnectorBase):
    connector_type = "github_repository"
    required_scopes = frozenset({"contents:read"})

    def __init__(
        self,
        *,
        endpoint_provider: GitHubRepositoryEndpointPort,
        commit_resolver: GitHubCommitResolverPort,
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
        self._endpoint_provider = endpoint_provider
        self._commit_resolver = commit_resolver

    def _connector_validation_errors(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if _AUTHORIZATION_REF.fullmatch(self._connection_ref(descriptor)) is None:
            errors.append("github_authorization_ref_invalid")
        if _REPOSITORY.fullmatch(
            str(descriptor.get("repository") or "").strip()
        ) is None:
            errors.append("github_repository_invalid")
        if not validate_requested_ref(self._requested_ref(descriptor)):
            errors.append("git_ref_invalid")
        return tuple(errors)

    def _connection_ref(self, descriptor: Mapping[str, Any]) -> str:
        return str(descriptor.get("github_authorization_ref") or "").strip()

    def _repository_identifier(
        self,
        descriptor: Mapping[str, Any],
    ) -> str:
        return str(descriptor.get("repository") or "").strip()

    def _requested_ref(self, descriptor: Mapping[str, Any]) -> str:
        return str(descriptor.get("ref") or "").strip()

    def _resolve_endpoint(
        self,
        scope: GitSourceScope,
        descriptor: Mapping[str, Any],
    ) -> GitRemoteEndpoint:
        return self._endpoint_provider.resolve_repository(
            GitHubRepositoryEndpointRequest(
                scope=scope,
                authorization_ref=self._connection_ref(descriptor),
                repository=self._repository_identifier(descriptor),
            )
        )

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
            GitHubCommitResolutionRequest(
                scope=scope,
                authorization_ref=self._connection_ref(descriptor),
                repository=self._repository_identifier(descriptor),
                requested_ref=self._requested_ref(descriptor),
                transport_authorization=transport_authorization,
            )
        )

    def _public_metadata(
        self,
        descriptor: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {"repository": self._repository_identifier(descriptor)}


__all__ = [
    "GitHubCommitResolutionRequest",
    "GitHubCommitResolverPort",
    "GitHubRepositoryConnector",
    "GitHubRepositoryEndpointPort",
    "GitHubRepositoryEndpointRequest",
]
