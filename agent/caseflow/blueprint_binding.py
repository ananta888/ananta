"""CaseFlow Blueprint Binding — connects cases to VisualProcess workflows."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CaseBlueprintBinding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    visual_process_graph_id: str
    blueprint_id: Optional[str] = None
    blueprint_version: Optional[str] = None
    active_step_id: Optional[str] = None
    workflow_id: Optional[str] = None
    status: str = "pending"  # "pending" | "running" | "completed" | "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


def start_case_workflow(
    case_id: str,
    graph_id: str,
    metadata: dict[str, Any] | None = None,
) -> CaseBlueprintBinding:
    """Create a blueprint binding and start the workflow.

    On error: status is set to "failed" and the case is NOT modified.
    """
    from agent.caseflow.timeline import CaseEvent, append_event

    binding = CaseBlueprintBinding(
        case_id=case_id,
        visual_process_graph_id=graph_id,
        status="running",
        metadata=metadata or {},
    )

    try:
        event = CaseEvent(
            case_id=case_id,
            event_type="case_workflow_started",
            title=f"Workflow gestartet: {graph_id}",
            payload={
                "binding_id": binding.id,
                "graph_id": graph_id,
            },
        )
        append_event(case_id, event)
    except Exception as exc:
        binding.status = "failed"
        binding.metadata["error"] = str(exc)

    return binding
