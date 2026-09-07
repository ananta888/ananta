"""Real scoped Hub tasks/catalog and exact temporary files; synthetic clip input."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import select, update
from sqlmodel import Session

from agent.database import engine
from agent.db_models import ProjectMembershipDB
from agent.repositories.persona_retention import SqlPersonaRetention
from agent.repositories.persona_video_retention import create_video_retention_store, events
from agent.services.persona_retention_runner import PersonaRetentionRunner
from agent.services.persona_retention_service import PersonaRetentionService
from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
from agent.services.persona_video_erasure import create_video_erasure_service
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_video_asset_service import admit
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_routes import video_api as video_api
from tests.test_persona_video_tasks import video_task as video_task

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def video_retired(request, app):
    fixture = request.getfixturevalue("video_assets")
    case = fixture.case
    case.policy.access = app.extensions["project_access_authority"]
    asset = admit(fixture)
    fixture.service.revoke(case.principal, "project", asset.video.artifact_id, expected_revision=2)
    store = create_video_retention_store(case.base.engine)
    store.initialize()
    now = [1000.0]
    tasks = HubPersonaRetentionTasks(kind="video", clock=lambda: now[0])
    erasure = create_video_erasure_service(
        policy=case.policy, catalog=fixture.catalog, base_dir=fixture.storage.store.base_dir
    )
    admin = PersonaRetentionService(policy=case.policy, catalog=fixture.catalog, store=store, clock=lambda: now[0])
    runner = PersonaRetentionRunner(
        policy=case.policy,
        catalog=fixture.catalog,
        store=store,
        erasure=erasure,
        tasks=tasks,
        clock=lambda: now[0],
    )
    admin.schedule(
        case.principal,
        "project",
        asset.video.artifact_id,
        asset_revision=3,
        expected_revision=0,
        delete_after_seconds=60,
    )
    return SimpleNamespace(
        fixture=fixture,
        case=case,
        asset=asset,
        store=store,
        tasks=tasks,
        erasure=erasure,
        admin=admin,
        runner=runner,
        now=now,
    )


def files(case):
    root = case.fixture.storage.store.base_dir
    return (
        root / case.asset.video.artifact_id / "v0001__clip.mp4",
        root / case.asset.preview.artifact_id / "v0001__preview.png",
    )


def due(case):
    case.now[0] = 1060.0
    return case.store.due(1_060_000, limit=5)[0]


def test_due_video_retention_runs_as_exact_hub_task_and_preserves_audit(video_retired):
    case = video_retired
    assert case.runner.run_once()["claimed"] == 0
    due(case)
    assert case.runner.run_once()["completed"] == 1
    assert not any(path.exists() for path in files(case))
    row = case.store.get(dict(tenant_id="tenant", project_id="project", artifact_id=case.asset.video.artifact_id))
    from agent.services.repository_registry import get_repository_registry

    task = get_repository_registry().task_repo.get_by_id(row["task_id"])
    assert task.status == "completed" and task.task_kind == "persona_video_retention"
    assert set(task.worker_execution_context) == {"persona_video_retention"}
    assert task.required_capabilities == ["hub_persona_video_retention"]
    assert case.runner.run_once()["claimed"] == 0
    with case.store.engine.connect() as connection:
        assert list(connection.execute(select(events.c.state)).scalars()) == ["scheduled", "running", "completed"]


def test_image_ledger_and_task_adapter_cannot_consume_video_grants(video_retired):
    case = video_retired
    image_store = SqlPersonaRetention(case.store.engine)
    image_store.initialize()
    observed = due(case)
    assert image_store.due(1_060_000, limit=5) == ()
    with pytest.raises(ValueError, match="unavailable"):
        image_store.get(observed)
    assert image_store.claim(observed, 1_060_000) is None
    record = case.store.claim(observed, 1_060_000)
    case.tasks.start(record)
    wrong = HubPersonaRetentionTasks(clock=lambda: case.now[0])
    try:
        with pytest.raises(PermissionError):
            wrong.require(record)
        assert not wrong.finish(record, "completed")
        case.tasks.require(record)
    finally:
        assert case.tasks.finish(record, "failed")


def test_video_grant_has_single_cross_hub_claim_and_cancel_invalidates_it(video_retired):
    case = video_retired
    observed = due(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: case.store.claim(observed, 1_060_000), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    record = next(claim for claim in claims if claim is not None)
    assert case.admin.cancel(case.case.principal, "project", case.asset.video.artifact_id, expected_revision=1) == 2
    with pytest.raises(PermissionError):
        case.store.require_claim(record, 1_060_000)
    assert not case.store.finish(record, "completed", 1_060_000)
    assert all(path.exists() for path in files(case))


def test_revoked_real_project_membership_blocks_video_erasure(video_retired):
    case = video_retired
    with Session(engine) as session:
        session.exec(
            update(ProjectMembershipDB)
            .where(
                ProjectMembershipDB.tenant_id == "tenant",
                ProjectMembershipDB.project_id == "project",
                ProjectMembershipDB.subject_id == "actor",
            )
            .values(state="revoked")
        )
        session.commit()
    due(case)
    assert case.runner.run_once()["blocked"] == 1
    assert all(path.exists() for path in files(case))


def test_video_interrupted_second_part_resumes_without_reusing_old_task(video_retired):
    case = video_retired
    original = case.erasure.eraser
    calls = 0

    def erase(reference, size, *, checkpoint):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic interrupted preview deletion")
        original.erase(reference, size, checkpoint=checkpoint)

    case.erasure.eraser = Mock(erase=erase)
    due(case)
    assert case.runner.run_once()["retrying"] == 1
    assert not files(case)[0].exists() and files(case)[1].exists()
    case.erasure.eraser = original
    case.now[0] += 61
    assert case.runner.run_once()["completed"] == 1
    assert not any(path.exists() for path in files(case))


def test_expired_video_attempt_cannot_finish_after_fresh_claim(video_retired):
    case = video_retired
    first = case.store.claim(due(case), 1_060_000)
    case.tasks.start(first)
    case.now[0] += 61
    assert case.runner.run_once()["completed"] == 1
    with pytest.raises(PermissionError):
        case.tasks.require(first)
    assert not case.tasks.finish(first, "completed")
    assert not case.store.finish(first, "completed", int(case.now[0] * 1000))


def test_video_retention_http_is_headless_scoped_and_cancellable(request):
    http, app = request.getfixturevalue("video_api")
    case = request.getfixturevalue("video_retired")
    app.extensions["persona_video_retention"] = case.admin
    path = "/api/persona-media/v1/projects/project/videos/" + case.asset.video.artifact_id + "/retention"
    assert http.get(path).status_code == 401
    assert http.get(path, headers=HEADERS).json["state"] == "scheduled"
    body = {"asset_revision": 3, "expected_revision": 1, "delete_after_seconds": 120}
    assert http.put(path, headers=HEADERS, json=body).json["revision"] == 2
    assert http.put(path, headers=HEADERS, json=body | {"tenant_id": "other"}).status_code == 409
    assert http.put(path, headers=HEADERS, json=body | {"asset_revision": True}).status_code == 409
    assert http.delete(path, headers=HEADERS, json={"expected_revision": 2}).json["state"] == "cancelled"
    assert all(path.exists() for path in files(case))


def test_video_reconciler_has_independent_opt_in_and_lifecycle_stop(monkeypatch):
    from agent.services.background.persona_retention import (
        EXTENSION,
        VIDEO_EXTENSION,
        start_persona_retention,
        stop_persona_retention,
    )

    monkeypatch.setattr("agent.common.context.active_threads", [])
    monkeypatch.setenv("ANANTA_PERSONA_RETENTION_ENABLED", "1")
    monkeypatch.delenv("ANANTA_PERSONA_VIDEO_RETENTION_ENABLED", raising=False)
    app = Flask("video-retention-lifecycle")
    app.config["ROLE"] = "hub"
    called = Event()
    app.extensions["persona_video_retention_runner"] = SimpleNamespace(run_once=lambda **_: called.set())
    start_persona_retention(app)
    assert VIDEO_EXTENSION not in app.extensions
    monkeypatch.setenv("ANANTA_PERSONA_VIDEO_RETENTION_ENABLED", "1")
    app.config["ROLE"] = "worker"
    start_persona_retention(app)
    assert VIDEO_EXTENSION not in app.extensions
    app.config["ROLE"] = "hub"
    start_persona_retention(app)
    thread = app.extensions[VIDEO_EXTENSION]["thread"]
    try:
        assert called.wait(2)
        assert EXTENSION not in app.extensions
        start_persona_retention(app)
        assert app.extensions[VIDEO_EXTENSION]["thread"] is thread
    finally:
        stop_persona_retention(app)
        thread.join(2)
    assert not thread.is_alive()
