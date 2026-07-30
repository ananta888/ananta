"""Lazy production composition for persistent Hub Git source connectors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from sqlmodel import Session

from agent.repositories.hub_git_authorization_repository import (
    SQLHubGitAuthorizationRepository,
)
from agent.services.git_remote_policy_service import GitRemoteAccessPolicyPort
from agent.services.hub_git_credential_resolver import (
    GitSecretValueResolverPort,
    SubprocessGitCredentialCommandResolver,
)
from agent.sources.git_source_connector_common import GitRepositoryBudgets
from agent.sources.hub_git_connector_composition import (
    HubGitConnectorComposition,
    compose_hub_git_source_connectors,
)


@dataclass(frozen=True)
class PersistentHubGitConnectorComposition:
    registry: SQLHubGitAuthorizationRepository
    connectors: HubGitConnectorComposition


def compose_persistent_hub_git_source_connectors(
    *,
    session_factory: Callable[[], Session],
    config: Mapping[str, Any],
    secret_resolver: GitSecretValueResolverPort,
    remote_policy: GitRemoteAccessPolicyPort,
) -> PersistentHubGitConnectorComposition:
    """Build adapters without opening a DB session or resolving any secret."""

    workspace_root = _configured_path(
        config,
        "hub_git_workspace_root",
        "HUB_GIT_WORKSPACE_ROOT",
    )
    credential_root = _configured_path(
        config,
        "hub_git_credential_root",
        "HUB_GIT_CREDENTIAL_ROOT",
    )
    budgets = _configured_budgets(
        config.get(
            "hub_git_budgets",
            config.get("HUB_GIT_BUDGETS"),
        )
    )
    registry = SQLHubGitAuthorizationRepository(
        session_factory=session_factory,
    )
    credential_resolver = SubprocessGitCredentialCommandResolver(
        secret_resolver=secret_resolver,
        credential_root=credential_root,
    )
    connectors = compose_hub_git_source_connectors(
        authorization_registry=registry,
        credential_resolver=credential_resolver,
        remote_policy=remote_policy,
        workspace_root=workspace_root,
        budgets=budgets,
    )
    return PersistentHubGitConnectorComposition(
        registry=registry,
        connectors=connectors,
    )


def _configured_path(
    config: Mapping[str, Any],
    key: str,
    alternate_key: str,
) -> Path:
    value = config.get(key, config.get(alternate_key))
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{key}_required")
    return Path(value)


def _configured_budgets(value: Any) -> GitRepositoryBudgets | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("hub_git_budgets_invalid")
    allowed = {item.name for item in fields(GitRepositoryBudgets)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("hub_git_budgets_unknown_field")
    return GitRepositoryBudgets(**dict(value))


__all__ = [
    "PersistentHubGitConnectorComposition",
    "compose_persistent_hub_git_source_connectors",
]
