"""Tenant-scoped append-only collaboration store facade."""

from __future__ import annotations

from pathlib import Path

from agent.services.collaboration_event_delivery_store import CollaborationEventDeliveryStoreMixin
from agent.services.collaboration_resource_control_store import CollaborationResourceControlStoreMixin
from agent.services.collaboration_store_infrastructure import CollaborationStoreInfrastructureMixin
from agent.services.collaboration_workspace_catalog_store import CollaborationWorkspaceCatalogStoreMixin
from agent.services.collaboration_workspace_store_contracts import CollaborationStoreConflict
from agent.services.interprocess_file_transaction import InterProcessFileTransaction


class CollaborationWorkspaceStore(
    CollaborationWorkspaceCatalogStoreMixin,
    CollaborationResourceControlStoreMixin,
    CollaborationEventDeliveryStoreMixin,
    CollaborationStoreInfrastructureMixin,
):
    """Backward-compatible facade over focused collaboration persistence traits."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()


__all__ = ["CollaborationStoreConflict", "CollaborationWorkspaceStore"]
