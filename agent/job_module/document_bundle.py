"""Job Application Document Bundle — tracks required documents and their status."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


DOCUMENT_TYPES = [
    "cv", "certificate", "cover_letter", "email_draft",
    "job_posting", "portfolio", "notes",
]
DOCUMENT_STATUS_ORDER = [
    "missing", "available", "generated", "reviewed", "approved", "sent",
]

REQUIRED_DOCS = ["cv", "cover_letter", "job_posting"]
CRITICAL_DOCS = ["cv", "cover_letter"]


class DocumentStatus(BaseModel):
    doc_type: str
    status: str = "missing"  # "missing" | "available" | "generated" | "reviewed" | "approved" | "sent"
    artifact_ids: list[str] = Field(default_factory=list)
    latest_artifact_id: Optional[str] = None


class ApplicationDocumentBundle(BaseModel):
    case_id: str
    documents: dict[str, DocumentStatus] = Field(default_factory=dict)
    completion_percent: float = 0.0

    def compute_completion(self) -> float:
        ready_statuses = {"approved", "sent", "reviewed"}
        ready = [
            d for d in REQUIRED_DOCS
            if self.documents.get(d, DocumentStatus(doc_type=d)).status in ready_statuses
        ]
        return len(ready) / len(REQUIRED_DOCS) * 100

    def missing_required_docs(self) -> list[str]:
        return [
            d for d in REQUIRED_DOCS
            if self.documents.get(d, DocumentStatus(doc_type=d)).status == "missing"
        ]

    def can_send(self) -> bool:
        """Only allow sending when all critical documents are approved."""
        return all(
            self.documents.get(d, DocumentStatus(doc_type=d)).status == "approved"
            for d in CRITICAL_DOCS
        )
