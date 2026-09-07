"""The admission layer fences actual Hub tasks, not independent worker jobs."""

import time
import uuid
from unittest.mock import Mock

import pytest

from agent.services.meet_capacity_admission import MeetCapacityAdmission
from agent.services.meet_contract import MeetError
from agent.services.meet_turn_service import HubMediaTasks
from agent.services.worker_pool_scheduler_service import WorkerPoolSchedulerService
from tests.test_meet_capacity import repository as repository
from tests.test_meet_media import PRINCIPAL, service, turn


def test_actual_hub_task_and_capacity_lease_complete_without_extra_task(request, app):
    capacity_store = request.getfixturevalue("repository")
    runtime, _binding, worker, _tasks = service()
    runtime.tasks = tasks = HubMediaTasks()
    runtime.capacity = MeetCapacityAdmission(capacity_store, tasks)
    with app.app_context():
        response = runtime.execute(PRINCIPAL, "project", {"text": "synthetic"})
        worker.execute.assert_called_once()
        envelope = worker.execute.call_args.args[0]
        assert response["task_id"] == envelope["task_id"]
        with pytest.raises(MeetError, match="task_changed"):
            tasks.require_current(envelope)


def test_task_state_or_binding_changes_cannot_acquire_capacity(app):
    envelope = turn() | {"task_id": str(uuid.uuid4())}
    tasks = HubMediaTasks()
    with app.app_context():
        tasks.start(envelope, "actor")
        tasks.require_current(envelope)
        for field, value in [
            ("tenant_id", "other"),
            ("project_id", "other"),
            ("lease_id", "other"),
            ("deadline", envelope["deadline"] + 1),
            ("speech_profile", {}),
        ]:
            with pytest.raises(MeetError, match="task_changed"):
                tasks.require_current(envelope | {field: value})
        assert tasks.finish(envelope, "failed")
        with pytest.raises(MeetError, match="task_changed"):
            tasks.require_current(envelope)


def test_generic_worker_scheduler_cannot_release_or_promote_media_capacity(monkeypatch):
    from agent.db_models import WorkerSlotLeaseDB

    lease_repo = Mock()
    lease = WorkerSlotLeaseDB(lease_type="meet_media", status="queued", deadline_at=time.time() - 1)
    lease_repo.get_by_id.return_value = lease
    lease_repo.list_expired.return_value = [lease]
    monkeypatch.setattr("agent.services.worker_pool_scheduler_service.worker_slot_lease_repo", lease_repo)
    scheduler = WorkerPoolSchedulerService()
    scheduler.release_for_job(lease.id)
    assert scheduler.cleanup_stale_leases() == 0
    decision = scheduler.revalidate_queued_job(
        slot_lease_id=lease.id, policy_decision_ref=None, policy_decision_hash=None
    )
    assert decision.reason_code == "slot_lease_not_owned"
    lease_repo.release.assert_not_called()
    lease_repo.save.assert_not_called()


def test_global_worker_slot_projection_does_not_disclose_media_task_bindings():
    from agent.db_models import WorkerSlotLeaseDB
    from agent.routes.worker_pool import _public_leases

    ordinary = WorkerSlotLeaseDB(id="ordinary", lease_type="worker")
    private = WorkerSlotLeaseDB(
        lease_type="meet_media",
        lease_metadata={"tenant_id": "private-marker", "lease_id": "private-marker"},
    )
    projected = _public_leases([ordinary, private])
    assert [item["id"] for item in projected] == ["ordinary"]
    assert "private-marker" not in repr(projected)


@pytest.mark.parametrize("video_enabled", [False, True])
def test_enabled_bootstrap_installs_capacity_without_enabling_machine_trust(
    request, tmp_path, monkeypatch, video_enabled
):
    from flask import Flask

    from agent.bootstrap.meet import configure_meet_media

    capacity_store = request.getfixturevalue("repository")
    key = tmp_path / "synthetic-key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    monkeypatch.setenv("ANANTA_MEET_MEDIA_ENABLED", "1")
    monkeypatch.setenv("ANANTA_MEET_MEDIA_ALLOWED_SCOPES", '[["synthetic", "synthetic"]]')
    monkeypatch.setenv("ANANTA_MEET_MEDIA_KEY_FILE", str(key))
    monkeypatch.setenv("ANANTA_MEET_MEDIA_WORKER_URL", "http://synthetic:8094/v1/turns")
    monkeypatch.setenv("ANANTA_MEET_MEDIA_CAPACITY_POOL", "synthetic-media")
    monkeypatch.setenv("ANANTA_MEET_MACHINE_ENABLED", "0")
    monkeypatch.setattr("agent.database.engine", capacity_store.engine)
    app = Flask(__name__)
    app.extensions["meet_binding_service"] = Mock()
    assets = Mock()
    if video_enabled:
        app.extensions["persona_video_assets"] = assets
    configure_meet_media(app)
    assert app.extensions["meet_turn_service"].capacity is app.extensions["meet_media_capacity"]
    assert app.extensions["meet_turn_service"].grant_issuer is None
    videos = app.extensions["meet_turn_service"].persona_videos
    if video_enabled:
        assert videos.assets is assets
    else:
        assert videos is None
