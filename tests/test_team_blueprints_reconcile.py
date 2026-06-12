import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TaskDB, TeamBlueprintDB, TeamDB, TeamMemberDB
from agent.repository import audit_repo
from tests_support import admin_login_token as _login_admin



# Split from tests/test_team_blueprints.py to keep source files below 1000 lines.

def test_delete_blueprint_blocks_when_team_references_it(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    create_response = client.post(
        "/teams/blueprints",
        json={
            "name": "Delete Guard Blueprint",
            "description": "should be protected while in use",
            "roles": [
                {
                    "name": "Engineer",
                    "description": "builds features",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {},
                }
            ],
            "artifacts": [],
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201
    blueprint = create_response.json["data"]

    instantiate_response = client.post(
        f"/teams/blueprints/{blueprint['id']}/instantiate",
        json={"name": "Delete Guard Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]

    delete_response = client.delete(f"/teams/blueprints/{blueprint['id']}", headers=auth_header)

    assert delete_response.status_code == 409
    assert delete_response.json["message"] == "blueprint_in_use"
    data = delete_response.json["data"]
    assert data["blueprint_id"] == blueprint["id"]
    assert data["team_count"] == 1
    assert team["id"] in (data["team_ids"] or [])


def test_seed_blueprints_reconcile_drift_and_preserve_ids(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")
    backlog_artifact = next(artifact for artifact in scrum_blueprint["artifacts"] if artifact["title"] == "Scrum Backlog")

    with Session(engine) as session:
        persisted_role = session.get(BlueprintRoleDB, developer_role["id"])
        persisted_artifact = session.get(BlueprintArtifactDB, backlog_artifact["id"])
        persisted_role.description = "drifted description"
        persisted_artifact.description = "drifted artifact"
        session.add(
            BlueprintRoleDB(
                blueprint_id=scrum_blueprint["id"],
                name="Temporary Drift Role",
                description="should be removed",
                sort_order=90,
                is_required=False,
                config={},
            )
        )
        session.add(
            BlueprintArtifactDB(
                blueprint_id=scrum_blueprint["id"],
                kind="task",
                title="Temporary Drift Artifact",
                description="should be removed",
                sort_order=90,
                payload={},
            )
        )
        session.commit()

    reconcile_response = client.get("/teams/blueprints", headers=auth_header)
    assert reconcile_response.status_code == 200
    scrum_blueprint = next(blueprint for blueprint in reconcile_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role_after = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")
    backlog_artifact_after = next(artifact for artifact in scrum_blueprint["artifacts"] if artifact["title"] == "Scrum Backlog")

    assert developer_role_after["id"] == developer_role["id"]
    assert developer_role_after["description"] == "Builds and delivers backlog items."
    assert backlog_artifact_after["id"] == backlog_artifact["id"]
    assert backlog_artifact_after["description"] == "Initiales Product Backlog für das Team."
    assert all(role["name"] != "Temporary Drift Role" for role in scrum_blueprint["roles"])
    assert all(artifact["title"] != "Temporary Drift Artifact" for artifact in scrum_blueprint["artifacts"])


def test_seed_blueprint_reconcile_keeps_instantiation_contract(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")

    with Session(engine) as session:
        persisted_role = session.get(BlueprintRoleDB, developer_role["id"])
        persisted_role.description = "temporary drift"
        session.add(
            BlueprintArtifactDB(
                blueprint_id=scrum_blueprint["id"],
                kind="task",
                title="Drift Artifact",
                description="should be removed before instantiate",
                sort_order=95,
                payload={"status": "todo"},
            )
        )
        session.commit()

    reconcile_response = client.get("/teams/blueprints", headers=auth_header)
    assert reconcile_response.status_code == 200
    reconciled_scrum = next(blueprint for blueprint in reconcile_response.json["data"] if blueprint["name"] == "Scrum")
    reconciled_developer = next(role for role in reconciled_scrum["roles"] if role["name"] == "Developer")
    assert reconciled_developer["id"] == developer_role["id"]

    instantiate_response = client.post(
        f"/teams/blueprints/{reconciled_scrum['id']}/instantiate",
        json={
            "name": "Reconciled Scrum Team",
            "activate": False,
            "members": [{"agent_url": "http://worker-dev", "blueprint_role_id": reconciled_developer["id"]}],
        },
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_snapshot"]["name"] == "Scrum"
    assert all(artifact["title"] != "Drift Artifact" for artifact in (team["blueprint_snapshot"].get("artifacts") or []))

    with Session(engine) as session:
        persisted_members = session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team["id"])).all()
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    assert len(persisted_members) == 1
    assert persisted_members[0].blueprint_role_id == reconciled_developer["id"]
    assert any(task.title == "Reconciled Scrum Team: Scrum Backlog" for task in persisted_tasks)


def test_blueprint_update_preserves_existing_child_ids(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    create_response = client.post(
        "/teams/blueprints",
        json={
            "name": "Stable Id Blueprint",
            "description": "preserve ids",
            "roles": [
                {"name": "Developer", "description": "initial", "sort_order": 10, "is_required": True, "config": {}},
            ],
            "artifacts": [
                {"kind": "task", "title": "Kickoff", "description": "initial", "sort_order": 10, "payload": {"status": "todo"}},
            ],
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201
    blueprint = create_response.json["data"]
    developer_role = next(role for role in blueprint["roles"] if role["name"] == "Developer")
    kickoff_artifact = next(artifact for artifact in blueprint["artifacts"] if artifact["title"] == "Kickoff")

    update_response = client.patch(
        f"/teams/blueprints/{blueprint['id']}",
        json={
            "roles": [
                {"name": "Developer", "description": "updated", "sort_order": 10, "is_required": True, "config": {"focus": "build"}},
                {"name": "Reviewer", "description": "new", "sort_order": 20, "is_required": False, "config": {}},
            ],
            "artifacts": [
                {"kind": "task", "title": "Kickoff", "description": "updated", "sort_order": 10, "payload": {"status": "todo", "priority": "High"}},
                {"kind": "policy", "title": "Definition Of Done", "description": "new", "sort_order": 20, "payload": {"required": True}},
            ],
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200
    updated_blueprint = update_response.json["data"]
    developer_role_after = next(role for role in updated_blueprint["roles"] if role["name"] == "Developer")
    kickoff_artifact_after = next(artifact for artifact in updated_blueprint["artifacts"] if artifact["title"] == "Kickoff")
    reviewer_role = next(role for role in updated_blueprint["roles"] if role["name"] == "Reviewer")
    dod_artifact = next(artifact for artifact in updated_blueprint["artifacts"] if artifact["title"] == "Definition Of Done")

    assert developer_role_after["id"] == developer_role["id"]
    assert developer_role_after["description"] == "updated"
    assert kickoff_artifact_after["id"] == kickoff_artifact["id"]
    assert kickoff_artifact_after["description"] == "updated"
    assert reviewer_role["id"] != developer_role["id"]
    assert dod_artifact["id"] != kickoff_artifact["id"]


def test_blueprint_audit_log_contains_child_change_sets(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    create_response = client.post(
        "/teams/blueprints",
        json={
            "name": "Audit Blueprint",
            "description": "audit create",
            "roles": [{"name": "Developer", "description": "initial", "sort_order": 10, "is_required": True, "config": {}}],
            "artifacts": [{"kind": "task", "title": "Kickoff", "description": "initial", "sort_order": 10, "payload": {"status": "todo"}}],
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201
    blueprint = create_response.json["data"]

    create_log = next(
        log
        for log in audit_repo.get_all(limit=500)
        if log.action == "team_blueprint_created" and log.details.get("blueprint_id") == blueprint["id"]
    )
    assert create_log.details["changes"]["blueprint_fields"] == ["name", "description", "base_team_type_name", "is_seed"]
    assert create_log.details["changes"]["roles"]["created"] == [{"name": "Developer"}]
    assert create_log.details["changes"]["artifacts"]["created"] == [{"title": "Kickoff", "kind": "task"}]

    update_response = client.patch(
        f"/teams/blueprints/{blueprint['id']}",
        json={
            "description": "audit update",
            "roles": [{"name": "Developer", "description": "updated", "sort_order": 10, "is_required": True, "config": {"focus": "build"}}],
            "artifacts": [{"kind": "task", "title": "Kickoff", "description": "updated", "sort_order": 10, "payload": {"status": "todo", "priority": "High"}}],
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200

    update_log = next(
        log
        for log in audit_repo.get_all(limit=500)
        if log.action == "team_blueprint_updated" and log.details.get("blueprint_id") == blueprint["id"]
    )
    assert update_log.details["changes"]["blueprint_fields"] == ["description"]
    assert update_log.details["changes"]["roles"]["updated"] == [{"name": "Developer", "fields": ["description", "config"]}]
    assert update_log.details["changes"]["artifacts"]["updated"] == [{"title": "Kickoff", "fields": ["description", "payload"]}]


def test_seed_reconcile_writes_audit_diff(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")
    backlog_artifact = next(artifact for artifact in scrum_blueprint["artifacts"] if artifact["title"] == "Scrum Backlog")

    with Session(engine) as session:
        persisted_role = session.get(BlueprintRoleDB, developer_role["id"])
        persisted_artifact = session.get(BlueprintArtifactDB, backlog_artifact["id"])
        persisted_role.description = "drift"
        persisted_artifact.description = "drift"
        session.add(
            BlueprintRoleDB(
                blueprint_id=scrum_blueprint["id"],
                name="Audit Drift Role",
                description="remove me",
                sort_order=90,
                is_required=False,
                config={},
            )
        )
        session.add(
            BlueprintArtifactDB(
                blueprint_id=scrum_blueprint["id"],
                kind="task",
                title="Audit Drift Artifact",
                description="remove me",
                sort_order=90,
                payload={},
            )
        )
        session.commit()

    reconcile_response = client.get("/teams/blueprints", headers=auth_header)
    assert reconcile_response.status_code == 200

    reconcile_log = next(
        log
        for log in audit_repo.get_all(limit=500)
        if log.action == "team_blueprint_reconciled" and log.details.get("blueprint_id") == scrum_blueprint["id"]
    )
    assert reconcile_log.details["source"] == "seed_sync"
    assert reconcile_log.details["changes"]["roles"]["updated"] == [{"name": "Developer", "fields": ["description"]}]
    assert reconcile_log.details["changes"]["roles"]["deleted"] == [{"name": "Audit Drift Role"}]
    assert reconcile_log.details["changes"]["artifacts"]["updated"] == [{"title": "Scrum Backlog", "fields": ["description"]}]
    assert reconcile_log.details["changes"]["artifacts"]["deleted"] == [{"title": "Audit Drift Artifact", "kind": "task"}]


def test_blueprint_constraints_block_duplicate_rows_on_db_level():
    with Session(engine) as session:
        blueprint = TeamBlueprintDB(name="Constraint Blueprint", description="db integrity", is_seed=False)
        session.add(blueprint)
        session.commit()
        session.refresh(blueprint)

        session.add(BlueprintRoleDB(blueprint_id=blueprint.id, name="Developer", sort_order=10, is_required=True, config={}))
        session.commit()

        session.add(BlueprintRoleDB(blueprint_id=blueprint.id, name="Developer", sort_order=20, is_required=True, config={}))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(BlueprintArtifactDB(blueprint_id=blueprint.id, kind="task", title="Kickoff", sort_order=10, payload={}))
        session.commit()
        session.add(BlueprintArtifactDB(blueprint_id=blueprint.id, kind="policy", title="Policy", sort_order=10, payload={}))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_create_blueprint_rolls_back_on_child_persist_failure(client, monkeypatch):
    import agent.services.team_blueprint_persistence_service as team_blueprint_persistence_service

    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    def fail_persist(*args, **kwargs):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(team_blueprint_persistence_service, "persist_blueprint_children_in_session", fail_persist)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Rollback Blueprint",
            "description": "should not persist",
            "roles": [{"name": "Developer", "sort_order": 10, "is_required": True, "config": {}}],
            "artifacts": [],
        },
        headers=auth_header,
    )
    assert response.status_code == 500

    with Session(engine) as session:
        persisted = session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == "Rollback Blueprint")).first()
    assert persisted is None


def test_instantiate_blueprint_rolls_back_when_materialization_fails(client, monkeypatch):
    import agent.services.team_blueprint_instantiation_service as team_blueprint_instantiation_service

    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")

    def fail_materialization(*args, **kwargs):
        raise RuntimeError("materialization failed")

    monkeypatch.setattr(team_blueprint_instantiation_service, "_materialize_blueprint_artifacts_in_session", fail_materialization)

    with Session(engine) as session:
        before_team_count = len(session.exec(select(TeamDB)).all())
        before_member_count = len(session.exec(select(TeamMemberDB)).all())
        before_rollback_task_count = len(session.exec(select(TaskDB).where(TaskDB.title.startswith("Rollback Team:"))).all())

    response = client.post(
        f"/teams/blueprints/{scrum_blueprint['id']}/instantiate",
        json={"name": "Rollback Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert response.status_code == 500

    with Session(engine) as session:
        persisted_team = session.exec(select(TeamDB).where(TeamDB.name == "Rollback Team")).first()
        persisted_members = len(session.exec(select(TeamMemberDB)).all())
        persisted_tasks = len(session.exec(select(TaskDB).where(TaskDB.title.startswith("Rollback Team:"))).all())
    assert persisted_team is None
    assert persisted_members == before_member_count
    assert persisted_tasks == before_rollback_task_count
    with Session(engine) as session:
        after_team_count = len(session.exec(select(TeamDB)).all())
    assert after_team_count == before_team_count
