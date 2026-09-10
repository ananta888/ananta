"""Hub persistence wiring; never imported by the standalone Worker."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError

from agent.db_models import ContextBundleDB
from agent.services.native_context_preparation_service import NativeContextPreparationService, NativePreparedTaskContext
from agent.services.native_context_snapshot import NativeContextSnapshot
from agent.services.repository_registry import get_repository_registry
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessService
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand


class NativeBundlePersistencePort(Protocol):
    def get_by_id(self, bundle_id: str) -> Any: ...
    def save(self, bundle: ContextBundleDB) -> Any: ...


class HubNativeContextSnapshotStore:
    def __init__(self, repository: NativeBundlePersistencePort) -> None:
        self._repository = repository

    def ensure(self, snapshot: NativeContextSnapshot) -> None:
        stored = self._repository.get_by_id(snapshot.bundle_id)
        if stored is None:
            try:
                # Always a new model: never merge/update the original owned bundle.
                stored = self._repository.save(ContextBundleDB(**snapshot.values()))
            except IntegrityError:
                # A concurrent insert may win. Only identical immutable content
                # is idempotent; any other persistence failure propagates.
                stored = self._repository.get_by_id(snapshot.bundle_id)
        if not snapshot.matches(stored):
            raise ValueError("native_context_snapshot_conflict")


class HubNativeContextPreparer:
    def prepare(self, *, command: NativeNodeCommand, hub_task_id: str) -> NativePreparedTaskContext:
        registry = get_repository_registry()
        return NativeContextPreparationService(
            tasks=registry.task_repo,
            bundles=TaskContextBundleAccessService(registry.context_bundle_repo),
            snapshots=HubNativeContextSnapshotStore(registry.context_bundle_repo),
        ).prepare(command=command, hub_task_id=hub_task_id)
