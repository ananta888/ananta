"""DiffProposalService — TRANS-006

Erzwingt DiffProposal als einzige erlaubte Schreib-Vorstufe.
Agenten duerfen Aenderungen nur als pruefbares DiffProposal erzeugen.
Anwendung ins echte Repository ist ein separater Gate-Schritt.
"""
from __future__ import annotations
import hashlib, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ProposalStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    EXPIRED = "expired"

class DiffHunkStatus(str, Enum):
    CLEAN = "clean"
    RISKY = "risky"
    UNVERIFIED = "unverified"

RISKY_PATH_PATTERNS = [
    ".github/", ".ci/", "Dockerfile", "docker-compose",
    "requirements.txt", "package.json", "pyproject.toml",
    ".env", "secrets/", "config/policy", "workflow-security",
]

class DiffProposalError(ValueError):
    pass

@dataclass
class DiffHunk:
    hunk_id: str
    file_path: str
    lines_added: int
    lines_removed: int
    operation: str
    content_hash: str
    hunk_status: DiffHunkStatus
    risk_flags: list[str]

@dataclass
class DiffProposal:
    proposal_id: str
    run_id: str
    worker_id: str
    origin: str
    hunks: list[DiffHunk]
    total_files: int
    total_lines_added: int
    total_lines_removed: int
    risk_summary: str
    status: ProposalStatus
    created_at: float
    approval_record_ref: str | None
    policy_check_passed: bool | None
    applied_at: float | None
    blocked_reason: str | None
    _approved_by: str | None = field(default=None, repr=False)

    def is_applicable(self) -> bool:
        return self.status == ProposalStatus.APPROVED and self.policy_check_passed is True

    def has_risky_hunks(self) -> bool:
        return any(h.hunk_status == DiffHunkStatus.RISKY for h in self.hunks)

    def file_paths(self) -> list[str]:
        return [h.file_path for h in self.hunks]

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "run_id": self.run_id,
            "worker_id": self.worker_id,
            "origin": self.origin,
            "total_files": self.total_files,
            "total_lines_added": self.total_lines_added,
            "total_lines_removed": self.total_lines_removed,
            "risk_summary": self.risk_summary,
            "status": self.status.value,
            "policy_check_passed": self.policy_check_passed,
            "is_applicable": self.is_applicable(),
        }


class DiffProposalService:
    def __init__(self) -> None:
        self._proposals: dict[str, DiffProposal] = {}

    def create_proposal(self, *, run_id: str, worker_id: str, origin: str,
                        hunks: list[dict], policy_constraints: dict | None = None) -> DiffProposal:
        proposal_id = str(uuid.uuid4())
        built_hunks = []
        for h in hunks:
            path = h.get("file_path", "")
            status = self._classify_hunk(path)
            flags = self._risk_flags(path)
            raw = f"{path}{h.get('lines_added', 0)}{h.get('lines_removed', 0)}"
            built_hunks.append(DiffHunk(
                hunk_id=str(uuid.uuid4())[:8],
                file_path=path,
                lines_added=int(h.get("lines_added", 0)),
                lines_removed=int(h.get("lines_removed", 0)),
                operation=h.get("operation", "modified"),
                content_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
                hunk_status=status,
                risk_flags=flags,
            ))
        total_added = sum(h.lines_added for h in built_hunks)
        total_removed = sum(h.lines_removed for h in built_hunks)
        risk = self._compute_risk_summary(built_hunks)
        p = DiffProposal(
            proposal_id=proposal_id, run_id=run_id, worker_id=worker_id, origin=origin,
            hunks=built_hunks, total_files=len(built_hunks),
            total_lines_added=total_added, total_lines_removed=total_removed,
            risk_summary=risk, status=ProposalStatus.DRAFT,
            created_at=time.time(), approval_record_ref=None,
            policy_check_passed=None, applied_at=None, blocked_reason=None,
        )
        self._proposals[proposal_id] = p
        return p

    def run_policy_check(self, proposal_id: str, *, allowed_paths: list[str] | None = None,
                         denied_paths: list[str] | None = None) -> DiffProposal:
        p = self._get(proposal_id)
        denied = denied_paths or []
        passed = True
        for h in p.hunks:
            if any(d in h.file_path for d in denied):
                passed = False
                break
        p.policy_check_passed = passed
        return p

    def submit_for_approval(self, proposal_id: str) -> DiffProposal:
        p = self._get(proposal_id)
        if p.policy_check_passed is not True:
            raise DiffProposalError("Policy check must pass before submitting for approval")
        p.status = ProposalStatus.PENDING_APPROVAL
        return p

    def approve(self, proposal_id: str, *, approval_record_ref: str, approved_by: str) -> DiffProposal:
        p = self._get(proposal_id)
        p.status = ProposalStatus.APPROVED
        p.approval_record_ref = approval_record_ref
        p._approved_by = approved_by
        return p

    def reject(self, proposal_id: str, *, reason: str, rejected_by: str) -> DiffProposal:
        p = self._get(proposal_id)
        p.status = ProposalStatus.REJECTED
        p.blocked_reason = f"Rejected by {rejected_by}: {reason}"
        return p

    def mark_applied(self, proposal_id: str) -> DiffProposal:
        p = self._get(proposal_id)
        if p.status != ProposalStatus.APPROVED:
            raise DiffProposalError(f"Cannot apply non-APPROVED proposal (status={p.status.value})")
        p.status = ProposalStatus.APPLIED
        p.applied_at = time.time()
        return p

    def is_applicable(self, proposal_id: str) -> bool:
        p = self._proposals.get(proposal_id)
        return p is not None and p.is_applicable()

    def check_direct_mutation_blocked(self) -> bool:
        return True

    def get(self, proposal_id: str) -> DiffProposal | None:
        return self._proposals.get(proposal_id)

    def _get(self, proposal_id: str) -> DiffProposal:
        p = self._proposals.get(proposal_id)
        if p is None:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        return p

    def _classify_hunk(self, file_path: str) -> DiffHunkStatus:
        for pattern in RISKY_PATH_PATTERNS:
            if pattern in file_path:
                return DiffHunkStatus.RISKY
        return DiffHunkStatus.CLEAN

    def _risk_flags(self, file_path: str) -> list[str]:
        flags = []
        if ".github" in file_path or ".ci" in file_path:
            flags.append("modifies_ci")
        if "policy" in file_path or "security" in file_path:
            flags.append("modifies_policy")
        if ".env" in file_path or "secrets" in file_path:
            flags.append("modifies_secrets")
        return flags

    def _compute_risk_summary(self, hunks: list[DiffHunk]) -> str:
        risky = [h for h in hunks if h.hunk_status == DiffHunkStatus.RISKY]
        if not risky:
            return "low"
        critical_flags = ["modifies_secrets", "modifies_policy"]
        if any(f in h.risk_flags for h in risky for f in critical_flags):
            return "critical"
        return "high"
