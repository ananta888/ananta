"""CaseFlow Artifact System — generic artifact management for cases."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ArtifactStatus(str, Enum):
    draft = "draft"
    generated = "generated"
    reviewed = "reviewed"
    approved = "approved"
    sent = "sent"
    archived = "archived"


class ArtifactKind(str, Enum):
    file = "file"
    json = "json"
    text = "text"
    report = "report"
    dataset = "dataset"
    unknown = "unknown"


class CaseArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    artifact_type: str  # "cv", "cover_letter", "job_posting", etc.
    artifact_kind: ArtifactKind = ArtifactKind.text
    title: str
    source: str = "manual"  # "manual" | "agent" | "upload"
    content_ref: Optional[str] = None
    content_text: Optional[str] = None
    mime_type: str = "text/plain"
    version: int = 1
    version_group_id: Optional[str] = None
    previous_artifact_id: Optional[str] = None
    status: ArtifactStatus = ArtifactStatus.draft
    created_by: Optional[str] = None
    trace_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_sensitive: bool = False
