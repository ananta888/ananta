"""Compatibility re-export of the connection-to-selector binding.

The dependency-free contracts live in ``agent.models.source_control_connection_binding`` so that
repositories can depend on them without importing the service layer.
"""

from __future__ import annotations

from agent.models.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
    SourceControlConnectionBindingError,
    implementation_connector_type,
    normalize_workspace_relative_path,
)

__all__ = [
    "SourceConnectionSelectorBinding",
    "SourceControlConnectionBindingError",
    "implementation_connector_type",
    "normalize_workspace_relative_path",
]
