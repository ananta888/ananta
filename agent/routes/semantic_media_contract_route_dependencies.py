"""Collaborators of the semantic-media contract routes and their single override seam.

The handlers in :mod:`agent.routes.semantic_media_contracts` reach share
membership and capability authority, the semantic contract service, the
compute execution service and the semantic capability check only through this
bundle. Tests replace collaborators per application with
``SEMANTIC_MEDIA_CONTRACT_ROUTE_DEPENDENCIES.install(app, ...)`` instead of
monkeypatching attributes of the route module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.semantic_media_contract_route_authority import (
    SemanticContractRouteAuthority,
    _require_semantic_capability,
)
from agent.services.semantic_compute_execution_service import (
    get_semantic_compute_execution_service,
)
from agent.services.semantic_contract_service import get_semantic_contract_service


@dataclass(frozen=True)
class SemanticMediaContractRouteDependencies:
    """Services the semantic-media contract routes delegate to."""

    route_authority: SemanticContractRouteAuthority
    contract_service: Callable[[], Any]
    compute_execution_service: Callable[[], Any]
    require_semantic_capability: Callable[..., None]


def production_semantic_media_contract_route_dependencies() -> SemanticMediaContractRouteDependencies:
    return SemanticMediaContractRouteDependencies(
        route_authority=SemanticContractRouteAuthority(),
        contract_service=get_semantic_contract_service,
        compute_execution_service=get_semantic_compute_execution_service,
        require_semantic_capability=_require_semantic_capability,
    )


SEMANTIC_MEDIA_CONTRACT_ROUTE_DEPENDENCIES: RouteDependencySeam[SemanticMediaContractRouteDependencies] = (
    RouteDependencySeam(
        "ananta.semantic_media_contract_route_dependencies",
        production_semantic_media_contract_route_dependencies,
    )
)
