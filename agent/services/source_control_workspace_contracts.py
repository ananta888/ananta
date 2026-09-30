"""Compatibility re-export of the workspace registration contracts.

The dependency-free contracts live in ``agent.models.source_control_workspace_contracts`` so that
repositories can depend on them without importing the service layer.
"""

from __future__ import annotations

from agent.models.source_control_workspace_contracts import (
    SourceControlWorkspaceContractError,
    WorkspaceCreateSelection,
    WorkspaceFolderSelection,
    WorkspaceFolderSnapshot,
    WorkspaceRegistrationRecord,
    WorkspaceValidationBinding,
)

__all__ = [
    "SourceControlWorkspaceContractError",
    "WorkspaceCreateSelection",
    "WorkspaceFolderSelection",
    "WorkspaceFolderSnapshot",
    "WorkspaceRegistrationRecord",
    "WorkspaceValidationBinding",
]
