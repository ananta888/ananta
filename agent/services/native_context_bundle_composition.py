"""Hub-only composition using existing task, policy and destination stores."""

from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from agent.repositories.context_policy_lifecycle_repository import SQLContextPolicyLifecycleRepository
from agent.services.context_access_policy_service import ContextAccessPolicyService
from agent.services.native_context_blocks import NativeContextBlockProjector
from agent.services.native_context_bundle_service import NativeContextBundleService
from agent.services.native_context_policy_service import NativeContextPolicyService
from agent.services.repository_registry import get_repository_registry
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessService
from ananta_contracts.native_context_bundle import NativeContextBundleProjection
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


class HubNativeContextBundleReader:
    """Resolve app-owned catalogs at read time, not a process-wide new cache."""

    def __init__(self, *, engine: Any) -> None:
        self._engine = engine

    def read(
        self, *, binding: WorkflowWorkerBinding, hub_task_id: str, command_id: str,
        attempt_id: str, fencing_token: int, worker_id: str,
    ) -> NativeContextBundleProjection:
        if not has_app_context():
            raise ValueError("native_context_hub_app_required")
        destinations = current_app.extensions.get("source_control_destination_catalog")
        if destinations is None:
            raise ValueError("native_context_destination_catalog_unavailable")
        registry = get_repository_registry()
        service = NativeContextBundleService(
            tasks=registry.task_repo,
            bundles=TaskContextBundleAccessService(registry.context_bundle_repo),
            policy=NativeContextPolicyService(
                policies=SQLContextPolicyLifecycleRepository(self._engine), destinations=destinations,
                blocks=NativeContextBlockProjector(ContextAccessPolicyService()),
            ),
        )
        return service.read(
            binding=binding, hub_task_id=hub_task_id, command_id=command_id,
            attempt_id=attempt_id, fencing_token=fencing_token, worker_id=worker_id,
        )
