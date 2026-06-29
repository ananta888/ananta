"""Job Fit Scoring — explainable multi-dimensional fit evaluation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


@dataclass
class EvidenceRef:
    artifact_id: Optional[str] = None
    field_path: Optional[str] = None
    quote_hash: Optional[str] = None
    explanation: str = ""


@dataclass
class SubScore:
    score: Optional[float]  # None = unknown
    explanation: str = ""
    evidence: list[EvidenceRef] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "explanation": self.explanation,
            "evidence": [
                {
                    "artifact_id": e.artifact_id,
                    "field_path": e.field_path,
                    "quote_hash": e.quote_hash,
                    "explanation": e.explanation,
                }
                for e in self.evidence
            ],
        }


class JobFitScore(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    source: str = "ai"  # "ai" | "manual"
    technical_fit: Optional[SubScore] = None
    domain_fit: Optional[SubScore] = None
    seniority_fit: Optional[SubScore] = None
    location_fit: Optional[SubScore] = None
    remote_fit: Optional[SubScore] = None
    salary_fit: Optional[SubScore] = None
    risk_score: Optional[SubScore] = None
    effort_score: Optional[SubScore] = None
    final_score: Optional[float] = None
    manual_override: Optional[float] = None
    manual_override_reason: Optional[str] = None
    trace_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"arbitrary_types_allowed": True}

    def compute_final_score(self) -> Optional[float]:
        if self.manual_override is not None:
            return self.manual_override
        subscores = [
            s for s in [
                self.technical_fit, self.domain_fit, self.seniority_fit,
                self.location_fit, self.remote_fit, self.salary_fit,
            ]
            if s is not None and s.score is not None
        ]
        if not subscores:
            return None
        return sum(s.score for s in subscores) / len(subscores)

    def model_dump(self, **kwargs) -> dict[str, Any]:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "source": self.source,
            "technical_fit": self.technical_fit.model_dump() if self.technical_fit else None,
            "domain_fit": self.domain_fit.model_dump() if self.domain_fit else None,
            "seniority_fit": self.seniority_fit.model_dump() if self.seniority_fit else None,
            "location_fit": self.location_fit.model_dump() if self.location_fit else None,
            "remote_fit": self.remote_fit.model_dump() if self.remote_fit else None,
            "salary_fit": self.salary_fit.model_dump() if self.salary_fit else None,
            "risk_score": self.risk_score.model_dump() if self.risk_score else None,
            "effort_score": self.effort_score.model_dump() if self.effort_score else None,
            "final_score": self.final_score,
            "manual_override": self.manual_override,
            "manual_override_reason": self.manual_override_reason,
            "trace_id": self.trace_id,
            "agent_run_id": self.agent_run_id,
            "created_at": self.created_at.isoformat(),
        }
