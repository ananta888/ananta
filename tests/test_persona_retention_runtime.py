"""Headless HTTP, real Hub task/membership and lifecycle integration."""

from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlalchemy import update
from sqlmodel import Session

from agent.database import engine
from agent.db_models import ProjectMembershipDB
from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.persona_retention_tasks import HubPersonaRetentionTasks
from agent.services.project_access_authority import SqlProjectAccessAuthority
from agent.services.source_control_access_policy import HubSourcePrincipal
from tests.test_persona_asset_erasure import paths
from tests.test_persona_asset_erasure import retired as retired
from tests.test_persona_assets import setup as setup
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media_routes import BASE, HEADERS
from tests.test_persona_media_routes import client as client
from tests.test_persona_retention import due, schedule
from tests.test_persona_retention import scheduled as scheduled

pytestmark = pytest.mark.timeout(45)


def test_retention_http_inherits_auth_scope_limits_and_no_store(request):
    http, app = request.getfixturevalue("client")
    case = request.getfixturevalue("scheduled")
    app.extensions["persona_retention"] = case.admin
    path = BASE + "/" + case.asset.image.artifact_id + "/retention"
    assert http.get(path).status_code == 401
    assert http.get(path, headers=HEADERS).json["state"] == "scheduled"
    body = {"asset_revision": 3, "expected_revision": 1, "delete_after_seconds": 120}
    response = http.put(path, json=body, headers=HEADERS)
    assert response.status_code == 200 and response.json["revision"] == 2
    assert response.headers["Cache-Control"] == "no-store"
    assert http.put(path, json=body | {"tenant_id": "other"}, headers=HEADERS).status_code == 409
    assert http.put(path, json=body | {"expected_revision": True}, headers=HEADERS).status_code == 409
    assert http.get(path + "?extra=true", headers=HEADERS).status_code == 400
    assert http.delete(path, json={"expected_revision": 2}, headers=HEADERS).json["state"] == "cancelled"
    app.config["ROLE"] = "worker"
    assert http.get(path, headers=HEADERS).status_code == 403


def test_real_membership_and_hub_task_fence_retention_execution(request):
    request.getfixturevalue("runtime")  # Real project and actor membership in the Hub task store.
    case = request.getfixturevalue("scheduled")
    case.principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))
    policy = PersonaAssetPolicyService(
        access=SqlProjectAccessAuthority(), policies=Mock(), sources=Mock(), inspection_receipts=Mock()
    )
    case.admin.policy = case.runner.policy = case.erasure.policy = policy
    schedule(case, expected_revision=1)
    case.runner.tasks = HubPersonaRetentionTasks(clock=lambda: case.now[0])
    due(case)
    assert case.runner.run_once()["completed"] == 1
    from agent.services.repository_registry import get_repository_registry

    row = case.store.get(dict(tenant_id="tenant", project_id="project", artifact_id=case.asset.image.artifact_id))
    task = get_repository_registry().task_repo.get_by_id(row["task_id"])
    assert task.status == "completed" and task.task_kind == "persona_image_retention"
    assert task.tenant_id == "tenant" and task.project_id == "project"
    assert not all(path.exists() for path in paths(case.service, case.asset))


def test_actual_project_revocation_never_reuses_the_scheduling_authority(request):
    request.getfixturevalue("runtime")
    case = request.getfixturevalue("scheduled")
    case.principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))
    policy = PersonaAssetPolicyService(
        access=SqlProjectAccessAuthority(), policies=Mock(), sources=Mock(), inspection_receipts=Mock()
    )
    case.admin.policy = case.runner.policy = case.erasure.policy = policy
    schedule(case, expected_revision=1)
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
    assert all(path.exists() for path in paths(case.service, case.asset))


def test_background_is_opt_in_hub_only_and_stops_automatically(monkeypatch):
    import agent.common.context
    from agent.services.background.persona_retention import EXTENSION, start_persona_retention, stop_persona_retention

    monkeypatch.setattr(agent.common.context, "active_threads", [])
    monkeypatch.delenv("ANANTA_PERSONA_RETENTION_ENABLED", raising=False)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    called = Event()
    app.extensions["persona_retention_runner"] = SimpleNamespace(run_once=lambda **_: called.set())
    start_persona_retention(app)
    assert EXTENSION not in app.extensions
    monkeypatch.setenv("ANANTA_PERSONA_RETENTION_ENABLED", "1")
    app.config["ROLE"] = "worker"
    start_persona_retention(app)
    assert EXTENSION not in app.extensions
    app.config["ROLE"] = "hub"
    start_persona_retention(app)
    thread = app.extensions[EXTENSION]["thread"]
    try:
        assert called.wait(2)
        start_persona_retention(app)
        assert app.extensions[EXTENSION]["thread"] is thread
    finally:
        stop_persona_retention(app)
        thread.join(2)
    assert not thread.is_alive()
