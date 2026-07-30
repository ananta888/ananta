"""Additive composition of governed source-control connector extensions."""

from __future__ import annotations

from typing import Protocol

from agent.sources.registered_workspace_connector import (
    RegisteredWorkspaceConnector,
)
from agent.sources.registered_workspace_source_adapter import (
    build_registered_workspace_source_connectors,
)
from agent.sources.source_connectors import SourceConnector, SourceConnectorError


class RegistryConnectorAdapterPort(Protocol):
    def connector(self) -> SourceConnector: ...


def _require_type(
    connector: SourceConnector,
    expected_type: str,
) -> SourceConnector:
    if connector.connector_type != expected_type:
        raise SourceConnectorError("connector_composition_type_mismatch")
    return connector


def build_source_control_connector_extensions(
    *,
    github_repository: RegistryConnectorAdapterPort | None = None,
    generic_git: RegistryConnectorAdapterPort | None = None,
    registered_workspace: RegisteredWorkspaceConnector | None = None,
) -> tuple[SourceConnector, ...]:
    """Build optional extensions without adding provider branches to the registry."""

    connectors: list[SourceConnector] = []
    if github_repository is not None:
        connectors.append(
            _require_type(
                github_repository.connector(),
                "github_repository",
            )
        )
    if generic_git is not None:
        connectors.append(
            _require_type(generic_git.connector(), "generic_git")
        )
    if registered_workspace is not None:
        connectors.extend(
            build_registered_workspace_source_connectors(
                registered_workspace
            )
        )
    return tuple(connectors)


__all__ = [
    "RegistryConnectorAdapterPort",
    "build_source_control_connector_extensions",
]
