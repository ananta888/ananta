"""HeuristicProposal Review UI for the Operator TUI — T06.05.

User actions:
  approve(proposal_id)          → triggers validation + simulation, saves approval ref
  reject(proposal_id, reason)   → moves to heuristics/rejected/
  request_changes(proposal_id)  → attaches review note, status stays candidate
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.common.audit import log_audit
from agent.services.heuristic_runtime.activation_gate import register_human_approval
from agent.services.heuristic_runtime.proposal_service import ProposalService
from agent.services.heuristic_runtime.proposal_validator import (
    HeuristicProposal,
    HeuristicProposalValidator,
)
from agent.services.heuristic_runtime.simulation_harness import (
    HeuristicSimulationHarness,
    SimulationFixture,
)


@dataclass
class ReviewResult:
    success: bool
    action: str  # approve | reject | request_changes
    proposal_id: str
    reason: str = ""
    audit_ref: str = ""


class ProposalReviewView:
    """Provides list + detail display and user actions for proposal candidates."""

    def __init__(
        self,
        proposal_service: ProposalService | None = None,
        validator: HeuristicProposalValidator | None = None,
        harness: HeuristicSimulationHarness | None = None,
        reviewer: str = "operator",
    ) -> None:
        self._service = proposal_service or ProposalService()
        self._validator = validator or HeuristicProposalValidator()
        self._harness = harness or HeuristicSimulationHarness()
        self._reviewer = reviewer

    # ── List ──────────────────────────────────────────────────────────────────

    def list_open_candidates(self) -> list[dict[str, Any]]:
        return [
            p for p in self._service.list_candidates()
            if p.get("status") == "candidate"
        ]

    def pending_count(self) -> int:
        return len(self.list_open_candidates())

    # ── Detail ────────────────────────────────────────────────────────────────

    def render_detail(self, proposal_data: dict[str, Any]) -> str:
        lines = [
            f"Proposal: {proposal_data.get('proposal_id', '?')}",
            f"Domain:   {proposal_data.get('domain', '?')}",
            f"Version:  {proposal_data.get('version', '?')}",
            f"By:       {proposal_data.get('proposed_by', '?')}",
            "",
            f"Description: {proposal_data.get('description', '')}",
            f"Rationale:   {proposal_data.get('parameters', {}).get('rationale', proposal_data.get('rationale', ''))}",
            "",
            "Risks:",
        ]
        params = proposal_data.get("parameters") or {}
        risks = params.get("risks") or proposal_data.get("risks") or []
        for r in risks:
            lines.append(f"  - {r}")
        lines.append("")
        lines.append("Required tests:")
        required_tests = params.get("required_tests") or proposal_data.get("required_tests") or []
        for t in required_tests:
            lines.append(f"  - {t}")
        lines.append("")
        changes = params.get("proposed_changes") or proposal_data.get("proposed_changes") or "(no diff provided)"
        lines.append(f"Proposed changes: {changes}")
        return "\n".join(lines)

    # ── Actions ───────────────────────────────────────────────────────────────

    def approve(
        self,
        proposal_id: str,
        *,
        simulation_fixtures: list[SimulationFixture] | None = None,
    ) -> ReviewResult:
        data = self._load_candidate(proposal_id)
        if data is None:
            return ReviewResult(success=False, action="approve", proposal_id=proposal_id,
                                reason="candidate_not_found")

        proposal = _dict_to_proposal(data)
        validation = self._validator.validate(proposal)
        if not validation.passed:
            return ReviewResult(success=False, action="approve", proposal_id=proposal_id,
                                reason=f"validation_failed:{','.join(validation.reason_codes)}")

        approval_ref = register_human_approval(proposal_id, approved_by=self._reviewer)

        log_audit("heuristic_proposal_approved", {
            "proposal_id": proposal_id,
            "audit_ref": approval_ref,
            "reviewer": self._reviewer,
            "domain": data.get("domain"),
        })

        data["status"] = "approved"
        data["human_approval_ref"] = approval_ref
        data["approved_at"] = time.time()
        self._write_candidate(proposal_id, data)

        return ReviewResult(success=True, action="approve", proposal_id=proposal_id,
                            audit_ref=approval_ref)

    def reject(self, proposal_id: str, *, reason: str = "") -> ReviewResult:
        data = self._load_candidate(proposal_id)
        if data is None:
            return ReviewResult(success=False, action="reject", proposal_id=proposal_id,
                                reason="candidate_not_found")

        base_path = self._service._base_path
        rejected_dir = os.path.join(base_path, "rejected")
        os.makedirs(rejected_dir, exist_ok=True)
        data["status"] = "rejected"
        data["rejected_at"] = time.time()
        data["reject_reason"] = reason

        rejected_path = os.path.join(rejected_dir, f"{proposal_id}.json")
        with open(rejected_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        src = self._candidate_path(proposal_id)
        if os.path.exists(src):
            os.remove(src)

        audit_ref = str(uuid.uuid4())
        log_audit("heuristic_proposal_rejected", {
            "proposal_id": proposal_id,
            "audit_ref": audit_ref,
            "reviewer": self._reviewer,
            "reason": reason,
        })

        return ReviewResult(success=True, action="reject", proposal_id=proposal_id,
                            reason=reason, audit_ref=audit_ref)

    def request_changes(self, proposal_id: str, *, notes: str) -> ReviewResult:
        data = self._load_candidate(proposal_id)
        if data is None:
            return ReviewResult(success=False, action="request_changes",
                                proposal_id=proposal_id, reason="candidate_not_found")

        data["review_notes"] = notes
        data["review_requested_at"] = time.time()
        self._write_candidate(proposal_id, data)

        audit_ref = str(uuid.uuid4())
        log_audit("heuristic_proposal_changes_requested", {
            "proposal_id": proposal_id,
            "audit_ref": audit_ref,
            "reviewer": self._reviewer,
            "notes_length": len(notes),
        })

        return ReviewResult(success=True, action="request_changes",
                            proposal_id=proposal_id, audit_ref=audit_ref)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _candidate_path(self, proposal_id: str) -> str:
        return os.path.join(self._service._base_path, "candidates", f"{proposal_id}.json")

    def _load_candidate(self, proposal_id: str) -> dict[str, Any] | None:
        path = self._candidate_path(proposal_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_candidate(self, proposal_id: str, data: dict[str, Any]) -> None:
        path = self._candidate_path(proposal_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _dict_to_proposal(data: dict[str, Any]) -> HeuristicProposal:
    params = data.get("parameters") or {}
    return HeuristicProposal(
        proposal_id=str(data.get("proposal_id") or ""),
        proposed_by=str(data.get("proposed_by") or "ananta-worker"),
        domain=str(data.get("domain") or "tui_snake"),
        strategy_kind=str(data.get("strategy_kind") or "follow"),
        description=str(data.get("description") or ""),
        capabilities=list(data.get("capabilities") or []),
        requested_ttl_seconds=float(data.get("requested_ttl_seconds") or 7.0),
        safety_class=str(data.get("safety_class") or "bounded"),
        deterministic=bool(data.get("deterministic", True)),
        base_heuristic_ref=data.get("base_heuristic_ref"),
        human_approval_ref=data.get("human_approval_ref"),
        version=str(data.get("version") or "1.0.0"),
        parameters=dict(params),
    )
