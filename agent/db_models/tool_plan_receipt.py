"""One durable multi-tool invocation per scoped Hub task."""

from __future__ import annotations

import time
import uuid

from sqlmodel import Field, SQLModel


class ToolPlanReceiptDB(SQLModel, table=True):
    __tablename__ = "tool_plan_receipts"
    scope_digest: str = Field(primary_key=True)
    request_digest: str
    state: str = "running"
    response_json: str = ""


class ToolPlanEventDB(SQLModel, table=True):
    __tablename__ = "tool_plan_events"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    tenant_id: str = Field(index=True)
    project_id: str = Field(index=True)
    task_id: str = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    event_json: str
