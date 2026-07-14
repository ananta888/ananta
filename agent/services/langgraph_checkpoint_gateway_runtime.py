"""Production composition root for the Hub-owned LangGraph checkpointer."""

from __future__ import annotations

import threading

from agent.services.langgraph_checkpoint_gateway_service import (
    LangGraphCheckpointGatewayService,
)
from agent.services.workflow_hub_task_gateway_runtime import (
    get_workflow_authorization_key_ring,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    InMemoryReplayNonceStore,
    SQLAlchemyCheckpointStore,
    SQLAlchemyExecutionOwnershipStore,
    WorkflowCommandVerifier,
)

_SERVICE: LangGraphCheckpointGatewayService | None = None
_LOCK = threading.Lock()


def get_langgraph_checkpoint_gateway_service() -> LangGraphCheckpointGatewayService:
    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    with _LOCK:
        if _SERVICE is None:
            from agent.database import engine
            from agent.services.workflow_worker_assignment_runtime import (
                get_workflow_worker_assignment_store,
            )

            key_ring = get_workflow_authorization_key_ring()
            _SERVICE = LangGraphCheckpointGatewayService(
                checkpoints=SQLAlchemyCheckpointStore(engine),
                ownership=SQLAlchemyExecutionOwnershipStore(engine),
                key_ring=key_ring,
                authorization=AuthorizationVerifier(key_ring),
                commands=WorkflowCommandVerifier(key_ring, InMemoryReplayNonceStore()),
                assignments=get_workflow_worker_assignment_store(),
            )
    return _SERVICE


def reset_langgraph_checkpoint_gateway_service() -> None:
    global _SERVICE
    with _LOCK:
        _SERVICE = None


__all__ = [
    "get_langgraph_checkpoint_gateway_service",
    "reset_langgraph_checkpoint_gateway_service",
]
