"""Headless exact-file retirement with real Hub task/CAS and test-only voice receipts."""

from threading import Event
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import select

from agent.repositories.persona_retention import SqlPersonaRetention
from agent.repositories.persona_video_retention import create_video_retention_store
from agent.repositories.persona_voice_retention import create_voice_retention_store, events
from agent.services.persona_retention_runner import PersonaRetentionRunner
from agent.services.persona_retention_service import PersonaRetentionService
from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
from agent.services.persona_voice_erasure import create_voice_erasure_service
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_voice_asset_service import admit
from tests.test_persona_voice_asset_service import voice_assets as voice_assets
from tests.test_persona_voice_tasks import voice_task as voice_task

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def voice_retired(request, app):
    fixture = request.getfixturevalue("voice_assets")
    case = fixture.case
    case.policy.access = app.extensions["project_access_authority"]
    asset = admit(fixture)
    fixture.service.revoke(case.principal, "project", asset.voice.artifact_id, expected_revision=2)
    store = create_voice_retention_store(case.base.engine)
    store.initialize()
    now = [1000.0]
    tasks = HubPersonaRetentionTasks(kind="voice", clock=lambda: now[0])
    erasure = create_voice_erasure_service(
        policy=case.policy, catalog=fixture.catalog, base_dir=fixture.storage.store.base_dir
    )
    admin = PersonaRetentionService(policy=case.policy, catalog=fixture.catalog, store=store, clock=lambda: now[0])
    runner = PersonaRetentionRunner(
        policy=case.policy, catalog=fixture.catalog, store=store, erasure=erasure, tasks=tasks, clock=lambda: now[0]
    )
    admin.schedule(
        case.principal,
        "project",
        asset.voice.artifact_id,
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


def voice_path(case):
    return case.fixture.storage.store.base_dir / case.asset.voice.artifact_id / "v0001__voice.json"


def due(case):
    case.now[0] = 1060.0
    return case.store.due(1_060_000, limit=5)[0]


def test_voice_retention_runs_as_hub_task_and_preserves_audited_tombstone(voice_retired, tmp_path):
    case = voice_retired
    unrelated = tmp_path / "model.onnx"
    unrelated.touch()
    assert case.runner.run_once()["claimed"] == 0
    due(case)
    assert case.runner.run_once()["completed"] == 1
    assert not voice_path(case).exists() and unrelated.exists()
    row = case.store.get(dict(tenant_id="tenant", project_id="project", artifact_id=case.asset.voice.artifact_id))
    from agent.services.repository_registry import get_repository_registry

    task = get_repository_registry().task_repo.get_by_id(row["task_id"])
    assert task.status == "completed" and task.task_kind == "persona_voice_retention"
    assert set(task.worker_execution_context) == {"persona_voice_retention"}
    assert task.required_capabilities == ["hub_persona_voice_retention"]
    assert case.runner.run_once()["claimed"] == 0
    assert case.erasure.status(case.case.principal, "project", case.asset.voice.artifact_id) == {
        "revision": 5,
        "state": "purged",
    }
    with case.store.engine.connect() as connection:
        assert list(connection.execute(select(events.c.state)).scalars()) == ["scheduled", "running", "completed"]


def test_image_and_video_ledgers_and_tasks_cannot_consume_voice_claim(voice_retired):
    case = voice_retired
    observed = due(case)
    for other in (SqlPersonaRetention(case.store.engine), create_video_retention_store(case.store.engine)):
        other.initialize()  # Also verifies distinct SQL index names in one database.
        assert other.due(1_060_000, limit=5) == ()
        assert other.claim(observed, 1_060_000) is None
    record = case.store.claim(observed, 1_060_000)
    case.tasks.start(record)
    try:
        for kind in ("image", "video"):
            wrong = HubPersonaRetentionTasks(kind=kind, clock=lambda: case.now[0])
            with pytest.raises(PermissionError):
                wrong.require(record)
            assert not wrong.finish(record, "completed")
        case.tasks.require(record)
    finally:
        assert case.tasks.finish(record, "failed")


@pytest.mark.parametrize("change", ["bytes", "symlink", "hardlink", "parent_symlink"])
def test_changed_descriptor_or_links_are_never_erased(voice_retired, tmp_path, change):
    case = voice_retired
    path, elsewhere = voice_path(case), tmp_path / "unrelated"
    if change == "bytes":
        path.write_bytes(b"changed metadata")
    elif change == "symlink":
        path.replace(elsewhere)
        path.symlink_to(elsewhere)
    elif change == "hardlink":
        elsewhere.hardlink_to(path)
    else:
        path.parent.replace(elsewhere)
        path.parent.symlink_to(elsewhere, target_is_directory=True)
    due(case)
    assert case.runner.run_once()["blocked"] == 1
    assert path.exists()
    assert case.erasure.status(case.case.principal, "project", case.asset.voice.artifact_id)["state"] == "purging"


def test_cancelled_grant_prevents_old_claim_from_erasing_file(voice_retired):
    case = voice_retired
    record = case.store.claim(due(case), 1_060_000)
    assert case.admin.cancel(case.case.principal, "project", case.asset.voice.artifact_id, expected_revision=1) == 2
    with pytest.raises(PermissionError):
        case.store.require_claim(record, 1_060_000)
    assert not case.store.finish(record, "completed", 1_060_000)
    assert voice_path(case).is_file()


def test_voice_background_tick_has_separate_opt_in_and_bounded_stop(monkeypatch):
    from agent.services.background.persona_retention import (
        VOICE_EXTENSION,
        start_persona_retention,
        stop_persona_retention,
    )

    monkeypatch.setattr("agent.common.context.active_threads", [])
    monkeypatch.delenv("ANANTA_PERSONA_VOICE_RETENTION_ENABLED", raising=False)
    app = Flask("voice-retention-lifecycle")
    app.config["ROLE"] = "hub"
    called = Event()
    app.extensions["persona_voice_retention_runner"] = SimpleNamespace(run_once=lambda **_: called.set())
    start_persona_retention(app)
    assert VOICE_EXTENSION not in app.extensions
    monkeypatch.setenv("ANANTA_PERSONA_VOICE_RETENTION_ENABLED", "1")
    app.config["ROLE"] = "worker"
    start_persona_retention(app)
    assert VOICE_EXTENSION not in app.extensions
    app.config["ROLE"] = "hub"
    start_persona_retention(app)
    thread = app.extensions[VOICE_EXTENSION]["thread"]
    try:
        assert called.wait(2)
        start_persona_retention(app)
        assert app.extensions[VOICE_EXTENSION]["thread"] is thread
    finally:
        stop_persona_retention(app)
        thread.join(2)
    assert not thread.is_alive()
