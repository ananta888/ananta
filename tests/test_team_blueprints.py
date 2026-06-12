import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import BlueprintArtifactDB, BlueprintRoleDB, TaskDB, TeamBlueprintDB, TeamDB, TeamMemberDB
from agent.repository import audit_repo
from tests_support import admin_login_token as _login_admin


def test_seed_blueprints_are_listed(client):
    admin_token = _login_admin(client)

    response = client.get("/teams/blueprints", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    blueprints = response.json["data"]
    names = {blueprint["name"] for blueprint in blueprints}
    assert {
        "Scrum",
        "Kanban",
        "Research",
        "Code-Repair",
        "TDD",
        "Security-Review",
        "Release-Prep",
        "Research-Evolution",
    }.issubset(names)

    scrum_blueprint = next(blueprint for blueprint in blueprints if blueprint["name"] == "Scrum")
    assert scrum_blueprint["is_seed"] is True
    assert (scrum_blueprint.get("version_metadata") or {}).get("origin_kind") == "seed_blueprint"
    assert (scrum_blueprint.get("version_metadata") or {}).get("revision")
    assert {role["name"] for role in scrum_blueprint["roles"]} == {"Product Owner", "Scrum Master", "Developer"}
    assert any(artifact["title"] == "Scrum Backlog" for artifact in scrum_blueprint["artifacts"])

    research_blueprint = next(blueprint for blueprint in blueprints if blueprint["name"] == "Research")
    assert research_blueprint["is_seed"] is True
    assert {role["name"] for role in research_blueprint["roles"]} == {"Research Lead", "Source Analyst", "Reviewer"}
    research_lead = next(role for role in research_blueprint["roles"] if role["name"] == "Research Lead")
    assert (research_lead.get("config") or {}).get("capability_defaults")
    assert (research_lead.get("config") or {}).get("risk_profile") in {"low", "balanced", "high", "strict"}
    assert isinstance((research_lead.get("config") or {}).get("verification_defaults"), dict)
    policy_artifact = next((artifact for artifact in research_blueprint["artifacts"] if artifact["kind"] == "policy"), None)
    assert policy_artifact is not None
    assert (policy_artifact.get("payload") or {}).get("security_level") == "balanced"

    opencode_scrum = next(blueprint for blueprint in blueprints if blueprint["name"] == "Scrum-OpenCode")
    assert opencode_scrum["is_seed"] is True
    assert opencode_scrum["base_team_type_name"] == "Scrum"
    assert {role["name"] for role in opencode_scrum["roles"]} == {"Product Owner", "Scrum Master", "Developer"}
    assert any(artifact["title"] == "Execution Cascade Agreement" for artifact in opencode_scrum["artifacts"])
    opencode_policy = next(
        (artifact for artifact in opencode_scrum["artifacts"] if artifact["kind"] == "policy"),
        None,
    )
    assert opencode_policy is not None
    assert (opencode_policy.get("payload") or {}).get("artifact_flow_expected") is True

    research_evolution = next(blueprint for blueprint in blueprints if blueprint["name"] == "Research-Evolution")
    assert research_evolution["is_seed"] is True
    assert research_evolution["base_team_type_name"] == "Research-Evolution"
    assert {role["name"] for role in research_evolution["roles"]} == {
        "Research Lead",
        "Evolution Strategist",
        "Review Gate Owner",
    }
    role_by_name = {role["name"]: role for role in research_evolution["roles"]}
    assert (role_by_name["Research Lead"]["config"] or {})["preferred_backend"] == "deerflow"
    assert (role_by_name["Evolution Strategist"]["config"] or {})["preferred_backend"] == "evolver"
    assert (role_by_name["Review Gate Owner"]["config"] or {})["risk_profile"] == "strict"
    policy = next(artifact for artifact in research_evolution["artifacts"] if artifact["title"] == "Research Evolution Default Policy")
    assert policy["payload"]["standard_case"] == "existing_project_small_feature_extension"
    assert policy["payload"]["handoff_contract"]["from_deerflow"] == [
        "summary",
        "sources",
        "report_markdown",
        "research_metadata",
    ]

    tdd_blueprint = next(blueprint for blueprint in blueprints if blueprint["name"] == "TDD")
    assert tdd_blueprint["is_seed"] is True
    assert {role["name"] for role in tdd_blueprint["roles"]} == {
        "Behavior Analyst",
        "Test Driver",
        "Refactor Verifier",
    }
    tdd_policy = next((artifact for artifact in tdd_blueprint["artifacts"] if artifact["title"] == "TDD Default Policy"), None)
    assert tdd_policy is not None
    assert (tdd_policy.get("payload") or {}).get("patch_apply_requires_approval") is True


def test_blueprint_catalog_exposes_standard_product_cards(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    response = client.get("/teams/blueprints/catalog", headers=auth_header)

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["public_model"]["template_term"] == "Role Template"
    assert payload["public_model"]["default_entry_path"]
    assert payload["public_model"]["advanced_concepts"] == ["snapshot", "drift", "reconcile"]

    items = payload["items"]
    names = [item["name"] for item in items]
    assert names[:3] == ["Scrum", "Kanban", "Research"]
    assert {
        "Scrum",
        "Kanban",
        "Research",
        "Code-Repair",
        "TDD",
        "Security-Review",
        "Release-Prep",
        "Scrum-OpenCode",
        "Research-Evolution",
    }.issubset(set(names))

    scrum_item = next(item for item in items if item["name"] == "Scrum")
    assert scrum_item["is_standard_blueprint"] is True
    assert scrum_item["entry_recommended"] is True
    assert scrum_item["intended_use"]
    assert scrum_item["when_to_use"]
    assert scrum_item["expected_outputs"]
    assert scrum_item["safety_review_stance"]
    work_profile_summary = scrum_item["work_profile_summary"]
    assert work_profile_summary["recommended_goal_modes"]
    assert work_profile_summary["playbook_hints"]
    assert isinstance(work_profile_summary["capability_hints"], list)
    assert work_profile_summary["governance_profile"]["label"]
    assert work_profile_summary["governance_profile"]["hint"]

    tdd_item = next(item for item in items if item["name"] == "TDD")
    assert "TestPlanArtifact" in tdd_item["expected_outputs"]
    tdd_summary = tdd_item["work_profile_summary"]
    assert "code_fix" in tdd_summary["recommended_goal_modes"]
    assert "refactoring" in tdd_summary["playbook_hints"]
    capability_hints = set(tdd_summary["capability_hints"])
    assert {"worker.patch.propose", "worker.test.run", "worker.verify.result"}.issubset(capability_hints)


def test_blueprint_catalog_compact_read_model_hides_admin_child_details(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    response = client.get("/teams/blueprints/catalog", headers=auth_header)
    assert response.status_code == 200
    items = response.json["data"]["items"]
    assert items

    for item in items:
        assert item["intended_use"]
        assert item["when_to_use"]
        assert item["expected_outputs"]
        assert item["safety_review_stance"]
        assert "roles" not in item
        assert "artifacts" not in item
        assert "version_metadata" not in item

        summary = item["work_profile_summary"]
        assert summary["recommended_goal_modes"]
        assert isinstance(summary["playbook_hints"], list)
        assert isinstance(summary["capability_hints"], list)
        assert summary["governance_profile"]["label"]
        assert summary["governance_profile"]["hint"]


def test_standard_blueprint_onboarding_smoke_path_from_catalog(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    catalog_response = client.get("/teams/blueprints/catalog", headers=auth_header)
    assert catalog_response.status_code == 200
    catalog_items = catalog_response.json["data"]["items"]
    recommended = next(
        item for item in catalog_items if item.get("entry_recommended") and item.get("is_standard_blueprint")
    )
    assert recommended["expected_outputs"]

    instantiate_response = client.post(
        f"/teams/blueprints/{recommended['id']}/instantiate",
        json={"name": "Catalog Onboarding Smoke Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == recommended["id"]
    assert team["blueprint_snapshot"]["name"] == recommended["name"]

    snapshot_task_titles = {
        artifact["title"]
        for artifact in (team["blueprint_snapshot"].get("artifacts") or [])
        if str(artifact.get("kind", "")).lower() == "task"
    }
    assert snapshot_task_titles
    assert set(recommended["expected_outputs"]).intersection(snapshot_task_titles)

    teams_response = client.get("/teams", headers=auth_header)
    assert teams_response.status_code == 200
    listed_team = next(item for item in teams_response.json["data"] if item["id"] == team["id"])
    lifecycle = listed_team.get("user_lifecycle_state") or {}
    assert lifecycle.get("label") in {"Standard", "Angepasst", "Aktualisierbar"}
    assert lifecycle.get("hint")

    profile_response = client.get(f"/teams/blueprints/{recommended['id']}/work-profile", headers=auth_header)
    assert profile_response.status_code == 200
    profile = profile_response.json["data"]
    assert profile["goal_modes"] or profile["playbooks"]

    with Session(engine) as session:
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()
    assert persisted_tasks


def test_blueprint_crud_and_instantiate(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")

    create_response = client.post(
        "/teams/blueprints",
        json={
            "name": "Delivery Pod",
            "description": "Custom delivery team blueprint",
            "base_team_type_name": "Kanban",
            "roles": [
                {
                    "name": "Delivery Lead",
                    "description": "Owns delivery coordination.",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"focus": "coordination"},
                }
            ],
            "artifacts": [
                {
                    "kind": "task",
                    "title": "Kickoff",
                    "description": "Start the team cadence.",
                    "sort_order": 10,
                    "payload": {"status": "todo", "priority": "High"},
                }
            ],
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201
    created_blueprint = create_response.json["data"]
    assert created_blueprint["base_team_type_name"] == "Kanban"

    update_response = client.patch(
        f"/teams/blueprints/{created_blueprint['id']}",
        json={
            "description": "Updated delivery team blueprint",
            "roles": [
                {
                    "name": "Delivery Lead",
                    "description": "Owns delivery coordination and sequencing.",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"focus": "sequencing"},
                },
                {
                    "name": "Developer",
                    "description": "Builds the increment.",
                    "sort_order": 20,
                    "is_required": True,
                    "config": {"focus": "implementation"},
                },
            ],
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200
    updated_blueprint = update_response.json["data"]
    assert updated_blueprint["description"] == "Updated delivery team blueprint"
    assert len(updated_blueprint["roles"]) == 2

    instantiate_response = client.post(
        f"/teams/blueprints/{scrum_blueprint['id']}/instantiate",
        json={
            "name": "Blueprint Team Alpha",
            "activate": True,
            "members": [
                {
                    "agent_url": "http://worker-dev",
                    "blueprint_role_id": developer_role["id"],
                }
            ],
        },
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == scrum_blueprint["id"]
    assert team["is_active"] is True
    assert team["blueprint_snapshot"]["name"] == "Scrum"
    assert (team.get("definition_metadata") or {}).get("origin_kind") == "seed_blueprint_instance"
    assert (team.get("definition_metadata") or {}).get("drift_status") == "in_sync"

    with Session(engine) as session:
        persisted_team = session.get(TeamDB, team["id"])
        persisted_members = session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team["id"])).all()
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    assert persisted_team is not None
    assert persisted_team.blueprint_id == scrum_blueprint["id"]
    assert persisted_team.blueprint_snapshot["name"] == "Scrum"
    assert len(persisted_members) == 1
    assert persisted_members[0].blueprint_role_id == developer_role["id"]
    assert any(task.title == "Blueprint Team Alpha: Scrum Backlog" for task in persisted_tasks)


def test_blueprint_work_profile_exposes_operational_modes(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    opencode_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum-OpenCode")

    response = client.get(f"/teams/blueprints/{opencode_blueprint['id']}/work-profile", headers=auth_header)
    assert response.status_code == 200
    profile = response.json["data"]
    assert profile["blueprint_id"] == opencode_blueprint["id"]
    assert "code_fix" in profile["goal_modes"]
    assert "docker_compose_repair" in profile["goal_modes"]
    assert "incident" in profile["playbooks"]
    assert "opencode" in profile["preferred_backends"] or "sgpt" in profile["preferred_backends"]
    assert profile["policy_profiles"]
    assert any(item["title"] == "OpenCode Scrum Default Policy" for item in profile["policy_profiles"])


def test_team_blueprint_diff_reports_snapshot_drift(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")

    instantiate_response = client.post(
        f"/teams/blueprints/{scrum_blueprint['id']}/instantiate",
        json={"name": "Drift Check Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]

    update_response = client.patch(
        f"/teams/blueprints/{scrum_blueprint['id']}",
        json={
            "roles": [
                *[
                    {
                        "name": role["name"],
                        "description": role.get("description"),
                        "template_id": role.get("template_id"),
                        "sort_order": role.get("sort_order"),
                        "is_required": role.get("is_required", True),
                        "config": role.get("config") or {},
                    }
                    for role in scrum_blueprint["roles"]
                ],
                {
                    "name": "Definition Drift Reviewer",
                    "description": "Reviews blueprint drift.",
                    "sort_order": 999,
                    "is_required": False,
                    "config": {},
                },
            ]
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200

    diff_response = client.get(f"/teams/{team['id']}/blueprint-diff", headers=auth_header)
    assert diff_response.status_code == 200
    diff = diff_response.json["data"]
    assert diff["definition_metadata"]["drift_status"] == "drifted"
    assert "Definition Drift Reviewer" in diff["diff"]["roles_added"]


def test_team_list_exposes_simplified_lifecycle_state(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")

    instantiate_response = client.post(
        f"/teams/blueprints/{scrum_blueprint['id']}/instantiate",
        json={"name": "Lifecycle Label Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team_id = instantiate_response.json["data"]["team"]["id"]

    list_response = client.get("/teams", headers=auth_header)
    assert list_response.status_code == 200
    team = next(item for item in list_response.json["data"] if item["id"] == team_id)
    assert team["user_lifecycle_state"]["code"] == "standard"
    assert team["user_lifecycle_state"]["label"] == "Standard"

    update_response = client.patch(
        f"/teams/blueprints/{scrum_blueprint['id']}",
        json={
            "roles": [
                *[
                    {
                        "name": role["name"],
                        "description": role.get("description"),
                        "template_id": role.get("template_id"),
                        "sort_order": role.get("sort_order"),
                        "is_required": role.get("is_required", True),
                        "config": role.get("config") or {},
                    }
                    for role in scrum_blueprint["roles"]
                ],
                {
                    "name": "Lifecycle Drift Role",
                    "description": "creates drift",
                    "sort_order": 910,
                    "is_required": False,
                    "config": {},
                },
            ]
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200

    list_after_drift = client.get("/teams", headers=auth_header)
    assert list_after_drift.status_code == 200
    drifted_team = next(item for item in list_after_drift.json["data"] if item["id"] == team_id)
    assert drifted_team["user_lifecycle_state"]["code"] == "outdated"
    assert drifted_team["user_lifecycle_state"]["label"] == "Aktualisierbar"
    assert (drifted_team.get("definition_metadata") or {}).get("drift_status") == "drifted"

    diff_response = client.get(f"/teams/{team_id}/blueprint-diff", headers=auth_header)
    assert diff_response.status_code == 200
    assert (diff_response.json["data"].get("definition_metadata") or {}).get("drift_status") == "drifted"

    update_log = next(
        log
        for log in audit_repo.get_all(limit=500)
        if log.action == "team_blueprint_updated" and log.details.get("blueprint_id") == scrum_blueprint["id"]
    )
    assert update_log.details["changes"]["roles"]["created"] == [{"name": "Lifecycle Drift Role"}]


def test_setup_scrum_uses_seed_blueprint(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/setup-scrum",
        json={"name": "Scrum via Blueprint"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 201
    team = response.json["data"]["team"]
    blueprint = response.json["data"]["blueprint"]
    assert blueprint["name"] == "Scrum"
    assert team["blueprint_id"] == blueprint["id"]

    with Session(engine) as session:
        persisted_team = session.get(TeamDB, team["id"])

    assert persisted_team is not None
    assert persisted_team.blueprint_snapshot["name"] == "Scrum"


def test_blueprint_validation_rejects_duplicate_role_names(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Blueprint",
            "roles": [
                {"name": "Developer", "sort_order": 10, "is_required": True, "config": {}},
                {"name": "Developer", "sort_order": 20, "is_required": True, "config": {}},
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "duplicate_blueprint_role_name"


def test_blueprint_validation_rejects_duplicate_role_sort_order(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Role Order Blueprint",
            "roles": [
                {"name": "Developer", "sort_order": 10, "is_required": True, "config": {}},
                {"name": "Reviewer", "sort_order": 10, "is_required": True, "config": {}},
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "duplicate_blueprint_role_sort_order"


def test_blueprint_validation_rejects_duplicate_artifact_titles(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Artifact Title Blueprint",
            "roles": [],
            "artifacts": [
                {"kind": "task", "title": "Kickoff", "sort_order": 10, "payload": {}},
                {"kind": "task", "title": " kickoff ", "sort_order": 20, "payload": {}},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "duplicate_blueprint_artifact_title"


def test_blueprint_validation_rejects_duplicate_artifact_sort_order(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Artifact Order Blueprint",
            "roles": [],
            "artifacts": [
                {"kind": "task", "title": "Kickoff", "sort_order": 10, "payload": {}},
                {"kind": "policy", "title": "Policy", "sort_order": 10, "payload": {}},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "duplicate_blueprint_artifact_sort_order"


def test_blueprint_validation_rejects_invalid_artifact_kind(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Artifact Kind Blueprint",
            "roles": [],
            "artifacts": [
                {"kind": "note", "title": "Kickoff", "sort_order": 10, "payload": {}},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "blueprint_artifact_kind_invalid"


def test_blueprint_validation_rejects_missing_role_template_reference(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Template Ref Blueprint",
            "roles": [
                {
                    "name": "Developer",
                    "sort_order": 10,
                    "is_required": True,
                    "template_id": "missing-template-id",
                    "config": {},
                }
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json["message"] == "template_not_found"


def test_blueprint_validation_rejects_invalid_execution_mode(client):
    """DRR-T036: execution_mode with invalid format is rejected."""
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Bad Execution Mode Blueprint",
            "roles": [
                {
                    "name": "Repair Lead",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"execution_mode": "INVALID MODE!"},
                }
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "blueprint_role_execution_mode_invalid"


def test_blueprint_validation_accepts_repair_execution_mode(client):
    """DRR-T036: execution_mode='deterministic_repair' is a valid value."""
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Repair Blueprint DRR036",
            "roles": [
                {
                    "name": "Repair Lead",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {
                        "execution_mode": "deterministic_repair",
                        "preferred_backend": "native_worker",
                        "capability_defaults": ["repair.diagnose", "repair.execute.low_risk"],
                        "risk_profile": "high",
                        "verification_defaults": {"require_post_mutation": True},
                    },
                }
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 201
    blueprint = response.json["data"]
    role = next(r for r in blueprint["roles"] if r["name"] == "Repair Lead")
    assert role["config"]["execution_mode"] == "deterministic_repair"
    assert role["config"]["preferred_backend"] == "native_worker"


def test_blueprint_bundle_roundtrip_preserves_repair_role_config(client):
    """DRR-T039: bundle export/import preserves repair role config including execution_mode."""
    admin_token = _login_admin(client)
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Create a blueprint with repair role config
    create_resp = client.post(
        "/teams/blueprints",
        json={
            "name": "Bundle Roundtrip Repair DRR039",
            "roles": [
                {
                    "name": "Repair Agent",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {
                        "execution_mode": "deterministic_repair",
                        "preferred_backend": "native_worker",
                        "capability_defaults": ["repair.diagnose"],
                        "risk_profile": "high",
                        "verification_defaults": {"required": True},
                    },
                }
            ],
            "artifacts": [],
        },
        headers=auth,
    )
    assert create_resp.status_code == 201
    blueprint_id = create_resp.json["data"]["id"]

    # Export
    export_resp = client.get(f"/teams/blueprints/{blueprint_id}/bundle", headers=auth)
    assert export_resp.status_code == 200
    bundle = export_resp.json["data"]

    # Bundle has blueprint.roles structure
    blueprint_in_bundle = bundle["blueprint"]
    assert blueprint_in_bundle is not None
    role_in_bundle = next(r for r in blueprint_in_bundle["roles"] if r["name"] == "Repair Agent")
    assert role_in_bundle["config"]["execution_mode"] == "deterministic_repair"
    assert role_in_bundle["config"]["preferred_backend"] == "native_worker"

    # Validate that the exported config passes bundle import validation
    from agent.models import BlueprintBundleDefinition, BlueprintBundleRoleDefinition
    from agent.services.blueprint_bundle_service import _validate_blueprint_bundle_definition

    bundle_def = BlueprintBundleDefinition(
        name="Reimported Bundle DRR039b",
        roles=[
            BlueprintBundleRoleDefinition(
                name="Repair Agent",
                sort_order=10,
                is_required=True,
                config=role_in_bundle["config"],
            )
        ],
        artifacts=[],
    )
    errors = _validate_blueprint_bundle_definition(bundle_def)
    assert errors == [], f"Unexpected validation errors on reimport: {errors}"


def test_seed_research_blueprint_instantiation_materializes_tasks(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    research_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Research")
    source_analyst_role = next(role for role in research_blueprint["roles"] if role["name"] == "Source Analyst")

    instantiate_response = client.post(
        f"/teams/blueprints/{research_blueprint['id']}/instantiate",
        json={
            "name": "Research Pod Alpha",
            "activate": False,
            "members": [
                {
                    "agent_url": "http://worker-research",
                    "blueprint_role_id": source_analyst_role["id"],
                }
            ],
        },
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == research_blueprint["id"]
    assert team["blueprint_snapshot"]["name"] == "Research"
    policy_artifacts = [a for a in (team["blueprint_snapshot"].get("artifacts") or []) if a.get("kind") == "policy"]
    assert policy_artifacts

    with Session(engine) as session:
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    assert any(task.title == "Research Pod Alpha: Research Intake" for task in persisted_tasks)


def test_seed_research_evolution_blueprint_instantiates_standard_path(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Research-Evolution")
    evolution_role = next(role for role in blueprint["roles"] if role["name"] == "Evolution Strategist")

    instantiate_response = client.post(
        f"/teams/blueprints/{blueprint['id']}/instantiate",
        json={
            "name": "Research Evolution Alpha",
            "activate": False,
            "members": [
                {
                    "agent_url": "http://worker-evolver",
                    "blueprint_role_id": evolution_role["id"],
                }
            ],
        },
        headers=auth_header,
    )

    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == blueprint["id"]
    assert team["blueprint_snapshot"]["name"] == "Research-Evolution"
    assert any(
        artifact["title"] == "Research Evolution Default Policy"
        for artifact in team["blueprint_snapshot"].get("artifacts", [])
    )

    with Session(engine) as session:
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    task_titles = {task.title for task in persisted_tasks}
    assert "Research Evolution Alpha: DeerFlow Research Stage" in task_titles
    assert "Research Evolution Alpha: Evolver Proposal Stage" in task_titles
    assert "Research Evolution Alpha: Review Gate" in task_titles
