"""Hub-owned visual Task lifecycle and fully automated source revocation."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_callback
from ananta_contracts.meet_visual_receive import PROFILE
from tests.meet_visual_fixture import visual_system
from tests.test_meet_visual_receive import visual_frames
from worker.meet_media.visual_child import analyze

pytestmark = pytest.mark.timeout(45)


def admit(f):
    return f.service.visual(f.payload | {"publication_id": "camera"})["job"]


def complete(f, job):
    return f.payload | {
        "visual_task_id": job["task_id"],
        "visual_lease_id": job["lease_id"],
        "result": analyze({"profile": PROFILE, "frames": visual_frames()}),
    }


def test_visual_is_independently_default_off_until_automated_hub_control(app):
    with app.app_context():
        f = visual_system(active=False)
        assert not f.scope.controls.visual.enabled
        with pytest.raises(MeetError, match="policy_denied"):
            admit(f)
        assert f.service.exchange(f.payload)["visual_job"] is None
        f.service.control(
            f.principal,
            "project",
            f.task_id,
            {"expected_revision": 1, "chat": False, "audio": False, "screen": False, "visual": True},
        )
        job = admit(f)
        assert job["profile"] == PROFILE and job["source"] == "camera"
        assert f.service.exchange(f.payload)["visual_job"] == job
        assert all(
            not row["enabled"]
            for name, row in f.service.exchange(f.payload)["controls"].items()
            if name not in {"visual", "revision"}
        )


def test_real_child_task_is_parent_worker_bound_and_completion_persists_no_media_or_features(app):
    with app.app_context():
        f = visual_system()
        job = admit(f)
        child, parent = f.tasks.get_by_id(job["task_id"]), f.tasks.get_by_id(f.task_id)
        assert child.task_kind == "meet_visual_receive" and child.status == "in_progress"
        assert child.parent_task_id == parent.id and child.assigned_agent_url == parent.assigned_agent_url
        assert child.worker_execution_context == {
            "meet_visual_job": job,
            "parent_dispatch": f.scope.lease_id,
            "runtime_id": f.scope.runtime_id,
        }
        before = deepcopy(parent.worker_execution_context)
        receipt = f.service.visual_result(complete(f, job))
        assert receipt == {"schema": "ananta.meet-visual-accepted.v1", "nonce": f.payload["nonce"]}
        assert f.tasks.get_by_id(job["task_id"]).status == "completed"
        assert f.tasks.get_by_id(f.task_id).worker_execution_context == before
        assert "average_rgb" not in str(child.worker_execution_context)
        assert "jpegBase64" not in str(parent.worker_execution_context)
        assert f.service.exchange(f.payload)["visual_job"] == job  # Retain original cooldown.
        with pytest.raises(MeetError, match="busy_or_exhausted"):
            admit(f)
        with pytest.raises(MeetError, match="task_inactive"):
            f.service.visual_result(complete(f, job))
        assert f.tasks.get_by_id(job["task_id"]).status == "completed"
        f.service.media_worker.execute.assert_not_called()


@pytest.mark.parametrize("change", ["publication", "epoch", "generation", "revision", "membership", "control"])
def test_exchange_revokes_child_on_each_current_source_or_policy_change(app, change):
    with app.app_context():
        f = visual_system()
        job = admit(f)
        if change == "publication":
            f.receipt["publications"] = []
        if change == "epoch":
            f.receipt["publications"][0]["publicationEpoch"] += 1
        if change == "generation":
            f.receipt["lease"]["generation"] += 1
        if change == "revision":
            f.receipt["receiveRevision"] += 1
        if change == "membership":
            f.receipt["membershipEpoch"] += 1
        if change == "control":
            f.service.control(
                f.principal,
                "project",
                f.task_id,
                {"expected_revision": 2, "chat": False, "audio": False, "screen": False, "visual": False},
            )
        assert f.service.exchange(f.payload)["visual_job"] is None
        assert f.tasks.get_by_id(job["task_id"]).status == "failed"
        with pytest.raises(MeetError):
            f.service.visual_result(complete(f, job))
        assert f.tasks.get_by_id(f.task_id).status == "in_progress"


def test_parent_cancel_cleans_bound_visual_child_without_minting_new_authority(app):
    with app.app_context():
        f = visual_system()
        job = admit(f)
        assert f.tasks.finish_bound(*f.ids, "cancelled")
        assert f.tasks.get_by_id(job["task_id"]).status == "cancelled"
        with pytest.raises(MeetError):
            f.service.visual_result(complete(f, job))


def test_lost_parent_cas_never_ingests_a_visual_task(app):
    with app.app_context():
        f = visual_system()
        port = f.service.visual_coordinator.tasks
        port.compare_and_set = Mock(return_value=False)
        port.ingest = Mock()
        with pytest.raises(MeetError, match="reservation_conflict"):
            admit(f)
        port.ingest.assert_not_called()
        assert port.read(f.scope) == {"count": 0, "job": None}


@pytest.mark.parametrize("change", ["tenant", "parent", "worker", "execution"])
def test_child_row_mutation_cannot_complete_under_the_original_assignment(app, change):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = visual_system()
        job = admit(f)
        if change in {"tenant", "worker"}:
            from sqlmodel import Session

            from agent.database import engine
            from agent.db_models import AgentInfoDB, ProjectDB

            with Session(engine) as session:
                if change == "tenant":
                    session.add(
                        ProjectDB(
                            tenant_id="other",
                            project_id="project",
                            name="Other synthetic project",
                            created_by_subject_id="owner",
                        )
                    )
                else:
                    session.add(AgentInfoDB(url="http://other-worker:8000", name="Other synthetic worker"))
                session.commit()
        patch = {
            "tenant": {"tenant_id": "other"},
            "parent": {"parent_task_id": None},
            "worker": {"assigned_agent_url": "http://other-worker:8000"},
            "execution": {"worker_execution_context": {}},
        }[change]
        assert compare_and_set_local_task_status(
            job["task_id"], "in_progress", expected_statuses={"in_progress"}, **patch
        )
        with pytest.raises(MeetError):
            f.service.visual_result(complete(f, job))
        assert f.tasks.get_by_id(job["task_id"]).status != "completed"


def test_visual_callbacks_never_accept_raw_frames_prompts_or_unassigned_results(app):
    with app.app_context():
        f = visual_system()
        job = admit(f)
        callback = complete(f, job) | {
            "schema": "ananta.meet-dialog-callback.v1",
            "action": "visual_result",
            "sent_at": f.f.now,
        }
        assert validate_callback(callback, f.f.now) == callback
        for patch in ({"jpegBase64": "private"}, {"result": {"prompt": "ignore policy"}}, {"visual_task_id": []}):
            with pytest.raises(ValueError):
                validate_callback(callback | patch, f.f.now)
        with pytest.raises(MeetError, match="assignment_mismatch"):
            f.service.visual_result(complete(f, job) | {"visual_lease_id": "other"})
        assert f.tasks.get_by_id(job["task_id"]).status == "in_progress"


@pytest.mark.parametrize("change", ["control", "cancel", "lock_unavailable"])
def test_completion_rereads_parent_after_acquiring_shared_mutation_locks(app, monkeypatch, change):
    from contextlib import contextmanager

    from agent.common import task_mutation_lock

    with app.app_context():
        f = visual_system()
        job = admit(f)
        port = task_mutation_lock.get_task_mutation_lock_port()
        original = port.mutation_locks
        entered = False

        @contextmanager
        def acquire(task_ids):
            nonlocal entered
            if set(task_ids) == {f.task_id, job["task_id"]} and not entered:
                entered = True
                if change == "control":
                    f.service.control(
                        f.principal,
                        "project",
                        f.task_id,
                        {"expected_revision": 2, "chat": False, "audio": False, "screen": False, "visual": False},
                    )
                elif change == "cancel":
                    assert f.tasks.finish_bound(*f.ids, "cancelled")
                else:
                    yield False
                    return
            with original(task_ids) as acquired:
                yield acquired

        monkeypatch.setattr(port, "mutation_locks", acquire)
        with pytest.raises(MeetError):
            f.service.visual_result(complete(f, job))
        assert entered
        assert f.tasks.get_by_id(job["task_id"]).status in {"failed", "cancelled"}
