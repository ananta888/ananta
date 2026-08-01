"""Composition root for the tenant-bound project lifecycle."""

from __future__ import annotations

from flask import Flask

from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.project_lifecycle_service import ProjectLifecycleService


def configure_project_lifecycle(app: Flask) -> None:
    """Install one shared authority and lifecycle service per Hub app."""

    app.extensions.setdefault(
        "project_access_authority",
        SqlProjectAccessAuthority(),
    )
    app.extensions.setdefault(
        "project_lifecycle_service",
        ProjectLifecycleService(),
    )


__all__ = ["configure_project_lifecycle"]
