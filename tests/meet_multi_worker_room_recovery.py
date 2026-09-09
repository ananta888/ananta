"""Actual packaged recovery under native Hub SQL/phase policy and private Meet."""

import time

import pytest
from sqlalchemy import select

from agent.repositories.meet_dialog_phases import TaskDialogPhases
from agent.repositories.meet_dialog_recovery import SqlDialogRecovery, recoveries
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_phases import MeetDialogPhases
from agent.services.meet_dialog_recovery import MeetDialogRecovery
from agent.services.task_runtime_service import compare_and_set_local_task_status


class MultiWorkerRoomRecovery:
    def __init__(self, enabled):
        self.enabled = enabled
        self.engine = None
        self.elapsed = []
        self.dispatched = []

    def observe_dispatch(self, monkeypatch, router):
        if not self.enabled:
            return
        start = router.start_dialog

        def recorded(assignment):
            assert len(self.dispatched) < 2, "test_reconnect_unexpected_redispatch"
            self.dispatched.append(assignment["task_id"])
            return start(assignment)

        monkeypatch.setattr(router, "start_dialog", recorded)

    def service_options(self, engine, authority, tasks, meet, issuer):
        if not self.enabled:
            return {}
        self.engine = engine
        states = SqlDialogRecovery(engine)
        states.initialize()
        phases = MeetDialogPhases(
            TaskDialogPhases(tasks, task_status_cas=compare_and_set_local_task_status),
            authority,
            meet,
        )
        recovery = MeetDialogRecovery(authority, states, meet, issuer, phases)
        meet.recovery = recovery
        return {"phases": phases, "recovery": recovery}

    def current(self, task_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(recoveries).where(recoveries.c.task_id == task_id)).mappings().one()
            return dict(row)

    def wait_active(self, task_id, attempt):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            row = self.current(task_id)
            if row["state"] == "active" and row["attempt"] == attempt:
                return row
            time.sleep(0.05)
        raise AssertionError("test_reconnect_active_membership_missing")

    def exercise(self, app, service, started, command):
        if not self.enabled:
            return
        task_id = started[0]["task_id"]
        with app.app_context():
            task = service.tasks.get_by_id(task_id)
            context = task.worker_execution_context["meet_dialog"]
            ids = task_id, context["lease_id"], context["runtime_id"]
            original = {name: context[name] for name in ("lease_id", "runtime_id", "session_id", "deadline")}
            destination = task.assigned_agent_url
            assert context["reconnect"] is True
        previous = self.wait_active(task_id, 0)
        for attempt in (1, 2):
            began = time.monotonic()
            assert command("disconnect") == {"interrupted": 0, "attempt": attempt}
            assert command("recovered") == {
                "rejoined": True,
                "participants": 3,
                "retiredAbsent": True,
                "sameDevice": True,
            }
            assert command("screens") == {"moving": [True, True], "departedAbsent": False}
            row = self.wait_active(task_id, attempt)
            elapsed = (time.monotonic() - began) * 1000
            assert 4000 <= elapsed < 30000
            self.elapsed.append(round(elapsed, 2))
            assert row["assignment_digest"] == previous["assignment_digest"]
            assert row["deadline_ms"] == previous["deadline_ms"]
            assert len(row["retired"]) == attempt
            assert row["membership"]["epoch"] > previous["membership"]["epoch"]
            assert row["membership"]["session_id"] != previous["membership"]["session_id"]
            assert row["membership"]["peer_id"] != previous["membership"]["peer_id"]
            with app.app_context():
                task = service.tasks.get_by_id(task_id)
                current = task.worker_execution_context["meet_dialog"]
                assert {name: current[name] for name in original} == original
                assert task.assigned_agent_url == destination and task.status == "in_progress"
                assert len(task.worker_execution_context["meet_phase"]["retired_memberships"]) == attempt
                with pytest.raises(MeetError):
                    service.meet.inspect(*ids, previous["membership"]["session_id"])
            previous = row

    def exhaust(self, app, tasks, started, command, record_property):
        task_id = started[0]["task_id"]
        began = time.monotonic()
        assert command("disconnect") == {"interrupted": 0, "attempt": 3}
        while time.monotonic() < began + 4:
            with app.app_context():
                if tasks.get_by_id(task_id).status == "failed":
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("test_reconnect_exhausted_stop_missing")
        stopped_ms = (time.monotonic() - began) * 1000
        assert self.current(task_id)["attempt"] == 2
        assert self.dispatched == [row["task_id"] for row in started]
        record_property(
            "packaged_room_reconnect",
            {
                "synthetic_policy": True,
                "production_release_evidence": False,
                "same_original_task": True,
                "actual_rejoins": 2,
                "fresh_memberships": 2,
                "moving_screens_after_each": 2,
                "recovery_ms": self.elapsed,
                "exhausted_stop_ms": round(stopped_ms, 2),
                "new_dispatch": False,
            },
        )
