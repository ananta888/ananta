from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from typing import Any

@dataclass
class PRBody:
    pr_body_id: str
    run_id: str
    title: str
    goal: str
    changed_files: list[str]
    summary: str
    test_status: str  # "passed"|"failed"|"not_run"|"unknown"
    risks: list[str]
    open_questions: list[str]
    diff_artifact_ref: str | None
    test_report_ref: str | None
    risk_report_ref: str | None
    created_at: float
    approval_required_for_github: bool = True

    def to_markdown(self) -> str:
        lines = [
            f"## {self.title}", "",
            f"**Goal:** {self.goal}", "",
            "### Changed Files",
            *[f"- `{f}`" for f in self.changed_files], "",
            f"### Summary\n{self.summary[:500]}", "",
            f"### Test Status: {self.test_status}",
        ]
        if self.risks:
            lines += ["", "### Risks", *[f"- {r}" for r in self.risks]]
        if self.open_questions:
            lines += ["", "### Open Questions", *[f"- {q}" for q in self.open_questions]]
        if self.diff_artifact_ref:
            lines += ["", f"**Diff:** `{self.diff_artifact_ref}`"]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pr_body_id": self.pr_body_id, "run_id": self.run_id, "title": self.title,
            "test_status": self.test_status, "changed_files": self.changed_files,
            "approval_required_for_github": self.approval_required_for_github,
        }

class PRAuthorExpert:
    def create_pr_body(self, *, run_id: str, goal: str, changed_files: list[str],
                      diff_summary: str = "", test_status: str = "not_run",
                      risks: list[str] | None = None, diff_artifact_ref: str | None = None,
                      test_report_ref: str | None = None, risk_report_ref: str | None = None) -> PRBody:
        title = goal[:72] if goal else "Update"
        return PRBody(
            pr_body_id=str(uuid.uuid4()), run_id=run_id, title=title, goal=goal,
            changed_files=list(changed_files), summary=diff_summary[:500] or f"Changes to {len(changed_files)} files",
            test_status=test_status, risks=list(risks or []), open_questions=[],
            diff_artifact_ref=diff_artifact_ref, test_report_ref=test_report_ref,
            risk_report_ref=risk_report_ref, created_at=time.time(), approval_required_for_github=True,
        )

    def validate_for_github(self, pr_body: PRBody) -> list[str]:
        issues = []
        if not pr_body.title.strip(): issues.append("Empty title")
        if not pr_body.changed_files: issues.append("No changed files")
        return issues
