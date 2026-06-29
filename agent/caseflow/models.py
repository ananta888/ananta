"""CaseFlow Core Domain Models.

Generic case management model — no domain-specific fields.
Job application data lives in domain_payload or agent/job_module/models.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class CaseFlowCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_type: str  # "job_application", "lead", etc.
    title: str
    status: str = "new"
    priority: str = "medium"  # critical/high/medium/low
    risk: str = "low"
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    source: Optional[str] = None  # "manual", "discovery", etc.
    domain_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_deleted: bool = False


class CaseTypeDefinition(BaseModel):
    case_type: str
    statuses: list[str]
    initial_status: str
    terminal_statuses: list[str]
    allowed_artifact_types: list[str] = []
    ui_hints: dict[str, Any] = {}
