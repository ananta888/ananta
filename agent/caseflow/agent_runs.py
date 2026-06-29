"""CaseFlow AgentRuns — trace-bound agent execution records per case."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentRunStatus(str, Enum):
    running = "running"
    done = "done"
    error = "error"
    cancelled = "cancelled"


class CaseAgentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    agent_profile_id: str
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    status: AgentRunStatus = AgentRunStatus.running
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    trace_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    estimated_cost: Optional[float] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
