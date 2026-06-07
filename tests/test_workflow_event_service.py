"""Tests for the workflow handoff event service (WFG-015)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root on path so `import agent.*` works when pytest is run
# from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.workflow_event_service import (  # noqa: E402
    ALL_STATUSES,
    HandoffEvent,
    STATUS_BLOCKED,
    STATUS_CREATED,
    STATUS_REJECTED,
    STATUS_RELEASED,
    STATUS_WAIVED,
    WORKFLOW_HANDOFF_SCHEMA,
    append_handoff_to_task,
    build_handoff_event,
    derive_handoffs_for_steps,
    latest_handoff_for_pair,
    list_handoffs_for_goal,
    list_handoffs_for_task,
    record_handoff_to_audit_log,
)


# ---------------------------------------------------------------------------
# build_handoff_event
# ---------------------------------------------------------------------------


class TestBuildHandoffEvent:
    def test_minimal_event_roundtrips_to_dict(self):
        event = build_handoff_event(
            goal_id="goal-1",
            plan_id="plan-1",
            workflow_id="wf-1",
            from_step="planner",
            to_step="implementation",
            from_role="planner",
            to_role="developer",
        )
        payload = event.to_dict()
        assert payload["schema"] == WORKFLOW_HANDOFF_SCHEMA
        assert payload["goal_id"] == "goal-1"
        assert payload["plan_id"] == "plan-1"
        assert payload["from_step"] == "planner"
        assert payload["to_step"] == "implementation"
        assert payload["from_role"] == "planner"
        assert payload["to_role"] == "developer"
        assert payload["status"] == STATUS_CREATED
        assert payload["event_id"].startswith("hnd-")
        assert payload["timestamp"] > 0

    def test_drops_whitespace_only_strings(self):
        event = build_handoff_event(
            goal_id="g",
            plan_id="p",
            workflow_id="w",
            from_step="  a  ",
            to_step="\tb\t",
            from_role="",
            to_role=None,
            task_ids=["", "t1", "t2"],
            artifact_refs=["  ", "exec_plan"],
        )
        assert event.from_step == "a"
        assert event.to_step == "b"
        assert event.from_role == ""
        assert event.to_role == ""
        assert event.task_ids == ("t1", "t2")
        assert event.artifact_refs == ("exec_plan",)

    def test_unknown_status_clamps_to_created(self):
        event = build_handoff_event(
            goal_id="g",
            plan_id="p",
            workflow_id="w",
            from_step="a",
            to_step="b",
            from_role="a",
            to_role="b",
            status="vaporized",
        )
        assert event.status == STATUS_CREATED

    def test_known_statuses_pass_through(self):
        for status in ALL_STATUSES:
            event = build_handoff_event(
                goal_id="g",
                plan_id="p",
                workflow_id="w",
                from_step="a",
                to_step="b",
                from_role="a",
                to_role="b",
                status=status,
            )
            assert event.status == status

    def test_actor_defaults_to_system(self):
        event = build_handoff_event(
            goal_id="g",
            plan_id="p",
            workflow_id="w",
            from_step="a",
            to_step="b",
            from_role="a",
            to_role="b",
        )
        assert event.actor == "system"

    def test_actor_kept_when_provided(self):
        event = build_handoff_event(
            goal_id="g",
            plan_id="p",
            workflow_id="w",
            from_step="a",
            to_step="b",
            from_role="a",
            to_role="b",
            actor="human:alice",
        )
        assert event.actor == "human:alice"

    def test_event_id_is_deterministic(self):
        kwargs = dict(
            goal_id="g",
            plan_id="p",
            workflow_id="w",
            from_step="a",
            to_step="b",
            from_role="a",
            to_role="b",
            status=STATUS_CREATED,
            timestamp=1234.0,
        )
        a = build_handoff_event(**kwargs).to_dict()
        b = build_handoff_event(**kwargs).to_dict()
        assert a["event_id"] == b["event_id"]

    def test_event_id_changes_with_status(self):
        base = dict(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
            timestamp=1.0,
        )
        e1 = build_handoff_event(**base, status=STATUS_CREATED).to_dict()
        e2 = build_handoff_event(**base, status=STATUS_RELEASED).to_dict()
        e3 = build_handoff_event(**base, status=STATUS_BLOCKED).to_dict()
        ids = {e1["event_id"], e2["event_id"], e3["event_id"]}
        assert len(ids) == 3


# ---------------------------------------------------------------------------
# append_handoff_to_task
# ---------------------------------------------------------------------------


class TestAppendHandoffToTask:
    def test_appends_to_empty_context(self):
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        ctx = append_handoff_to_task(task={}, event=event)
        assert len(ctx["workflow_events"]) == 1
        assert ctx["workflow_events"][0]["to_step"] == "b"

    def test_appends_to_existing_context_without_mutating_input(self):
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        original_ctx = {"workflow_events": [{"event_id": "hnd-other", "x": 1}]}
        task = {"worker_execution_context": dict(original_ctx)}
        ctx = append_handoff_to_task(task=task, event=event)
        # New dict was returned; original is untouched.
        assert task["worker_execution_context"] == original_ctx
        assert len(ctx["workflow_events"]) == 2
        assert ctx["workflow_events"][0]["event_id"] == "hnd-other"
        assert ctx["workflow_events"][1]["to_step"] == "b"

    def test_idempotent_when_event_id_matches(self):
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        ctx1 = append_handoff_to_task(task={}, event=event)
        ctx2 = append_handoff_to_task(
            task={"worker_execution_context": ctx1}, event=event
        )
        assert len(ctx2["workflow_events"]) == 1

    def test_handles_non_dict_task(self):
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        ctx = append_handoff_to_task(task=None, event=event)
        assert len(ctx["workflow_events"]) == 1


# ---------------------------------------------------------------------------
# list_handoffs_for_task / list_handoffs_for_goal
# ---------------------------------------------------------------------------


class TestListHandoffs:
    def test_returns_empty_for_task_without_events(self):
        assert list_handoffs_for_task({}) == []
        assert list_handoffs_for_task({"worker_execution_context": {}}) == []

    def test_returns_sorted_by_timestamp(self):
        ctx = {
            "workflow_events": [
                {"event_id": "x1", "timestamp": 3.0, "to_step": "c"},
                {"event_id": "x2", "timestamp": 1.0, "to_step": "a"},
                {"event_id": "x3", "timestamp": 2.0, "to_step": "b"},
            ]
        }
        ordered = list_handoffs_for_task({"worker_execution_context": ctx})
        assert [e["to_step"] for e in ordered] == ["a", "b", "c"]

    def test_aggregate_across_tasks(self):
        tasks = [
            {"worker_execution_context": {"workflow_events": [
                {"event_id": "t1", "timestamp": 2.0, "to_step": "b"},
            ]}},
            {"worker_execution_context": {"workflow_events": [
                {"event_id": "t2", "timestamp": 1.0, "to_step": "a"},
                {"event_id": "t3", "timestamp": 3.0, "to_step": "c"},
            ]}},
        ]
        all_events = list_handoffs_for_goal(tasks=tasks)
        assert [e["to_step"] for e in all_events] == ["a", "b", "c"]

    def test_latest_for_pair_returns_most_recent(self):
        tasks = [
            {"worker_execution_context": {"workflow_events": [
                {"event_id": "p1", "timestamp": 1.0,
                 "from_step": "a", "to_step": "b", "status": STATUS_CREATED},
                {"event_id": "p2", "timestamp": 2.0,
                 "from_step": "a", "to_step": "b", "status": STATUS_RELEASED},
            ]}},
        ]
        latest = latest_handoff_for_pair(
            tasks=tasks, from_step="a", to_step="b"
        )
        assert latest["status"] == STATUS_RELEASED
        assert latest["event_id"] == "p2"

    def test_latest_for_pair_returns_none_when_missing(self):
        assert latest_handoff_for_pair(tasks=[], from_step="a", to_step="b") is None
        assert latest_handoff_for_pair(
            tasks=[{"worker_execution_context": {}}],
            from_step="a", to_step="b",
        ) is None

    def test_latest_for_pair_ignores_empty_strings(self):
        assert latest_handoff_for_pair(tasks=[], from_step="", to_step="") is None


# ---------------------------------------------------------------------------
# derive_handoffs_for_steps
# ---------------------------------------------------------------------------


class TestDeriveHandoffsForSteps:
    def test_emits_entry_point_handoff(self):
        steps = [{"id": "plan", "role": "planner", "depends_on": []}]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "ptask-plan"},
        )
        assert len(events) == 1
        assert events[0].from_step == "intake"
        assert events[0].to_step == "plan"
        assert events[0].from_role == "intake"
        assert events[0].to_role == "planner"
        assert events[0].task_ids == ("ptask-plan",)
        assert events[0].gate_required is False

    def test_emits_chain_handoffs_with_correct_roles(self):
        steps = [
            {"id": "plan", "role": "planner", "depends_on": []},
            {"id": "impl", "role": "developer", "depends_on": ["plan"]},
            {"id": "qa", "role": "qa_verifier", "depends_on": ["impl"]},
        ]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "ptask-1", "impl": "ptask-2", "qa": "ptask-3"},
        )
        # 1 entry-point + 2 chain edges = 3 events
        assert len(events) == 3
        assert events[0].from_step == "intake"
        assert events[0].to_step == "plan"
        assert events[1].from_step == "plan"
        assert events[1].to_step == "impl"
        assert events[1].from_role == "planner"
        assert events[1].to_role == "developer"
        assert events[2].from_step == "impl"
        assert events[2].to_step == "qa"
        assert events[2].from_role == "developer"
        assert events[2].to_role == "qa_verifier"

    def test_marks_gate_handoffs(self):
        steps = [
            {"id": "plan", "role": "planner", "depends_on": []},
            {"id": "g", "role": "scrum_master", "gate": True, "depends_on": ["plan"]},
            {"id": "impl", "role": "developer", "depends_on": ["g"]},
        ]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "p1", "g": "p2", "impl": "p3"},
        )
        gate_handoff = next(e for e in events if e.to_step == "g")
        impl_handoff = next(e for e in events if e.to_step == "impl")
        assert gate_handoff.gate_required is True
        assert gate_handoff.gate_task_id == "p2"
        assert impl_handoff.gate_required is True
        assert impl_handoff.gate_task_id == "p2"

    def test_skips_steps_without_task_id(self):
        steps = [
            {"id": "plan", "role": "planner", "depends_on": []},
            {"id": "impl", "role": "developer", "depends_on": ["plan"]},
        ]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "p1"},  # impl missing on purpose
        )
        # Only the intake -> plan entry point is emitted
        assert len(events) == 1
        assert events[0].to_step == "plan"

    def test_handles_non_list_input(self):
        assert derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=None, task_id_by_step={},
        ) == []

    def test_artifact_refs_passed_through(self):
        steps = [{"id": "plan", "role": "planner", "depends_on": []}]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "p1"},
            artifact_refs_by_step={"plan": ["goal_brief", "acceptance_criteria"]},
        )
        assert events[0].artifact_refs == ("goal_brief", "acceptance_criteria")

    def test_blueprint_provenance_kept(self):
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=[{"id": "plan", "role": "planner", "depends_on": []}],
            task_id_by_step={"plan": "p1"},
            blueprint_id="scrum_opencode",
            blueprint_version="2",
        )
        assert events[0].blueprint_id == "scrum_opencode"
        assert events[0].blueprint_version == "2"

    def test_dedupes_repeated_edge(self):
        # If two non-gate steps list the same dep twice (e.g. a graph
        # de-dup bug elsewhere), we still emit only one handoff.
        steps = [
            {"id": "plan", "role": "planner", "depends_on": []},
            {"id": "impl", "role": "developer", "depends_on": ["plan", "plan"]},
        ]
        events = derive_handoffs_for_steps(
            goal_id="g", plan_id="p", workflow_id="wf",
            steps=steps,
            task_id_by_step={"plan": "p1", "impl": "p2"},
        )
        # intake->plan + plan->impl (deduped) = 2
        assert len(events) == 2
        assert events[1].from_step == "plan"
        assert events[1].to_step == "impl"


# ---------------------------------------------------------------------------
# record_handoff_to_audit_log
# ---------------------------------------------------------------------------


class TestRecordHandoffToAuditLog:
    def test_logs_to_audit_with_status_specific_action(self, monkeypatch):
        captured = []

        def fake_log_audit(action, details):
            captured.append((action, details))

        # Stub the audit module the service imports lazily
        import sys
        fake_module = type(sys)("agent.common.audit")
        fake_module.log_audit = fake_log_audit
        monkeypatch.setitem(sys.modules, "agent.common.audit", fake_module)

        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
            status=STATUS_RELEASED,
        )
        record_handoff_to_audit_log(event=event)
        assert len(captured) == 1
        action, details = captured[0]
        assert action == "workflow_handoff_released"
        assert details["event_id"] == event.to_dict()["event_id"]
        assert details["goal_id"] == "g"
        assert details["details"]["to_step"] == "b"

    def test_swallows_audit_exceptions(self, monkeypatch):
        def broken_log_audit(*args, **kwargs):
            raise RuntimeError("db down")

        import sys
        fake_module = type(sys)("agent.common.audit")
        fake_module.log_audit = broken_log_audit
        monkeypatch.setitem(sys.modules, "agent.common.audit", fake_module)

        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        # Should not raise — the workflow is more important than the audit log.
        record_handoff_to_audit_log(event=event)

    def test_survives_missing_audit_module(self, monkeypatch):
        # Force the lazy import to fail
        monkeypatch.setattr(
            "agent.services.workflow_event_service.__loader__", None
        )
        # Use a fake module that raises on import
        import sys
        class BrokenAuditFinder:
            def find_module(self, name, path=None):
                if name == "agent.common.audit":
                    return self
            def load_module(self, name):
                raise ImportError("blocked for test")
        monkeypatch.setattr(
            sys, "meta_path",
            [finder for finder in sys.meta_path
             if finder is not BrokenAuditFinder()] + [BrokenAuditFinder()],
        )
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        # Should not raise
        record_handoff_to_audit_log(event=event)


# ---------------------------------------------------------------------------
# HandoffEvent dataclass
# ---------------------------------------------------------------------------


class TestHandoffEvent:
    def test_frozen(self):
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
        )
        with pytest.raises(Exception):
            event.goal_id = "other"  # type: ignore[misc]

    def test_to_dict_is_serializable(self):
        import json
        event = build_handoff_event(
            goal_id="g", plan_id="p", workflow_id="w",
            from_step="a", to_step="b", from_role="a", to_role="b",
            status=STATUS_WAIVED,
            reason_code="human_waived",
        )
        payload = event.to_dict()
        # Must be JSON-serializable (for the audit log / API response)
        json.dumps(payload)
