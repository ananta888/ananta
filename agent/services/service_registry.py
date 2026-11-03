"""Hub service registry (compatibility facade).

Typed core definitions live in ``service_registry_types`` and the Flask
composition in ``service_registry_runtime``; this facade keeps the
historical import surface stable.
"""

from __future__ import annotations

from agent.services.service_registry_runtime import (
    build_core_service_registry,
    get_core_services,
    get_governance_services,
    get_integration_services,
    get_knowledge_services,
    get_runtime_services,
    get_task_services,
    initialize_core_services,
    register_core_service_override,
)
from agent.services.service_registry_types import (
    CoreServiceRegistry,
    GovernanceServices,
    IntegrationServices,
    KnowledgeServices,
    RuntimeServices,
    TaskServices,
)

__all__ = [
    "CoreServiceRegistry",
    "GovernanceServices",
    "IntegrationServices",
    "KnowledgeServices",
    "RuntimeServices",
    "TaskServices",
    "build_core_service_registry",
    "get_core_services",
    "get_governance_services",
    "get_integration_services",
    "get_knowledge_services",
    "get_runtime_services",
    "get_task_services",
    "initialize_core_services",
    "register_core_service_override",
]
