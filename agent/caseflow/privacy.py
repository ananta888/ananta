"""CaseFlow Privacy — sensitive data classification and export/delete utilities."""
from __future__ import annotations

from enum import Enum
from typing import Any

from agent.caseflow.artifacts import CaseArtifact

SENSITIVE_ARTIFACT_TYPES = {"cv", "cover_letter", "email_draft", "personal_notes"}


class SensitiveField(str, Enum):
    salary = "salary"
    contact_email = "contact_email"
    contact_phone = "contact_phone"
    personal_address = "personal_address"


def classify_artifact_sensitivity(artifact: CaseArtifact) -> bool:
    """Return True if this artifact is considered sensitive."""
    return artifact.artifact_type in SENSITIVE_ARTIFACT_TYPES


def export_case(case_id: str, include_sensitive: bool = False) -> dict[str, Any]:
    """Export a case with all its events, artifacts and actions.

    Sensitive artifacts are excluded unless include_sensitive=True.
    """
    # In v1: returns a stub dict structure.
    # Real implementation would query the DB and filter accordingly.
    return {
        "case_id": case_id,
        "include_sensitive": include_sensitive,
        "note": "Export feature — full DB-backed implementation pending.",
    }


def delete_case_data(case_id: str, hard_delete: bool = False) -> dict[str, Any]:
    """Soft or hard delete a case.

    Soft: status="archived", is_deleted=True
    Hard: physically remove from DB (only with explicit confirmation)
    """
    return {
        "case_id": case_id,
        "hard_delete": hard_delete,
        "note": "Delete feature — full DB-backed implementation pending.",
    }
