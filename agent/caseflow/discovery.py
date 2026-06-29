"""CaseFlow Discovery — generic discovery adapter protocol and models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


@runtime_checkable
class DiscoverySourceAdapter(Protocol):
    def source_id(self) -> str: ...
    def capabilities(self) -> dict[str, Any]: ...
    def search(self, profile: "SearchProfile") -> list["DiscoveryResult"]: ...
    def normalize(self, raw_result: dict[str, Any]) -> "DiscoveryResult": ...


class DiscoveryResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    result_type: str  # "job_posting", "lead", etc.
    title: str
    source_url: Optional[str] = None
    source_name: str
    raw_text: Optional[str] = None
    normalized_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    fingerprint: Optional[str] = None
    duplicate_of: Optional[str] = None
    is_duplicate: bool = False
    ignored: bool = False
    converted_to_case_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DiscoveryRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile_id: str
    status: str = "running"  # "running" | "done" | "error"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    result_count: int = 0
    error_count: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str] = None


class SearchProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile_type: str = "job_search"
    name: str
    enabled: bool = True
    query_terms: list[str] = Field(default_factory=list)
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_policy: Optional[str] = None  # "remote" | "onsite" | "hybrid"
    source_ids: list[str] = Field(default_factory=list)
    schedule_hint: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyDenied(Exception):
    def __init__(self, reason: str, error_code: str = "POLICY_DENIED") -> None:
        self.reason = reason
        self.error_code = error_code
        super().__init__(reason)


def convert_result_to_case(
    result: DiscoveryResult,
    case_type: str,
    approved_by: str,
    options: dict[str, Any] | None = None,
    require_approval: bool = True,
) -> "CaseFlowCase":
    """Convert a discovery result to a case with human approval gate."""
    from agent.caseflow.models import CaseFlowCase

    if options is None:
        options = {}

    if require_approval and not approved_by:
        raise PolicyDenied(
            "Human approval required to convert discovery result to case",
            "APPROVAL_REQUIRED",
        )
    if result.converted_to_case_id and not options.get("allow_duplicate"):
        raise PolicyDenied(
            "Result already converted to a case. Use allow_duplicate=true to override.",
            "DUPLICATE_CONVERSION",
        )

    case = CaseFlowCase(
        case_type=case_type,
        title=result.title,
        source="discovery",
        domain_payload={
            "source_result_id": result.id,
            "source_name": result.source_name,
            "source_url": result.source_url,
            **result.normalized_payload,
        },
        metadata={
            "discovery_run_id": result.run_id,
            "approved_by": approved_by,
        },
    )
    # Mark the result as converted
    result.converted_to_case_id = case.id
    return case
