"""Per-application dependency bundle of the SGPT/CLI-backend routes.

The SGPT route family reads its runtime settings (execution backend default,
provider URLs, CLI binary paths, RAG root, hub/worker role) through this
bundle instead of the module-level ``agent.config.settings`` name. The
settings object is threaded explicitly into the CLI-backend preflight and
runtime resolution (``backend_settings=``), so tests replace the settings of
one Flask application via :data:`SGPT_ROUTE_DEPENDENCIES` instead of patching
module attributes of several ``agent.cli_backends`` modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.routes.route_dependency_seam import RouteDependencySeam


@dataclass(frozen=True)
class SgptRouteDependencies:
    """Collaborators of the SGPT routes."""

    settings: Any


def _production_dependencies() -> SgptRouteDependencies:
    from agent.config import settings

    return SgptRouteDependencies(settings=settings)


SGPT_ROUTE_DEPENDENCIES: RouteDependencySeam[SgptRouteDependencies] = RouteDependencySeam(
    "ananta.sgpt_route_dependencies",
    _production_dependencies,
)


def sgpt_route_settings() -> Any:
    """The settings object of the current application's SGPT routes."""

    return SGPT_ROUTE_DEPENDENCIES.resolve().settings
