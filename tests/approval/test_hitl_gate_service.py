"""Tests for HITLGateService — COSMOS-006."""
from __future__ import annotations

import time

import pytest

from agent.services.hitl_gate_service import (
    GateDecision,
    GateType,
    HITLGateError,
    HITLGateService,
    RiskLevel,
)


def _service() -> HITLGateService:
    return HITLGateService(default_ttl_seconds=3600)


def _pending(svc: HITLGateService, **kwargs):
    defaults = dict(
        run_id="run-1",
        gate_type=GateType.APPLY_DIFF,
        reason="unit-test",
    )
    defaults.update(kwargs)
    return svc.request_approval(**defaults)


# ── Creation ──────────────────────────────────────────────────────────────────

def test_request_creates_pending_gate():
    svc = _service()
    req = _pending(svc)
    assert req.decision == GateDecision.PENDING
    assert req.run_id == "run-1"
    assert req.gate_id
    assert req.decided_by is None
    assert req.decided_at is None


# ── Approve / Reject ──────────────────────────────────────────────────────────

def test_approve_transitions_to_approved():
    svc = _service()
    req = _pending(svc)
    approved = svc.approve(req.gate_id, decided_by="admin")
    assert approved.decision == GateDecision.APPROVED
    assert approved.decided_by == "admin"
    assert approved.decided_at is not None


def test_reject_transitions_to_rejected():
    svc = _service()
    req = _pending(svc, gate_type=GateType.DELETE_FILE)
    rejected = svc.reject(req.gate_id, decided_by="admin", reason="not allowed")
    assert rejected.decision == GateDecision.REJECTED
    assert rejected.decided_by == "admin"


def test_approve_expired_raises():
    """Approving a gate past its TTL must raise HITLGateError."""
    svc = HITLGateService(default_ttl_seconds=0)
    req = svc.request_approval(
        run_id="run-1",
        gate_type=GateType.APPLY_DIFF,
        reason="test",
        ttl_seconds=0,
    )
    time.sleep(0.02)  # ensure expires_at is in the past
    with pytest.raises(HITLGateError):
        svc.approve(req.gate_id, decided_by="admin")


# ── is_approved ───────────────────────────────────────────────────────────────

def test_is_approved_false_for_pending():
    svc = _service()
    req = _pending(svc)
    assert svc.is_approved(req.gate_id) is False


def test_is_approved_false_for_rejected():
    svc = _service()
    req = _pending(svc)
    svc.reject(req.gate_id, decided_by="admin")
    assert svc.is_approved(req.gate_id) is False


# ── Expiry sweep ──────────────────────────────────────────────────────────────

def test_check_expired_marks_gates():
    svc = HITLGateService(default_ttl_seconds=0)
    req = svc.request_approval(
        run_id="run-1",
        gate_type=GateType.APPLY_DIFF,
        reason="test",
        ttl_seconds=0,
    )
    time.sleep(0.02)
    updated = svc.check_expired_and_update()
    assert req.gate_id in updated
    assert svc._gates[req.gate_id].decision == GateDecision.EXPIRED


# ── Policy helpers ────────────────────────────────────────────────────────────

def test_risk_level_apply_diff_is_medium():
    svc = _service()
    assert svc.risk_level_for_gate_type(GateType.APPLY_DIFF) == RiskLevel.MEDIUM


def test_risk_level_delete_file_is_high():
    svc = _service()
    assert svc.risk_level_for_gate_type(GateType.DELETE_FILE) == RiskLevel.HIGH


def test_required_role_critical_is_owner():
    svc = _service()
    assert svc.required_role_for_risk(RiskLevel.CRITICAL) == "owner"


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_idempotent_approve():
    """Double-approve must not raise and must keep the original decision metadata."""
    svc = _service()
    req = _pending(svc)
    svc.approve(req.gate_id, decided_by="admin")
    # Second approve by a different actor — should be a no-op.
    result = svc.approve(req.gate_id, decided_by="admin2")
    assert result.decision == GateDecision.APPROVED
    # First approver is preserved.
    assert result.decided_by == "admin"
