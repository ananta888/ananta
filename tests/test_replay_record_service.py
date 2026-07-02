"""Tests for ReplayRecordService — COSMOS-007."""
from __future__ import annotations

import pytest

from agent.services.replay_record_service import (
    ReplayAnalysis,
    ReplayRecord,
    ReplayRecordService,
)


def _service() -> ReplayRecordService:
    return ReplayRecordService()


def _full_record(svc: ReplayRecordService) -> ReplayRecord:
    """Create a ReplayRecord with all optional refs populated."""
    return svc.create_record(
        run_id="run-1",
        expert_id="expert-a",
        expert_version="1.0.0",
        config_hash="abc123",
        policy_snapshot_ref="ps-001",
        context_bundle_refs=["ctx-001", "ctx-002"],
        tool_call_log_ref="tcl-001",
        non_deterministic_refs=["nd-001"],
    )


# ── create_record ─────────────────────────────────────────────────────────────

def test_create_record_fields():
    svc = _service()
    rec = _full_record(svc)
    assert rec.replay_id
    assert rec.run_id == "run-1"
    assert rec.expert_id == "expert-a"
    assert rec.expert_version == "1.0.0"
    assert rec.config_hash == "abc123"
    assert rec.policy_snapshot_ref == "ps-001"
    assert rec.context_bundle_refs == ["ctx-001", "ctx-002"]
    assert rec.tool_call_log_ref == "tcl-001"
    assert rec.non_deterministic_refs == ["nd-001"]
    assert rec.created_at > 0
    assert rec.replay_status == "available"
    assert rec.unavailable_reason is None


# ── analyse ───────────────────────────────────────────────────────────────────

def test_analyse_can_analyse_with_log_ref():
    svc = _service()
    rec = _full_record(svc)
    analysis = svc.analyse(rec)
    assert analysis.can_analyse is True


def test_analyse_cannot_action_replay_without_snapshot():
    svc = _service()
    rec = svc.create_record(
        run_id="run-1",
        expert_id="expert-a",
        expert_version="1.0.0",
        config_hash="abc123",
        policy_snapshot_ref=None,
        tool_call_log_ref="tcl-001",
    )
    analysis = svc.analyse(rec)
    assert analysis.can_action_replay is False


# ── is_dry_run_safe ───────────────────────────────────────────────────────────

def test_is_dry_run_safe_without_snapshot():
    svc = _service()
    rec = svc.create_record(
        run_id="run-1",
        expert_id="expert-a",
        expert_version="1.0.0",
        config_hash="abc123",
        policy_snapshot_ref=None,
        tool_call_log_ref="tcl-001",
    )
    assert svc.is_dry_run_safe(rec) is False


def test_is_dry_run_safe_with_snapshot():
    svc = _service()
    rec = _full_record(svc)
    assert svc.is_dry_run_safe(rec) is True


# ── mark_non_deterministic ────────────────────────────────────────────────────

def test_mark_non_deterministic_adds_entry():
    svc = _service()
    rec = _full_record(svc)
    svc.mark_non_deterministic(rec.replay_id, "llm_response_step_3")
    analysis = svc.analyse(rec)
    assert "llm_response_step_3" in analysis.non_deterministic_steps


# ── to_dict ───────────────────────────────────────────────────────────────────

def test_to_dict_no_nones():
    """None fields must be Python None, never the string 'None'."""
    svc = _service()
    rec = _full_record(svc)
    d = svc.to_dict(rec)
    for key, value in d.items():
        assert value != "None", f"Field {key!r} contains the string 'None'"
    assert d["policy_snapshot_ref"] == "ps-001"
    assert d["tool_call_log_ref"] == "tcl-001"
    assert d["unavailable_reason"] is None


# ── estimated_fidelity ────────────────────────────────────────────────────────

def test_estimated_fidelity_decreases_with_missing():
    """A record with all refs should have higher fidelity than one with none."""
    svc = _service()
    full_rec = _full_record(svc)
    partial_rec = svc.create_record(
        run_id="run-2",
        expert_id="expert-a",
        expert_version="1.0.0",
        config_hash="abc123",
        # no policy_snapshot_ref, no tool_call_log_ref, no context_bundle_refs
    )
    full_fidelity = svc.analyse(full_rec).estimated_fidelity
    partial_fidelity = svc.analyse(partial_rec).estimated_fidelity
    assert full_fidelity > partial_fidelity
