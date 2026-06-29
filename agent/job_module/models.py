"""Job Application Domain Models — CaseFlow specialization for job hunting."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RemotePolicy(str, Enum):
    remote = "remote"
    onsite = "onsite"
    hybrid = "hybrid"
    unknown = "unknown"


class EmploymentType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    internship = "internship"
    unknown = "unknown"


class JobApplicationPayload(BaseModel):
    """Domain-specific payload stored in CaseFlowCase.domain_payload."""
    company_name: str = ""
    role_title: str = ""
    job_url: Optional[str] = None
    source_name: str = "manual"
    location: Optional[str] = None
    remote_policy: RemotePolicy = RemotePolicy.unknown
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    employment_type: EmploymentType = EmploymentType.unknown
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    applied_at: Optional[datetime] = None
    response_due_at: Optional[datetime] = None
    interview_at: Optional[datetime] = None
    tech_stack: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    language_requirements: list[str] = Field(default_factory=list)


JOB_APPLICATION_STATUSES = [
    "found", "interesting", "preparing", "applied",
    "waiting_response", "interview", "offer", "rejected", "archived",
]
JOB_APPLICATION_INITIAL = "found"
JOB_APPLICATION_TERMINAL = ["archived"]

JOB_APPLICATION_TRANSITIONS: dict[str, list[str]] = {
    "found": ["interesting", "preparing", "archived"],
    "interesting": ["preparing", "archived"],
    "preparing": ["applied", "archived"],
    "applied": ["waiting_response", "interview", "rejected"],
    "waiting_response": ["interview", "rejected", "archived"],
    "interview": ["offer", "rejected", "waiting_response"],
    "offer": ["archived"],
    "rejected": ["archived"],
    "archived": [],  # terminal — reopen only with reason (special case)
}
