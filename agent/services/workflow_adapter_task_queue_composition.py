"""Production composition for the Hub-owned workflow-adapter queue."""

from __future__ import annotations

import os

from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterTaskQueueService,
)


def build_workflow_adapter_task_queue_service() -> WorkflowAdapterTaskQueueService:
    """Compose SQL-backed Hub ports; missing signing material fails closed."""

    from agent.database import engine
    from agent.repository import task_repo
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.task_runtime_service import get_task_runtime_service
    from agent.services.workflow_authorization_grant_service import (
        SQLAlchemyWorkflowAuthorizationGrantService,
    )
    from agent.services.workflow_hub_task_gateway_runtime import (
        get_workflow_authorization_key_ring,
    )
    from agent.services.workflow_runtime import (
        SQLAlchemyEventStore,
        SQLAlchemyExecutionOwnershipStore,
    )
    from agent.services.workflow_runtime.telemetry_runtime import (
        configure_workflow_telemetry,
    )

    return WorkflowAdapterTaskQueueService(
        task_queue=get_task_queue_service(),
        task_repository=task_repo,
        task_runtime=get_task_runtime_service(),
        ownership=SQLAlchemyExecutionOwnershipStore(engine),
        authorization_keys=get_workflow_authorization_key_ring(),
        authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine),
        events=configure_workflow_telemetry(SQLAlchemyEventStore(engine)),
        lease_seconds=float(
            os.environ.get("ANANTA_WORKFLOW_ADAPTER_LEASE_SECONDS") or 1800
        ),
    )


__all__ = ["build_workflow_adapter_task_queue_service"]
