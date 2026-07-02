"""HITL Gate Service — COSMOS-006

Human-in-the-loop Policy Gates on Run level.

Ergänzt human_approval_service.py (WFG-024 / gate-task-level) um
Gate-Typen auf Run-Ebene. Fehlende oder abgelaufene Approvals
blockieren HART — kein Fallback auf "allow".
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enumerations ──────────────────────────────────────────────────────────────

class GateType(str, Enum):
    APPLY_DIFF = "apply_diff"
    DELETE_FILE = "delete_file"
    RUN_NETWORK_TOOL = "run_network_tool"
    SEND_CONTEXT_EXTERNAL = "send_context_external"
    CREATE_PULL_REQUEST = "create_pull_request"
    MERGE_PULL_REQUEST = "merge_pull_request"
    RERUN_CI = "rerun_ci"
    ACCESS_SECRET_REF = "access_secret_ref"
    DEPLOY_OR_RELEASE = "deploy_or_release"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    gate_id: str
    run_id: str
    gate_type: GateType
    risk_level: RiskLevel
    required_role: str        # "operator" | "maintainer" | "owner"
    reason: str
    artifacts: list[str]      # artifact_ids als Evidence
    created_at: float
    expires_at: float         # unix timestamp
    decision: GateDecision
    decided_by: str | None
    decided_at: float | None
    audit_ref: str | None     # Ref auf Audit-Event


# ── Static policy tables ──────────────────────────────────────────────────────

_GATE_TYPE_RISK: dict[GateType, RiskLevel] = {
    GateType.APPLY_DIFF: RiskLevel.MEDIUM,
    GateType.DELETE_FILE: RiskLevel.HIGH,
    GateType.RUN_NETWORK_TOOL: RiskLevel.MEDIUM,
    GateType.SEND_CONTEXT_EXTERNAL: RiskLevel.HIGH,
    GateType.CREATE_PULL_REQUEST: RiskLevel.MEDIUM,
    GateType.MERGE_PULL_REQUEST: RiskLevel.CRITICAL,
    GateType.RERUN_CI: RiskLevel.LOW,
    GateType.ACCESS_SECRET_REF: RiskLevel.CRITICAL,
    GateType.DEPLOY_OR_RELEASE: RiskLevel.CRITICAL,
}

_RISK_ROLE: dict[RiskLevel, str] = {
    RiskLevel.LOW: "operator",
    RiskLevel.MEDIUM: "operator",
    RiskLevel.HIGH: "maintainer",
    RiskLevel.CRITICAL: "owner",
}


# ── Exception ─────────────────────────────────────────────────────────────────

class HITLGateError(ValueError):
    """Raised when a gate operation is invalid (e.g. approve on expired gate)."""


# ── Service ───────────────────────────────────────────────────────────────────

class HITLGateService:
    """Human-in-the-loop Policy Gates.

    Ergänzt human_approval_service.py für Gate-Typen auf Run-Ebene.
    Fehlende oder abgelaufene Approvals blockieren HART.
    """

    def __init__(self, default_ttl_seconds: int = 3600) -> None:
        self._default_ttl = default_ttl_seconds
        self._gates: dict[str, ApprovalRequest] = {}

    # ── Creation ──────────────────────────────────────────────────────────────

    def request_approval(
        self,
        *,
        run_id: str,
        gate_type: GateType,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        reason: str,
        required_role: str = "operator",
        artifacts: list[str] | None = None,
        ttl_seconds: int | None = None,
    ) -> ApprovalRequest:
        """Create an approval request. Returns a PENDING gate."""
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        gate = ApprovalRequest(
            gate_id=str(uuid.uuid4()),
            run_id=run_id,
            gate_type=gate_type,
            risk_level=risk_level,
            required_role=required_role,
            reason=reason,
            artifacts=list(artifacts or []),
            created_at=now,
            expires_at=now + ttl,
            decision=GateDecision.PENDING,
            decided_by=None,
            decided_at=None,
            audit_ref=None,
        )
        self._gates[gate.gate_id] = gate
        return gate

    # ── Decisions ─────────────────────────────────────────────────────────────

    def approve(self, gate_id: str, *, decided_by: str) -> ApprovalRequest:
        """Approve a pending gate.

        - Already APPROVED or REJECTED → idempotent, returns gate unchanged.
        - PENDING but expired → raises HITLGateError (marks gate EXPIRED first).
        """
        gate = self._get_or_raise(gate_id)
        if gate.decision in (GateDecision.APPROVED, GateDecision.REJECTED):
            return gate
        # Gate is PENDING — check expiry before approving.
        if gate.expires_at < time.time():
            gate.decision = GateDecision.EXPIRED
            raise HITLGateError(
                f"Gate {gate_id} has expired and cannot be approved."
            )
        gate.decision = GateDecision.APPROVED
        gate.decided_by = decided_by
        gate.decided_at = time.time()
        gate.audit_ref = str(uuid.uuid4())
        return gate

    def reject(
        self, gate_id: str, *, decided_by: str, reason: str = ""
    ) -> ApprovalRequest:
        """Reject a pending gate. Idempotent if already decided."""
        gate = self._get_or_raise(gate_id)
        if gate.decision in (GateDecision.APPROVED, GateDecision.REJECTED):
            return gate
        gate.decision = GateDecision.REJECTED
        gate.decided_by = decided_by
        gate.decided_at = time.time()
        gate.audit_ref = str(uuid.uuid4())
        return gate

    # ── Queries ───────────────────────────────────────────────────────────────

    def is_approved(self, gate_id: str) -> bool:
        """True iff gate exists, decision=APPROVED, and not yet expired."""
        gate = self._gates.get(gate_id)
        if gate is None:
            return False
        if gate.decision != GateDecision.APPROVED:
            return False
        # An already-approved gate is valid even past expires_at.
        return True

    def is_expired(self, gate_id: str) -> bool:
        """True iff gate exists and expires_at is in the past."""
        gate = self._gates.get(gate_id)
        if gate is None:
            return False
        return gate.expires_at < time.time()

    def check_expired_and_update(self) -> list[str]:
        """Mark all PENDING gates whose expires_at has passed as EXPIRED.

        Returns the list of updated gate_ids.
        """
        updated: list[str] = []
        now = time.time()
        for gate_id, gate in self._gates.items():
            if gate.decision == GateDecision.PENDING and gate.expires_at < now:
                gate.decision = GateDecision.EXPIRED
                updated.append(gate_id)
        return updated

    def get_pending(self, run_id: str | None = None) -> list[ApprovalRequest]:
        """Return all PENDING gates, optionally filtered by run_id."""
        result = [
            g for g in self._gates.values()
            if g.decision == GateDecision.PENDING
        ]
        if run_id is not None:
            result = [g for g in result if g.run_id == run_id]
        return result

    # ── Policy helpers ────────────────────────────────────────────────────────

    def risk_level_for_gate_type(self, gate_type: GateType) -> RiskLevel:
        """Return the default risk level for a gate type."""
        return _GATE_TYPE_RISK.get(gate_type, RiskLevel.MEDIUM)

    def required_role_for_risk(self, risk_level: RiskLevel) -> str:
        """low/medium → operator, high → maintainer, critical → owner."""
        return _RISK_ROLE.get(risk_level, "operator")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_or_raise(self, gate_id: str) -> ApprovalRequest:
        gate = self._gates.get(gate_id)
        if gate is None:
            raise HITLGateError(f"Gate {gate_id!r} not found.")
        return gate
