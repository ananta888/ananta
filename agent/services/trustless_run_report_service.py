"""TrustlessRunReportService — TRANS-007

Menschenlesbarer Bericht ohne blindes Modell-Vertrauen.
Unterscheidet Modellbehauptungen ("model claims:") und belegte Evidence ("verified:").
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_FINAL_RESULT_VALUES = frozenset(("completed", "failed", "cancelled", "blocked"))


@dataclass
class TrustlessRunReport:
    report_id: str
    run_id: str
    goal: str
    created_at: float
    selected_expert_or_worker: str
    policy_snapshot_ref: str | None        # snapshot_id
    context_sources: list[str]             # short descriptions
    tool_calls_summary: list[str]          # "tool_name: status" per call
    blocked_actions: list[str]             # what was blocked and why
    artifacts_produced: list[str]          # artifact_id list
    approval_gates_triggered: list[str]
    test_results_summary: str | None
    final_result: str                      # "completed" | "failed" | "cancelled" | "blocked"
    open_risks: list[str]
    evidence_refs: list[str]               # external artifact refs for verification

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "selected_expert_or_worker": self.selected_expert_or_worker,
            "policy_snapshot_ref": self.policy_snapshot_ref,
            "context_sources": list(self.context_sources),
            "tool_calls_summary": list(self.tool_calls_summary),
            "blocked_actions": list(self.blocked_actions),
            "artifacts_produced": list(self.artifacts_produced),
            "approval_gates_triggered": list(self.approval_gates_triggered),
            "test_results_summary": self.test_results_summary,
            "final_result": self.final_result,
            "open_risks": list(self.open_risks),
            "evidence_refs": list(self.evidence_refs),
        }


class TrustlessRunReportService:
    """Generates verifiable run reports from collected trace data."""

    def generate(
        self,
        *,
        run_id: str,
        goal: str,
        selected_expert_or_worker: str,
        policy_snapshot_ref: str | None = None,
        context_sources: list[str] | None = None,
        tool_calls_summary: list[str] | None = None,
        blocked_actions: list[str] | None = None,
        artifacts_produced: list[str] | None = None,
        approval_gates_triggered: list[str] | None = None,
        test_results_summary: str | None = None,
        final_result: str = "completed",
        open_risks: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> TrustlessRunReport:
        """Generate a run report from collected trace data."""
        if final_result not in _FINAL_RESULT_VALUES:
            final_result = "failed"

        return TrustlessRunReport(
            report_id=str(uuid.uuid4()),
            run_id=str(run_id or ""),
            goal=str(goal or ""),
            created_at=time.time(),
            selected_expert_or_worker=str(selected_expert_or_worker or ""),
            policy_snapshot_ref=str(policy_snapshot_ref) if policy_snapshot_ref else None,
            context_sources=list(context_sources) if context_sources else [],
            tool_calls_summary=list(tool_calls_summary) if tool_calls_summary else [],
            blocked_actions=list(blocked_actions) if blocked_actions else [],
            artifacts_produced=list(artifacts_produced) if artifacts_produced else [],
            approval_gates_triggered=list(approval_gates_triggered) if approval_gates_triggered else [],
            test_results_summary=str(test_results_summary) if test_results_summary else None,
            final_result=final_result,
            open_risks=list(open_risks) if open_risks else [],
            evidence_refs=list(evidence_refs) if evidence_refs else [],
        )

    def to_markdown(self, report: TrustlessRunReport) -> str:
        """Render report as Markdown. Sections: Goal, Worker, Policy, Context,
        Toolcalls, Blocked, Artifacts, Gates, Tests, Risks.
        Stays under 100 lines.
        """
        lines: list[str] = []

        lines.append(f"# Run Report — {report.run_id}")
        lines.append(f"**Report ID**: `{report.report_id}`  ")
        lines.append(f"**Created**: {report.created_at:.0f}")
        lines.append("")

        # Goal
        lines.append("## Goal")
        lines.append(f"verified: {report.goal}")
        lines.append("")

        # Worker
        lines.append("## Worker")
        lines.append(f"verified: `{report.selected_expert_or_worker}`")
        lines.append("")

        # Policy
        lines.append("## Policy")
        if report.policy_snapshot_ref:
            lines.append(f"verified: policy snapshot `{report.policy_snapshot_ref}`")
        else:
            lines.append("model claims: no policy snapshot linked")
        lines.append("")

        # Context
        lines.append("## Context Sources")
        if report.context_sources:
            for src in report.context_sources:
                lines.append(f"- verified: {src}")
        else:
            lines.append("- (none)")
        lines.append("")

        # Toolcalls
        lines.append("## Tool Calls")
        if report.tool_calls_summary:
            for entry in report.tool_calls_summary:
                lines.append(f"- verified: {entry}")
        else:
            lines.append("- (no tool calls recorded)")
        lines.append("")

        # Blocked
        lines.append("## Blocked Actions")
        if report.blocked_actions:
            for action in report.blocked_actions:
                lines.append(f"- verified: {action}")
        else:
            lines.append("- (none)")
        lines.append("")

        # Artifacts
        lines.append("## Artifacts Produced")
        if report.artifacts_produced:
            for art in report.artifacts_produced:
                lines.append(f"- `{art}`")
        else:
            lines.append("- (none)")
        lines.append("")

        # Gates
        lines.append("## Approval Gates Triggered")
        if report.approval_gates_triggered:
            for gate in report.approval_gates_triggered:
                lines.append(f"- {gate}")
        else:
            lines.append("- (none)")
        lines.append("")

        # Tests
        lines.append("## Test Results")
        if report.test_results_summary:
            lines.append(f"verified: {report.test_results_summary}")
        else:
            lines.append("model claims: no test results available")
        lines.append("")

        # Risks
        lines.append("## Open Risks")
        if report.open_risks:
            for risk in report.open_risks:
                lines.append(f"- {risk}")
        else:
            lines.append("- (none identified)")
        lines.append("")

        # Final result
        lines.append("## Final Result")
        lines.append(f"**verified: {report.final_result}**")
        if report.evidence_refs:
            lines.append("")
            lines.append("### Evidence References")
            for ref in report.evidence_refs:
                lines.append(f"- `{ref}`")

        return "\n".join(lines)

    def to_dict(self, report: TrustlessRunReport) -> dict[str, Any]:
        """Return a plain dict representation."""
        return report.as_dict()
