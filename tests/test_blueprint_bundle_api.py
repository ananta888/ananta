from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TeamBlueprintDB, TeamDB, TemplateDB


def _login_admin(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def _create_template(client, auth_header, name: str, prompt_template: str) -> dict:
    response = client.post(
        "/templates",
        json={"name": name, "description": f"{name} description", "prompt_template": prompt_template},
        headers=auth_header,
    )
    assert response.status_code == 201
    return response.json["data"]


def _create_blueprint(client, auth_header, name: str, template_id: str) -> dict:
    response = client.post(
        "/teams/blueprints",
        json={
            "name": name,
            "description": f"{name} description",
            "base_team_type_name": "Scrum",
            "roles": [
                {
                    "name": "Developer",
                    "description": "Builds increments.",
                    "template_id": template_id,
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"focus": "implementation"},
                }
            ],
            "artifacts": [
                {
                    "kind": "task",
                    "title": "Kickoff",
                    "description": "Initial work item.",
                    "sort_order": 10,
                    "payload": {"status": "todo", "priority": "High"},
                }
            ],
        },
        headers=auth_header,
    )
    assert response.status_code == 201
    return response.json["data"]


def _instantiate_blueprint_team(client, auth_header, blueprint: dict, team_name: str, custom_template_id: str | None = None) -> dict:
    developer_role = next(role for role in blueprint["roles"] if role["name"] == "Developer")
    response = client.post(
        f"/teams/blueprints/{blueprint['id']}/instantiate",
        json={
            "name": team_name,
            "activate": False,
            "members": [
                {
                    "agent_url": "http://worker-dev",
                    "blueprint_role_id": developer_role["id"],
                    "custom_template_id": custom_template_id,
                }
            ],
        },
        headers=auth_header,
    )
    assert response.status_code == 201
    return response.json["data"]["team"]


def test_export_blueprint_bundle_full_includes_templates_and_team(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    role_template = _create_template(client, auth_header, "Bundle Role Template", "Role prompt for {{team_goal}}")
    member_template = _create_template(client, auth_header, "Bundle Member Template", "Member prompt for {{task_title}}")
    blueprint = _create_blueprint(client, auth_header, "Bundle Export Blueprint", role_template["id"])
    team = _instantiate_blueprint_team(client, auth_header, blueprint, "Bundle Export Team", member_template["id"])

    response = client.get(
        f"/teams/blueprints/{blueprint['id']}/bundle?mode=full&team_id={team['id']}&include_members=true",
        headers=auth_header,
    )

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["schema_version"] == "1.0"
    assert payload["mode"] == "full"
    assert set(payload["parts"]) == {"blueprint", "templates", "team"}
    assert payload["blueprint"]["name"] == "Bundle Export Blueprint"
    assert payload["blueprint"]["roles"][0]["template_name"] == "Bundle Role Template"
    assert {item["name"] for item in payload["templates"]} == {"Bundle Role Template", "Bundle Member Template"}
    assert payload["team"]["role_templates"]["Developer"] == "Bundle Role Template"
    assert payload["team"]["members"][0]["custom_template_name"] == "Bundle Member Template"


def test_export_blueprint_bundle_split_can_limit_parts(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    role_template = _create_template(client, auth_header, "Split Export Template", "Split prompt for {{team_goal}}")
    blueprint = _create_blueprint(client, auth_header, "Split Export Blueprint", role_template["id"])

    response = client.get(
        f"/teams/blueprints/{blueprint['id']}/bundle?mode=split&parts=templates",
        headers=auth_header,
    )

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["mode"] == "split"
    assert payload["parts"] == ["templates"]
    assert payload["blueprint"] is None
    assert payload["team"] is None
    assert payload["templates"][0]["name"] == "Split Export Template"


def test_import_blueprint_bundle_dry_run_returns_diff_without_persisting(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    bundle = {
        "schema_version": "1.0",
        "mode": "full",
        "blueprint": {
            "name": "Dry Run Blueprint",
            "description": "preview only",
            "base_team_type_name": "Scrum",
            "roles": [
                {
                    "name": "Developer",
                    "description": "Builds features",
                    "template_name": "Dry Run Template",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {},
                }
            ],
            "artifacts": [{"kind": "task", "title": "Kickoff", "sort_order": 10, "payload": {}}],
        },
        "templates": [
            {
                "name": "Dry Run Template",
                "description": "preview template",
                "prompt_template": "Preview {{team_goal}}",
            }
        ],
    }

    response = client.post(
        "/teams/blueprints/import",
        json={"conflict_strategy": "overwrite", "dry_run": True, "bundle": bundle},
        headers=auth_header,
    )

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["dry_run"] is True
    assert payload["summary"]["create"] == 2

    with Session(engine) as session:
        assert session.exec(select(TemplateDB).where(TemplateDB.name == "Dry Run Template")).first() is None
        assert session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == "Dry Run Blueprint")).first() is None


def test_import_blueprint_bundle_split_can_stage_templates_before_blueprint(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    templates_only = {
        "schema_version": "1.0",
        "mode": "split",
        "parts": ["templates"],
        "templates": [
            {
                "name": "Split Stage Template",
                "description": "first stage",
                "prompt_template": "Stage {{team_goal}}",
            }
        ],
    }
    blueprint_only = {
        "schema_version": "1.0",
        "mode": "split",
        "parts": ["blueprint"],
        "blueprint": {
            "name": "Split Stage Blueprint",
            "description": "second stage",
            "base_team_type_name": "Scrum",
            "roles": [
                {
                    "name": "Developer",
                    "description": "Builds features",
                    "template_name": "Split Stage Template",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {},
                }
            ],
            "artifacts": [],
        },
    }

    templates_response = client.post(
        "/teams/blueprints/import",
        json={"conflict_strategy": "overwrite", "dry_run": False, "bundle": templates_only},
        headers=auth_header,
    )
    assert templates_response.status_code == 200

    blueprint_response = client.post(
        "/teams/blueprints/import",
        json={"conflict_strategy": "overwrite", "dry_run": False, "bundle": blueprint_only},
        headers=auth_header,
    )
    assert blueprint_response.status_code == 200

    with Session(engine) as session:
        template = session.exec(select(TemplateDB).where(TemplateDB.name == "Split Stage Template")).first()
        blueprint = session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == "Split Stage Blueprint")).first()
        teams = session.exec(select(TeamDB)).all()

    assert template is not None
    assert blueprint is not None
    assert teams == []


def test_import_blueprint_bundle_overwrite_is_idempotent(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    role_template = _create_template(client, auth_header, "Roundtrip Role Template", "Roundtrip {{team_goal}}")
    member_template = _create_template(client, auth_header, "Roundtrip Member Template", "Roundtrip member {{task_title}}")
    blueprint = _create_blueprint(client, auth_header, "Roundtrip Blueprint", role_template["id"])
    team = _instantiate_blueprint_team(client, auth_header, blueprint, "Roundtrip Team", member_template["id"])

    export_response = client.get(
        f"/teams/blueprints/{blueprint['id']}/bundle?mode=full&team_id={team['id']}&include_members=true",
        headers=auth_header,
    )
    assert export_response.status_code == 200

    import_response = client.post(
        "/teams/blueprints/import",
        json={"conflict_strategy": "overwrite", "dry_run": False, "bundle": export_response.json["data"]},
        headers=auth_header,
    )

    assert import_response.status_code == 200
    payload = import_response.json["data"]
    assert payload["summary"]["conflict"] == 0

    with Session(engine) as session:
        templates = session.exec(select(TemplateDB).where(TemplateDB.name.in_(["Roundtrip Role Template", "Roundtrip Member Template"]))).all()
        blueprints = session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == "Roundtrip Blueprint")).all()
        teams = session.exec(select(TeamDB).where(TeamDB.name == "Roundtrip Team")).all()

    assert len(templates) == 2
    assert len(blueprints) == 1
    assert len(teams) == 1


def test_import_blueprint_bundle_fail_strategy_reports_conflicts(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    role_template = _create_template(client, auth_header, "Conflict Template", "Conflict {{team_goal}}")
    blueprint = _create_blueprint(client, auth_header, "Conflict Blueprint", role_template["id"])

    export_response = client.get(f"/teams/blueprints/{blueprint['id']}/bundle?mode=full", headers=auth_header)
    assert export_response.status_code == 200

    import_response = client.post(
        "/teams/blueprints/import",
        json={"conflict_strategy": "fail", "dry_run": False, "bundle": export_response.json["data"]},
        headers=auth_header,
    )

    assert import_response.status_code == 409
    assert import_response.json["message"] == "bundle_import_conflict"
    errors = import_response.json["data"]["errors"]
    assert any(error["message"] == "template_name_exists" for error in errors)
    assert any(error["message"] == "blueprint_name_exists" for error in errors)
