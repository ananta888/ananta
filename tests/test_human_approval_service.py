"""Tests for the human-approval service (WFG-024)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.human_approval_service import (  # noqa: E402
    DECISION_APPROVED,
    DECISION_DEFERRED,
    DECISION_PENDING,
    DECISION_REJECTED,
    HumanApprovalError,
    OPERATOR_DECISIONS,
    apply_human_decision,
    build_pending_approval_record,
    current_decision,
    is_pending_approval,
    submit_human_decision_via_repo,
)


# ---------------------------------------------------------------------------
# build_pending_approval_record
# ---------------------------------------------------------------------------


class TestBuildPendingApprovalRecord:
    def test_returns_canonical_block(self):
        record = build_pending_approval_record(
            goal_id="g-1", gate_task_id="ptask-gate"
        )
        assert record["schema"] == "workflow_gate_decision.v1"
        assert record["status"] == DECISION_PENDING
        assert record["decision_id"].startswith("hdec-")
        assert record["goal_id"] == "g-1"
        assert record["gate_task_id"] == "ptask-gate"
        assert record["raised_at"] > 0

    def test_unique_decision_id_per_call(self):
        a = build_pending_approval_record(goal_id="g", gate_task_id="t")
        b = build_pending_approval_record(goal_id="g", gate_task_id="t")
        assert a["decision_id"] != b["decision_id"]


# ---------------------------------------------------------------------------
# apply_human_decision
# ---------------------------------------------------------------------------


def _pending_task():
    return {
        "id": "ptask-gate",
        "goal_id": "g-1",
        "status": "blocked",
        "verification_status": {
            "gate": DECISION_PENDING,
            "gate_decision": build_pending_approval_record(
                goal_id="g-1", gate_task_id="ptask-gate"
            ),
        },
    }


class TestApplyHumanDecision:
    def test_approves_pending_gate(self):
        task = _pending_task()
        block = apply_human_decision(
            task=task, operator="alice", outcome=DECISION_APPROVED,
            reason="verified manually"
        )
        assert block["status"] == DECISION_APPROVED
        assert block["resolved_by"] == "alice"
        assert block["resolution_reason"] == "verified manually"
        # The legacy ``gate`` key is mirrored
        assert task["verification_status"]["gate"] == DECISION_APPROVED
        assert task["verification_status"]["gate_resolved"] is True

    def test_rejects_pending_gate(self):
        task = _pending_task()
        block = apply_human_decision(
            task=task, operator="bob", outcome=DECISION_REJECTED,
            reason="evidence missing"
        )
        assert block["status"] == DECISION_REJECTED

    def test_defers_pending_gate(self):
        task = _pending_task()
        block = apply_human_decision(
            task=task, operator="carol", outcome=DECISION_DEFERRED,
            reason="waiting for QA"
        )
        assert block["status"] == DECISION_DEFERRED
        # Deferred does NOT mirror to ``gate`` because the
        # gate is still effectively pending.
        assert task["verification_status"]["gate"] == DECISION_PENDING

    def test_preserves_existing_decision_id(self):
        task = _pending_task()
        existing_id = task["verification_status"]["gate_decision"]["decision_id"]
        block = apply_human_decision(
            task=task, operator="alice", outcome=DECISION_APPROVED
        )
        assert block["decision_id"] == existing_id

    def test_invalid_outcome_raises(self):
        task = _pending_task()
        with pytest.raises(HumanApprovalError):
            apply_human_decision(task=task, operator="alice", outcome="nuked")

    def test_empty_operator_raises(self):
        task = _pending_task()
        with pytest.raises(HumanApprovalError):
            apply_human_decision(task=task, operator="", outcome=DECISION_APPROVED)
        with pytest.raises(HumanApprovalError):
            apply_human_decision(task=task, operator="   ", outcome=DECISION_APPROVED)

    def test_works_on_task_without_existing_decision(self):
        task = {"id": "ptask-gate", "goal_id": "g-1", "verification_status": {}}
        block = apply_human_decision(
            task=task, operator="alice", outcome=DECISION_APPROVED
        )
        assert block["status"] == DECISION_APPROVED
        assert block["decision_id"].startswith("hdec-")

    def test_works_on_pydantic_style_object(self):
        class FakeTask:
            verification_status: dict = {}
            id = "ptask-gate"
            goal_id = "g-1"
        task = FakeTask()
        block = apply_human_decision(
            task=task, operator="alice", outcome=DECISION_APPROVED
        )
        assert block["status"] == DECISION_APPROVED
        # Pydantic-style attribute assignment must have stuck
        assert task.verification_status["gate"] == DECISION_APPROVED

    def test_resolution_timestamp_set(self):
        task = _pending_task()
        block = apply_human_decision(
            task=task, operator="alice", outcome=DECISION_APPROVED,
            timestamp=12345.0
        )
        assert block["resolved_at"] == 12345.0


# ---------------------------------------------------------------------------
# current_decision / is_pending_approval
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_current_decision_returns_block(self):
        task = _pending_task()
        block = current_decision(task)
        assert block is not None
        assert block["status"] == DECISION_PENDING

    def test_current_decision_returns_none_for_task_without(self):
        assert current_decision({"verification_status": {}}) is None
        assert current_decision({}) is None

    def test_is_pending_approval_true(self):
        assert is_pending_approval(_pending_task()) is True

    def test_is_pending_approval_false(self):
        task = _pending_task()
        apply_human_decision(task=task, operator="alice", outcome=DECISION_APPROVED)
        assert is_pending_approval(task) is False

    def test_is_pending_approval_false_for_no_decision(self):
        assert is_pending_approval({"verification_status": {}}) is False
        assert is_pending_approval({}) is False


# ---------------------------------------------------------------------------
# submit_human_decision_via_repo (smoke test)
# ---------------------------------------------------------------------------


class TestSubmitViaRepo:
    def test_missing_task_raises(self, monkeypatch):
        # Stub the task_repo so we don't need a live DB
        import sys
        fake_module = type(sys)("agent.repository.task_repo")
        def fake_get_by_id(task_id):
            return None
        fake_module.get_by_id = fake_get_by_id
        monkeypatch.setitem(sys.modules, "agent.repository.task_repo", fake_module)
        with pytest.raises(HumanApprovalError):
            submit_human_decision_via_repo(
                goal_id="g", gate_task_id="missing",
                operator="alice", outcome=DECISION_APPROVED
            )

    def test_invalid_outcome_raises_before_db(self, monkeypatch):
        # No task_repo call should happen for an invalid outcome
        called = {"n": 0}
        import sys
        fake_module = type(sys)("agent.repository.task_repo")
        def fake_get_by_id(task_id):
            called["n"] += 1
            return None
        fake_module.get_by_id = fake_get_by_id
        monkeypatch.setitem(sys.modules, "agent.repository.task_repo", fake_module)
        with pytest.raises(HumanApprovalError):
            submit_human_decision_via_repo(
                goal_id="g", gate_task_id="t",
                operator="alice", outcome="bogus"
            )
        assert called["n"] == 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_operator_decisions_contains_approved_rejected_deferred(self):
        assert DECISION_APPROVED in OPERATOR_DECISIONS
        assert DECISION_REJECTED in OPERATOR_DECISIONS
        assert DECISION_DEFERRED in OPERATOR_DECISIONS
        assert DECISION_PENDING not in OPERATOR_DECISIONS
