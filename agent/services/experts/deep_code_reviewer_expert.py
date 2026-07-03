from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ReviewSeverity(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"
    SUGGESTION = "suggestion"
    QUESTION = "question"

@dataclass
class ReviewFinding:
    finding_id: str
    severity: ReviewSeverity
    path: str | None
    line: int | None
    title: str
    description: str
    evidence_ref: str | None

@dataclass
class ReviewReport:
    report_id: str
    run_id: str
    diff_artifact_ref: str | None
    context_bundle_ref: str | None
    blocking_count: int
    warning_count: int
    suggestion_count: int
    question_count: int
    findings: list[ReviewFinding]
    security_findings_artifact_ref: str | None
    overall_verdict: str
    created_at: float

    def has_blocking_issues(self) -> bool: return self.blocking_count > 0
    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id, "run_id": self.run_id,
            "blocking_count": self.blocking_count, "warning_count": self.warning_count,
            "overall_verdict": self.overall_verdict,
        }

class DeepCodeReviewerExpert:
    def create_report(self, *, run_id: str, findings: list[dict],
                     diff_artifact_ref: str | None = None,
                     context_bundle_ref: str | None = None) -> ReviewReport:
        built = []
        for f in findings:
            sev = ReviewSeverity(f.get("severity", "suggestion"))
            built.append(ReviewFinding(
                finding_id=str(uuid.uuid4())[:8],
                severity=sev, path=f.get("path"), line=f.get("line"),
                title=str(f.get("title", "Finding")), description=str(f.get("description", "")),
                evidence_ref=f.get("evidence_ref"),
            ))
        verdict = self.determine_verdict(built)
        return ReviewReport(
            report_id=str(uuid.uuid4()), run_id=run_id,
            diff_artifact_ref=diff_artifact_ref, context_bundle_ref=context_bundle_ref,
            blocking_count=sum(1 for f in built if f.severity == ReviewSeverity.BLOCKING),
            warning_count=sum(1 for f in built if f.severity == ReviewSeverity.WARNING),
            suggestion_count=sum(1 for f in built if f.severity == ReviewSeverity.SUGGESTION),
            question_count=sum(1 for f in built if f.severity == ReviewSeverity.QUESTION),
            findings=built, security_findings_artifact_ref=None,
            overall_verdict=verdict, created_at=time.time(),
        )

    def determine_verdict(self, findings: list[ReviewFinding]) -> str:
        if any(f.severity == ReviewSeverity.BLOCKING for f in findings): return "blocked"
        if any(f.severity == ReviewSeverity.WARNING for f in findings): return "needs_changes"
        if any(f.severity == ReviewSeverity.SUGGESTION for f in findings): return "approved_with_suggestions"
        return "approved"
