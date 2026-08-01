"""Composition root for concrete Hub-side Git source connectors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.services.git_remote_policy_service import GitRemoteAccessPolicyPort
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
)
from agent.services.hub_git_credential_resolver import (
    GitCredentialCommandResolverPort,
)
from agent.services.hub_git_transport import (
    HubGitTransport,
    HubGitTransportPort,
)
from agent.services.remote_source_payload_store import (
    RemoteSourcePayloadStorePort,
)
from agent.sources.generic_git_connector import GenericGitConnector
from agent.sources.git_source_connector_common import GitRepositoryBudgets
from agent.sources.github_repository_connector import (
    GitHubRepositoryConnector,
)
from agent.sources.hub_git_connector_providers import (
    HubGenericGitCommitResolver,
    HubGitContentProvider,
    HubGitHubCommitResolver,
    HubGitHubRepositoryEndpointProvider,
    HubRegisteredGitRemoteProvider,
)


@dataclass(frozen=True)
class HubGitConnectorComposition:
    github_repository: GitHubRepositoryConnector
    generic_git: GenericGitConnector
    transport: HubGitTransportPort | None = None
    registered_remotes: HubGitAuthorizationRegistryPort | None = None
    payload_store: RemoteSourcePayloadStorePort | None = None


def compose_hub_git_source_connectors(
    *,
    authorization_registry: HubGitAuthorizationRegistryPort,
    credential_resolver: GitCredentialCommandResolverPort,
    remote_policy: GitRemoteAccessPolicyPort,
    workspace_root: Path,
    budgets: GitRepositoryBudgets | None = None,
    payload_store: RemoteSourcePayloadStorePort | None = None,
) -> HubGitConnectorComposition:
    """Build both connectors without performing filesystem or network I/O."""

    transport = HubGitTransport(
        credential_resolver=credential_resolver,
        workspace_root=workspace_root,
    )
    content = HubGitContentProvider(
        registry=authorization_registry,
        transport=transport,
        payload_store=payload_store,
    )
    return HubGitConnectorComposition(
        github_repository=GitHubRepositoryConnector(
            endpoint_provider=HubGitHubRepositoryEndpointProvider(
                registry=authorization_registry
            ),
            commit_resolver=HubGitHubCommitResolver(
                registry=authorization_registry,
                transport=transport,
            ),
            remote_policy=remote_policy,
            inventory_provider=content,
            refresh_provider=content,
            budgets=budgets,
        ),
        generic_git=GenericGitConnector(
            remote_registry=HubRegisteredGitRemoteProvider(
                registry=authorization_registry
            ),
            commit_resolver=HubGenericGitCommitResolver(
                registry=authorization_registry,
                transport=transport,
            ),
            remote_policy=remote_policy,
            inventory_provider=content,
            refresh_provider=content,
            budgets=budgets,
        ),
        transport=transport,
        registered_remotes=authorization_registry,
        payload_store=payload_store,
    )


__all__ = [
    "HubGitConnectorComposition",
    "compose_hub_git_source_connectors",
]
