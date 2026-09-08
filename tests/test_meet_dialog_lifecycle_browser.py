"""Private real transport stops from parent/organization loss, not a stop API."""

import os
import time

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.services.meet_dialog_lifecycle import organization_tuple
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.meet_lifecycle_revocation import revoke_fixture_lifecycle


class LifecycleScenario:
    parent_id = "meet-test-parent"

    def __init__(self, reason):
        if reason not in {"parent-cancel", "organization-pause", "role-draining", "assignment-suspended"}:
            raise ValueError("test_lifecycle_scenario_invalid")
        self.reason = reason
        self.observation = None

    def prepare(self, engine, *, publisher):
        seed_parent(engine, tenant="synthetic", project="synthetic", create_project=False, publisher=publisher)

    def revoke(self, engine, service, started, completed):
        child = service.tasks.get_by_id(started["task_id"])
        assert child.status == "in_progress"
        assert organization_tuple(child) == organization_tuple(service.tasks.get_by_id(self.parent_id))
        with Session(engine) as session:
            media_children = session.exec(
                select(TaskDB).where(TaskDB.parent_task_id == child.id, TaskDB.task_kind == "meet_media_turn")
            ).all()
            assert len(media_children) == 2
            assert all(
                row.status == "completed" and organization_tuple(row) == organization_tuple(child)
                for row in media_children
            )
        attempts = revoke_fixture_lifecycle(engine, self.reason)
        revoked_at = time.monotonic()
        assert completed.wait(8), "Worker failed to stop after authoritative lifecycle loss"
        assert service.tasks.get_by_id(child.id).status == "failed"
        self.observation = {
            "synthetic": True,
            "production_release_evidence": False,
            "cause": self.reason,
            "fixture_revocation_write_attempts": attempts,
            "worker_stop_ms": round((time.monotonic() - revoked_at) * 1000, 2),
            "dialog_stop_api_used": False,
            "organization_tuple_inherited": True,
            "completed_media_children_with_scope": len(media_children),
        }


@pytest.mark.timeout(240)
@pytest.mark.parametrize("reason", ["parent-cancel", "organization-pause", "role-draining", "assignment-suspended"])
@pytest.mark.skipif(os.environ.get("MEET_DIALOG_LIFECYCLE_GATE") != "1", reason="opt-in private lifecycle transport")
def test_parent_or_organization_loss_stops_real_dialog_and_removes_machine(
    app, tmp_path, monkeypatch, record_property, reason
):
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "lifecycle regression has a fixed short budget"
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    scenario = LifecycleScenario(reason)
    run_gate(app, tmp_path, monkeypatch, False, False, None, False, False, record_property, lifecycle_scenario=scenario)
    assert scenario.observation is not None
    record_property("synthetic_dialog_parent_lifecycle", scenario.observation)
