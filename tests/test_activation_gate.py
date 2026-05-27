"""Tests for HeuristicActivationGate — T07.03."""
from __future__ import annotations

import os
import json
import shutil
import tempfile

import pytest

from agent.services.heuristic_runtime.activation_gate import (
    HeuristicActivationGate,
    _AUDIT,
    get_audit_events,
    register_human_approval,
)
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry
from agent.services.heuristic_runtime.proposal_validator import HeuristicProposal
from agent.services.heuristic_runtime.simulation_harness import SimulationReport


@pytest.fixture(autouse=True)
def _clear_audit():
    _AUDIT.clear()
    yield
    _AUDIT.clear()


@pytest.fixture
def tmp_heuristic_dir(tmp_path):
    for d in ("active", "archive", "quarantine", "candidates"):
        (tmp_path / d).mkdir()
    return tmp_path


def _make_registry(base_path):
    reg = HeuristicRegistry(base_path=str(base_path))
    reg._loaded = True
    return reg


def _make_proposal(hid="prop-new", version="2.0.0", **kwargs):
    defaults = dict(
        proposal_id=hid,
        proposed_by="ananta-worker",
        domain="tui_snake",
        strategy_kind="follow",
        description="A test proposal",
        capabilities=["motion_suggest"],
        requested_ttl_seconds=7.0,
        safety_class="bounded",
        deterministic=True,
        version=version,
    )
    defaults.update(kwargs)
    return HeuristicProposal(**defaults)


def _gate(base_path):
    reg = _make_registry(base_path)
    return HeuristicActivationGate(registry=reg, base_path=str(base_path))


def _sim_report_pass(hid):
    return SimulationReport(
        candidate_id=hid, candidate_version="1.0.0",
        total_runs=5, success_count=5, no_match_count=0,
        wrong_context_count=0, policy_violation_count=0,
        expired_usage_count=0, total_latency_ms=10.0,
    )


# ── Activation blocked without approval ──────────────────────────────────────

def test_activation_blocked_without_human_approval(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal()  # no human_approval_ref
    result = gate.activate(proposal)
    assert not result.success
    assert "no_human_approval_ref" in result.reason


def test_activation_blocked_when_approval_not_in_audit(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(human_approval_ref="fake-ref")
    # audit log is empty → approval not found
    result = gate.activate(proposal)
    assert not result.success
    assert "human_approval_not_found_in_audit_log" in result.reason


# ── Activation succeeds with all gates ───────────────────────────────────────

def test_activation_succeeds_with_approval(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(proposal_id="prop-ok")
    approval_id = register_human_approval("prop-ok")
    proposal.human_approval_ref = approval_id

    result = gate.activate(proposal, simulation_report=_sim_report_pass("prop-ok"))
    assert result.success, result.reason
    assert result.heuristic_id == "prop-ok"
    assert os.path.exists(result.activated_path)


def test_activation_writes_json_file(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(proposal_id="prop-file")
    register_human_approval("prop-file")
    proposal.human_approval_ref = proposal.proposal_id  # shortcut for test
    # re-register with proper ref
    ref = register_human_approval("prop-file")
    proposal.human_approval_ref = ref

    result = gate.activate(proposal)
    assert result.success
    with open(result.activated_path) as f:
        data = json.load(f)
    assert data["heuristic_id"] == "prop-file"
    assert data["status"] == "active"


# ── Activation blocked by failed validation ───────────────────────────────────

def test_activation_blocked_by_capability_violation(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(capabilities=["network_access"])
    ref = register_human_approval(proposal.proposal_id)
    proposal.human_approval_ref = ref
    result = gate.activate(proposal)
    assert not result.success
    assert "validation_failed" in result.reason


# ── Activation blocked by simulation failure ──────────────────────────────────

def test_activation_blocked_by_simulation_failure(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(proposal_id="prop-sim-fail")
    ref = register_human_approval("prop-sim-fail")
    proposal.human_approval_ref = ref
    bad_report = SimulationReport(
        candidate_id="prop-sim-fail", candidate_version="1.0.0",
        total_runs=5, success_count=3, no_match_count=2,
        wrong_context_count=0, policy_violation_count=2,
        expired_usage_count=0, total_latency_ms=10.0,
    )
    result = gate.activate(proposal, simulation_report=bad_report)
    assert not result.success
    assert "simulation_failed" in result.reason


# ── Archives existing on second activation ────────────────────────────────────

def test_second_activation_archives_first(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    pid = "prop-archive-test"

    for version in ("1.0.0", "2.0.0"):
        proposal = _make_proposal(proposal_id=pid, version=version)
        ref = register_human_approval(pid)
        proposal.human_approval_ref = ref
        result = gate.activate(proposal)
        assert result.success

    archive_dir = os.path.join(str(tmp_heuristic_dir), "archive")
    archived = os.listdir(archive_dir)
    assert any(pid in f for f in archived)


# ── Rollback ──────────────────────────────────────────────────────────────────

def test_rollback_restores_archived_version(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    pid = "prop-rollback"
    archive_dir = os.path.join(str(tmp_heuristic_dir), "archive")

    # Create an archived version manually
    archived_file = os.path.join(archive_dir, f"{pid}-1.0.0.json")
    with open(archived_file, "w") as f:
        json.dump({"heuristic_id": pid, "version": "1.0.0"}, f)

    result = gate.rollback(pid, "1.0.0")
    assert result.success, result.reason
    active_file = os.path.join(str(tmp_heuristic_dir), "active", f"{pid}-1.0.0.json")
    assert os.path.exists(active_file)


def test_rollback_fails_if_archive_not_found(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    result = gate.rollback("nonexistent-h", "1.0.0")
    assert not result.success
    assert "archive_not_found" in result.reason


# ── Quarantine ────────────────────────────────────────────────────────────────

def test_quarantine_moves_active_to_quarantine(tmp_heuristic_dir):
    active_dir = os.path.join(str(tmp_heuristic_dir), "active")
    active_file = os.path.join(active_dir, "danger-h-1.0.0.json")
    with open(active_file, "w") as f:
        json.dump({"heuristic_id": "danger-h", "version": "1.0.0"}, f)

    gate = _gate(tmp_heuristic_dir)
    result = gate.quarantine("danger-h", reason="policy_violation_detected")
    assert result.success, result.reason
    assert not os.path.exists(active_file)
    quarantine_dir = os.path.join(str(tmp_heuristic_dir), "quarantine")
    assert any("danger-h" in f for f in os.listdir(quarantine_dir))


def test_quarantine_emits_audit_event(tmp_heuristic_dir):
    active_dir = os.path.join(str(tmp_heuristic_dir), "active")
    with open(os.path.join(active_dir, "q-h-1.0.0.json"), "w") as f:
        json.dump({"heuristic_id": "q-h"}, f)

    gate = _gate(tmp_heuristic_dir)
    gate.quarantine("q-h", reason="test_reason")
    events = get_audit_events("heuristic_quarantined")
    assert len(events) >= 1
    assert events[-1]["heuristic_id"] == "q-h"


def test_quarantine_fails_if_not_active(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    result = gate.quarantine("not-there", reason="test")
    assert not result.success


# ── Audit events ──────────────────────────────────────────────────────────────

def test_register_human_approval_emits_event():
    ref = register_human_approval("test-proposal")
    events = get_audit_events("heuristic_proposal_approved")
    assert any(e["event_id"] == ref for e in events)


def test_activation_emits_activated_event(tmp_heuristic_dir):
    gate = _gate(tmp_heuristic_dir)
    proposal = _make_proposal(proposal_id="prop-audit")
    ref = register_human_approval("prop-audit")
    proposal.human_approval_ref = ref
    gate.activate(proposal)
    events = get_audit_events("heuristic_activated")
    assert any(e.get("heuristic_id") == "prop-audit" for e in events)
