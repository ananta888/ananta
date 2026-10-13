"""Project-scoped, content-free quarantine inventory; real SQL, synthetic auth."""

import pytest
from flask import Flask, g
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import MemoryEntryDB, MemoryTreeChunkDB, TaskDB
from agent.db_models.projects import ProjectDB, ProjectMembershipDB
from agent.repositories.memory_quarantine_read import SQLMemoryQuarantineReadRepository
from agent.routes.memory_audit import memory_audit_bp
from agent.services.memory_audit_read_service import MemoryAuditReadService
from agent.services.project_access_authority import ProjectAccessError, SqlProjectAccessAuthority
from agent.services.source_control_access_policy import HubSourcePrincipal

ACTOR = "operator"


@pytest.fixture
def operator(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'quarantine.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for tenant, project in [("tenant-a", "project-a"), ("tenant-a", "project-b"), ("tenant-b", "project-a")]:
            session.add(ProjectDB(tenant_id=tenant, project_id=project, name="Synthetic project",
                                  created_by_subject_id=ACTOR))
        session.add(ProjectMembershipDB(tenant_id="tenant-a", project_id="project-a",
                                        subject_id=ACTOR, role="owner"))
    with Session(engine) as session, session.begin():
        for task_id, tenant, project in [("task-a", "tenant-a", "project-a"), ("task-b", "tenant-a", "project-b")]:
            session.add(TaskDB(id=task_id, title="t", tenant_id=tenant, project_id=project))
    with Session(engine) as session, session.begin():
        session.add(MemoryEntryDB(id="entry-a", task_id="task-a", content="private canary",
                                  memory_metadata={"memory_quarantine": {
                                      "schema": "ananta.memory-quarantine.v1", "state": "quarantined",
                                      "reason_code": "suspected_injection", "policy_digest": "a" * 64,
                                      "decision_audit_ref": "audit-1"}}))
        session.add(MemoryEntryDB(id="entry-active", task_id="task-a", content="kept",
                                  memory_metadata={"memory_quarantine": {
                                      "schema": "ananta.memory-quarantine.v1", "state": "active"}}))
        session.add(MemoryEntryDB(id="entry-foreign", task_id="task-b", content="foreign canary",
                                  memory_metadata={"memory_quarantine": {
                                      "schema": "ananta.memory-quarantine.v1", "state": "revoked"}}))
        session.add(MemoryTreeChunkDB(id="chunk-a", source_id="s", content="tree canary", lifecycle="quarantined",
                                      chunk_metadata={"memory_namespace": {"tenant_id": "tenant-a",
                                                                           "workspace_id": "project-a"},
                                                      "memory_quarantine": {"reason_code": "review",
                                                                            "policy_digest": "b" * 64}}))
        session.add(MemoryTreeChunkDB(id="chunk-foreign", source_id="s", content="foreign tree canary",
                                      lifecycle="revoked",
                                      chunk_metadata={"memory_namespace": {"tenant_id": "tenant-a",
                                                                           "workspace_id": "project-b"}}))
    import agent.database as db_module

    monkeypatch.setattr(db_module, "engine", engine)

    service = MemoryAuditReadService(access=SqlProjectAccessAuthority(session_factory=lambda: Session(engine)),
                                     repository=None, quarantine=SQLMemoryQuarantineReadRepository())
    principal = HubSourcePrincipal(ACTOR, "tenant-a", None, frozenset())
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["memory_audit_read_service"] = service
    app.register_blueprint(memory_audit_bp)

    def authenticate(token, *, require_admin):
        if token != "synthetic-operator-token":
            return False, "missing_token"
        g.user = {"sub": principal.subject_id, "tenant_id": principal.tenant_id}
        g.is_admin = False
        return True, "synthetic_fixture"

    monkeypatch.setattr("agent.auth._authenticate_request", authenticate)
    yield service, principal, app
    engine.dispose()


def inventory(service, principal, **kwargs):
    return service.quarantine_state(principal=principal, project_id="project-a", **kwargs)


def test_inventory_is_scoped_and_content_free(operator):
    service, principal, _app = operator
    page = inventory(service, principal)
    assert page["schema"] == "ananta.memory-quarantine-page.v1" and page["project_id"] == "project-a"
    by_id = {item["id"]: item for item in page["items"]}
    assert set(by_id) == {"entry-a", "chunk-a"}
    assert by_id["entry-a"] == {
        "kind": "result_memory_entry", "id": "entry-a", "channel_id": "hub-result-memory",
        "state": "quarantined", "reason_code": "suspected_injection", "policy_digest": "a" * 64,
        "decision_audit_ref": "audit-1", "task_id": "task-a", "created_at": by_id["entry-a"]["created_at"]}
    assert by_id["chunk-a"]["kind"] == "memory_tree_chunk" and by_id["chunk-a"]["channel_id"] == "hub-memory-tree"
    assert by_id["chunk-a"]["state"] == "quarantined" and by_id["chunk-a"]["reason_code"] == "review"
    text = str(page)
    for secret in ("canary", "private", "foreign"):
        assert secret not in text


def test_active_and_foreign_rows_are_never_projected(operator):
    service, principal, _app = operator
    ids = {item["id"] for item in inventory(service, principal)["items"]}
    assert "entry-active" not in ids and "entry-foreign" not in ids and "chunk-foreign" not in ids
    with pytest.raises(ProjectAccessError):
        service.quarantine_state(principal=HubSourcePrincipal(ACTOR, "tenant-a", None, frozenset()),
                                 project_id="project-b")


@pytest.mark.parametrize("limit", [0, 501, "x", None])
def test_invalid_limit_is_rejected(operator, limit):
    service, principal, _app = operator
    with pytest.raises((ValueError, TypeError)):
        inventory(service, principal, limit=limit)


def test_route_returns_inventory_for_hub_operator(operator):
    _service, _principal, app = operator
    response = app.test_client().get("/api/projects/project-a/memory-audit/quarantine",
                                     headers={"Authorization": "Bearer synthetic-operator-token"})
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert {item["id"] for item in payload["items"]} == {"entry-a", "chunk-a"}
    assert payload["production_release_evidence"] is False


def test_route_rejects_bad_query(operator):
    _service, _principal, app = operator
    client = app.test_client()
    assert client.get("/api/projects/project-a/memory-audit/quarantine?limit=abc",
                      headers={"Authorization": "Bearer synthetic-operator-token"}).status_code == 422
    assert client.get("/api/projects/project-a/memory-audit/quarantine?extra=1",
                      headers={"Authorization": "Bearer synthetic-operator-token"}).status_code == 422
    assert client.get("/api/projects/project-a/memory-audit/quarantine").status_code in (401, 403)
