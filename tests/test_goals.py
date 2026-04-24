import time

import jwt

from agent.config import settings
from agent.db_models import AgentInfoDB
from agent.repository import agent_repo, audit_repo, goal_repo, plan_node_repo, task_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.utils import _get_local_task_status


def _mock_goal_planning_llm(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan release","description":"Prepare release artifacts","priority":"High"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)


class TestGoalsAPI:
    def test_goal_readiness_exposes_defaults(self, client, admin_auth_header):
        res = client.get("/goals/readiness", headers=admin_auth_header)
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert "defaults" in data
        assert data["defaults"]["planning"]["engine"] == "auto_planner"
        assert "happy_path_ready" in data

    def test_goal_modes_include_docker_and_runtime_repair(self, client, admin_auth_header):
        res = client.get("/goals/modes", headers=admin_auth_header)
        assert res.status_code == 200
        mode_ids = {item["id"] for item in res.get_json()["data"]}
        assert {"docker_compose_repair", "runtime_repair", "admin_repair"}.issubset(mode_ids)
        assert {"new_software_project", "project_evolution"}.issubset(mode_ids)

    def test_create_goal_from_docker_compose_mode(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "docker_compose_repair",
                "mode_data": {
                    "issue_symptom": "Container restart loop on boot",
                    "compose_file": "docker-compose.yml",
                    "service": "hub",
                },
            },
        )
        assert res.status_code == 201
        goal_payload = res.get_json()["data"]["goal"]
        assert "Docker-/Compose-Problem" in goal_payload["goal"]
        assert "Container restart loop on boot" in goal_payload["goal"]
        assert "docker-compose.yml" in goal_payload["goal"]
        assert "Fokus-Service: hub" in goal_payload["goal"]

    def test_create_goal_from_admin_repair_mode(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "admin_repair",
                "mode_data": {
                    "issue_symptom": "Service restart loop after package update",
                    "platform_target": "ubuntu",
                    "execution_scope": "bounded_repair",
                    "evidence_sources": "error_logs,service_status,runtime_state",
                    "affected_targets": "api-service",
                },
            },
        )
        assert res.status_code == 201
        payload = res.get_json()["data"]
        goal_payload = payload["goal"]
        assert "Shared Foundation" in goal_payload["goal"]
        assert "Dry-run default: True" in goal_payload["goal"]
        workflow = payload["workflow"]["effective"]
        assert workflow["planning"]["use_repo_context"] is False
        assert workflow["verification"]["review_required"] is True
        assert workflow["policy"]["runtime_execution"] == "bounded_preview_only"
        persisted_goal = goal_repo.get_by_id(goal_payload["id"])
        assert persisted_goal is not None
        assert persisted_goal.mode == "admin_repair"
        assert persisted_goal.mode_data["repair_plan"]["dry_run_default"] is True
        assert persisted_goal.mode_data["execution_session"]["execution_mode"] == "step_confirmed"
        assert persisted_goal.mode_data["verification_phase"]["schema"] == "admin_repair_verification_v1"
        assert persisted_goal.mode_data["bridge_contract"]["schema"] == "admin_repair_bridge_contract_v1"
        assert persisted_goal.mode_data["platform_evidence_adapters"]["selected_adapter"]["platform"] == "ubuntu"
        assert persisted_goal.mode_data["platform_playbooks"]["recommended_playbooks"]
        assert persisted_goal.mode_data["rollback_caution_model"]["schema"] == "admin_repair_rollback_caution_v1"
        assert persisted_goal.mode_data["golden_paths"]["schema"] == "admin_repair_golden_paths_v1"
        assert persisted_goal.mode_data["golden_paths"]["windows"]["platform_target"] == "windows11"
        assert persisted_goal.mode_data["golden_paths"]["ubuntu"]["platform_target"] == "ubuntu"
        assert persisted_goal.mode_data["future_extension_boundaries"]["schema"] == "admin_repair_extension_boundaries_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["target_model"]["model_id"] == "deterministic_repair_path_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["evidence_ingestion_model"]["allowed_evidence_types"]
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["signature_matching"]["schema"] == "deterministic_signature_matching_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["repair_procedure_model"]["schema"] == "deterministic_repair_procedure_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["repair_catalog"]["schema"] == "deterministic_repair_catalog_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["final_repair_verification"]["schema"] == "deterministic_repair_final_verification_v1"
        assert persisted_goal.mode_data["session_trail"]["entries"]
        assert persisted_goal.mode_data["cli_output"]["sections"][0]["id"] == "diagnosis"
        assert persisted_goal.mode_data["smoke_scenarios"]
        steps = persisted_goal.mode_data["repair_plan"]["steps"]
        assert steps
        first_step = steps[0]
        assert first_step["repair_action_class"] == "inspect_state"
        assert "risk_class" in first_step
        assert "requires_approval" in first_step
        assert "evidence_sources" in first_step
        assert "expected_verification" in first_step
        tasks = task_repo.get_by_goal_id(goal_payload["id"])
        assert "Dry-run-first bounded repair plan erzeugen" in {task.title for task in tasks}

    def test_create_goal_from_new_software_project_mode(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "new_software_project",
                "mode_data": {
                    "project_idea": "Release-Check-Tool fuer Maintainer",
                    "target_users": "Maintainer",
                    "platform": "Web",
                    "preferred_stack": "Python + Angular",
                    "non_goals": "Keine Vollautomatik ohne Review",
                },
            },
        )
        assert res.status_code == 201
        goal_payload = res.get_json()["data"]["goal"]
        assert "neues Softwareprojekt" in goal_payload["goal"]
        assert "Release-Check-Tool" in goal_payload["goal"]
        assert "Maintainer" in goal_payload["goal"]
        assert "Nicht-Ziele" in goal_payload["goal"]
        assert len(res.get_json()["data"]["created_task_ids"]) >= 5
        workflow = res.get_json()["data"]["workflow"]["effective"]
        assert workflow["planning"]["use_repo_context"] is False
        assert workflow["verification"]["review_required"] is True
        assert workflow["policy"]["write_access"] == "confirmation_required"
        persisted_goal = goal_repo.get_by_id(goal_payload["id"])
        assert persisted_goal is not None
        assert "Keine unkontrollierte Vollautomatik" in persisted_goal.constraints[0]
        assert "Projekt-Blueprint" in persisted_goal.acceptance_criteria[0]
        tasks = task_repo.get_by_goal_id(goal_payload["id"])
        titles = {task.title for task in tasks}
        assert "Projekt-Blueprint erstellen" in titles
        assert "Initiales Task-Backlog erzeugen" in titles
        nodes = plan_node_repo.get_by_plan_id(res.get_json()["data"]["plan_id"])
        assert any((node.rationale or {}).get("artifact") == "projekt_blueprint" for node in nodes)
        assert any("Review" in ((node.rationale or {}).get("review_focus") or "") for node in nodes)
        detail = client.get(f"/goals/{goal_payload['id']}/detail", headers=admin_auth_header).get_json()["data"]
        planned_artifacts = detail["artifacts"]["planned_artifacts"]
        artifact_keys = {item["artifact"] for item in planned_artifacts}
        assert {"projekt_blueprint", "initial_backlog", "naechste_schritte"}.issubset(artifact_keys)
        assert detail["artifacts"]["reusable_artifacts"] == planned_artifacts

    def test_create_goal_from_project_evolution_mode(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "project_evolution",
                "mode_data": {
                    "change_goal": "Dashboard um Projektstartmodus erweitern",
                    "change_type": "feature_ausbau",
                    "affected_areas": "frontend-angular, agent/services",
                    "risk_level": "mittel",
                    "constraints": "Keine Worker-zu-Worker-Orchestrierung",
                },
            },
        )
        assert res.status_code == 201
        goal_payload = res.get_json()["data"]["goal"]
        assert "kontrollierte Weiterentwicklung" in goal_payload["goal"]
        assert "feature_ausbau" in goal_payload["goal"]
        assert "frontend-angular" in goal_payload["goal"]
        assert "Keine Worker-zu-Worker-Orchestrierung" in goal_payload["goal"]
        assert len(res.get_json()["data"]["created_task_ids"]) >= 5
        workflow = res.get_json()["data"]["workflow"]["effective"]
        assert workflow["planning"]["use_repo_context"] is True
        assert workflow["verification"]["mode"] == "risk_and_regression_review"
        assert workflow["artifacts"]["include_risk_view"] is True
        persisted_goal = goal_repo.get_by_id(goal_payload["id"])
        assert persisted_goal is not None
        assert "MODUSKONTEXT: Existierendes Softwareprojekt weiterentwickeln" in persisted_goal.context
        assert "frontend-angular, agent/services" in persisted_goal.context
        assert "Keine Worker-zu-Worker-Orchestrierung" in persisted_goal.constraints
        tasks = task_repo.get_by_goal_id(goal_payload["id"])
        descriptions = "\n".join(task.description or "" for task in tasks)
        assert "Risiko-, Diff- und Testsicht erstellen" in {task.title for task in tasks}
        assert "kleine, sequenzierte Tasks" in descriptions
        nodes = plan_node_repo.get_by_plan_id(res.get_json()["data"]["plan_id"])
        assert any((node.rationale or {}).get("artifact") == "risiko_test_review_plan" for node in nodes)
        assert any((node.rationale or {}).get("test_focus") for node in nodes)
        detail = client.get(f"/goals/{goal_payload['id']}/detail", headers=admin_auth_header).get_json()["data"]
        planned_artifacts = detail["artifacts"]["planned_artifacts"]
        assert any(item["artifact"] == "risiko_test_review_plan" and item["test_focus"] for item in planned_artifacts)
        assert any(item["artifact"] == "aenderungsplan" for item in planned_artifacts)
        assert "aktive Weiterentwicklung" in next(
            item for item in client.get("/goals/modes", headers=admin_auth_header).get_json()["data"] if item["id"] == "project_evolution"
        )["description"]

    def test_project_evolution_high_risk_uses_strict_review_defaults(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "project_evolution",
                "mode_data": {
                    "change_goal": "Persistenzschicht umbauen",
                    "risk_level": "hoch",
                },
            },
        )
        assert res.status_code == 201
        workflow = res.get_json()["data"]["workflow"]["effective"]
        assert workflow["policy"]["security_level"] == "strict_review"

    def test_create_goal_simple_flow_persists_goal_and_task_links(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Implement login feature"})
        assert res.status_code == 201
        payload = res.get_json()["data"]
        goal = payload["goal"]

        assert goal["status"] == "planned"
        assert payload["created_task_ids"]
        assert payload["plan_id"]
        assert payload["plan_node_ids"]
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal is not None
        assert persisted_goal.trace_id

        linked_tasks = task_repo.get_by_goal_id(goal["id"])
        assert linked_tasks
        assert all(task.goal_trace_id == persisted_goal.trace_id for task in linked_tasks)
        assert all(task.plan_id == payload["plan_id"] for task in linked_tasks)

    def test_create_goal_advanced_overrides_preserve_provenance(self, client, admin_auth_header):
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "goal": "Implement reporting feature",
                "team_id": "team-advanced",
                "create_tasks": False,
                "use_repo_context": False,
                "constraints": ["No breaking API changes"],
                "acceptance_criteria": ["Result must be documented"],
                "execution_preferences": {"verification_mode": "strict"},
                "visibility": {"show_plan": True},
                "workflow": {"policy": {"security_level": "strict"}},
            },
        )
        assert res.status_code == 201
        payload = res.get_json()["data"]
        goal = payload["goal"]
        workflow = payload["workflow"]

        assert payload["created_task_ids"] == []
        assert workflow["effective"]["routing"]["team_id"] == "team-advanced"
        assert workflow["provenance"]["planning.create_tasks"] == "override"
        assert workflow["provenance"]["planning.use_repo_context"] == "override"
        assert workflow["provenance"]["policy.security_level"] == "override"

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal.constraints == ["No breaking API changes"]
        assert persisted_goal.acceptance_criteria == ["Result must be documented"]
        assert persisted_goal.visibility["show_plan"] is True

    def test_get_goal_returns_task_count(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Create feature backlog"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]

        res = client.get(f"/goals/{goal_id}", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["id"] == goal_id
        assert payload["task_count"] >= 1

    def test_goal_create_accepts_instruction_selection_fields(self, client, user_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        profile_res = client.post(
            "/instruction-profiles",
            headers=user_auth_header,
            json={"name": "goal-input-profile", "prompt_content": "Use concise rationale."},
        )
        assert profile_res.status_code == 201
        profile_id = profile_res.get_json()["data"]["id"]

        overlay_res = client.post(
            "/instruction-overlays",
            headers=user_auth_header,
            json={
                "name": "goal-input-overlay",
                "prompt_content": "Prioritize acceptance criteria first.",
                "attachment_kind": "usage",
                "attachment_id": "project:goal-input",
            },
        )
        assert overlay_res.status_code == 201
        overlay_id = overlay_res.get_json()["data"]["id"]

        create_res = client.post(
            "/goals",
            headers=user_auth_header,
            json={
                "goal": "Validate goal input instruction integration",
                "create_tasks": False,
                "instruction_owner_username": "testuser",
                "instruction_profile_id": profile_id,
                "instruction_overlay_id": overlay_id,
            },
        )
        assert create_res.status_code == 201
        layers = create_res.get_json()["data"]["goal"]["instruction_layers"]
        assert layers["owner_username"] == "testuser"
        assert layers["profile_id"] == profile_id
        assert layers["overlay_id"] == overlay_id

    def test_goal_plan_inspection_and_patch(self, client, admin_auth_header):
        create_res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Implement reporting feature", "create_tasks": False},
        )
        goal_payload = create_res.get_json()["data"]
        goal_id = goal_payload["goal"]["id"]
        plan_id = goal_payload["plan_id"]
        node_id = goal_payload["plan_node_ids"][0]

        get_res = client.get(f"/goals/{goal_id}/plan", headers=admin_auth_header)
        assert get_res.status_code == 200
        plan_payload = get_res.get_json()["data"]
        assert plan_payload["plan"]["id"] == plan_id
        assert plan_payload["nodes"]

        patch_res = client.patch(
            f"/goals/{goal_id}/plan/nodes/{node_id}",
            headers=admin_auth_header,
            json={"title": "Adjusted step", "priority": "High"},
        )
        assert patch_res.status_code == 200
        patched = patch_res.get_json()["data"]
        assert patched["title"] == "Adjusted step"
        assert patched["status"] == "edited"

    def test_non_admin_goal_access_is_team_scoped(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
        open_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Public goal"})
        scoped_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Scoped goal", "team_id": "team-a"})

        token = jwt.encode(
            {
                "sub": "scoped-user",
                "role": "user",
                "team_id": "team-a",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        list_res = client.get("/goals", headers=headers)
        assert list_res.status_code == 200
        goal_ids = {item["id"] for item in list_res.get_json()["data"]}
        assert open_res.get_json()["data"]["goal"]["id"] in goal_ids
        assert scoped_res.get_json()["data"]["goal"]["id"] in goal_ids

        other_token = jwt.encode(
            {
                "sub": "other-user",
                "role": "user",
                "team_id": "team-b",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        other_headers = {"Authorization": f"Bearer {other_token}"}
        forbidden_res = client.get(f"/goals/{scoped_res.get_json()['data']['goal']['id']}", headers=other_headers)
        assert forbidden_res.status_code == 404

    def test_goal_detail_exposes_artifact_first_summary(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Deliver release"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        task_id = create_res.get_json()["data"]["created_task_ids"][0]

        client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={"task_id": task_id, "actor": "http://coder:5000", "gate_results": {"passed": True}, "output": "Release notes ready", "trace_id": goal_repo.get_by_id(goal_id).trace_id},
        )

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        payload = detail_res.get_json()["data"]
        assert payload["artifacts"]["result_summary"]["task_count"] >= 1
        assert payload["artifacts"]["headline_artifact"]["preview"] == "Release notes ready"

    def test_goal_detail_exposes_aggregated_cost_summary(self, client, admin_auth_header):
        create_res = client.post(
            "/goals/test/provision",
            headers=admin_auth_header,
            json={"goal": "Track release cost"},
        )
        assert create_res.status_code == 200
        goal_id = create_res.get_json()["data"]["id"]

        from agent.routes.tasks.utils import _update_local_task_status

        _update_local_task_status(
            "goal-cost-1",
            "completed",
            title="Task 1",
            goal_id=goal_id,
            task_kind="coding",
            history=[
                {
                    "event_type": "execution_result",
                    "cost_summary": {
                        "provider": "openai",
                        "model": "gpt-4",
                        "task_kind": "coding",
                        "tokens_total": 1200,
                        "cost_units": 1.8,
                        "latency_ms": 900,
                        "pricing_source": "openai:gpt-4",
                    },
                }
            ],
        )
        _update_local_task_status(
            "goal-cost-2",
            "failed",
            title="Task 2",
            goal_id=goal_id,
            task_kind="analysis",
            history=[
                {
                    "event_type": "execution_result",
                    "cost_summary": {
                        "provider": "openai",
                        "model": "gpt-4",
                        "task_kind": "analysis",
                        "tokens_total": 800,
                        "cost_units": 1.2,
                        "latency_ms": 700,
                        "pricing_source": "openai:gpt-4",
                    },
                }
            ],
        )

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        payload = detail_res.get_json()["data"]
        assert payload["cost_summary"]["total_cost_units"] == 3.0
        assert payload["cost_summary"]["total_tokens"] == 2000
        assert payload["cost_summary"]["tasks_with_cost"] == 2
        assert payload["artifacts"]["result_summary"]["cost_summary"]["total_cost_units"] == 3.0
        task_costs = {item["id"]: item["cost_summary"]["cost_units"] for item in payload["tasks"]}
        assert task_costs["goal-cost-1"] == 1.8
        assert task_costs["goal-cost-2"] == 1.2

    def test_goal_python_e2e_runs_planning_and_execution_without_frontend(
        self, client, app, admin_auth_header, monkeypatch
    ):
        _mock_goal_planning_llm(monkeypatch)

        create_res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "goal": "Build a small Python-only goal flow test",
                "team_id": "team-goal-e2e",
                "use_template": False,
                "use_repo_context": False,
                "create_tasks": True,
            },
        )
        assert create_res.status_code == 201, create_res.get_json()
        payload = create_res.get_json()["data"]
        goal_id = payload["goal"]["id"]
        created_ids = payload["created_task_ids"]
        assert len(created_ids) == 1

        monkeypatch.setattr(settings, "role", "hub")
        autonomous_loop.stop(persist=False)

        with app.app_context():
            agent_repo.save(
                AgentInfoDB(
                    url="http://worker-goal:5000",
                    name="worker-goal",
                    role="worker",
                    token="tok-goal",
                    status="online",
                )
            )

        def _fake_forward(worker_url, endpoint, data, token=None):
            if endpoint.endswith("/step/propose"):
                return {"status": "success", "data": {"reason": "execute goal task", "command": "echo ok"}}
            if endpoint.endswith("/step/execute"):
                return {
                    "status": "success",
                    "data": {"status": "completed", "exit_code": 0, "output": "execution success ok"},
                }
            raise AssertionError(endpoint)

        monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)

        with app.app_context():
            for _ in range(len(created_ids) + 1):
                autonomous_loop.tick_once()

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["goal"]["id"] == goal_id
        assert detail["trace"]["task_ids"]

        for tid in created_ids:
            task = _get_local_task_status(tid)
            assert task is not None
            assert task["status"] == "completed"
            assert task["goal_id"] == goal_id

        assert detail["artifacts"]["result_summary"]["completed_tasks"] == len(created_ids)
        assert detail["artifacts"]["headline_artifact"]["preview"] == "execution success ok"

    def test_goal_first_run_happy_path_uses_default_configuration(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        monkeypatch.setattr(settings, "hub_can_be_worker", True)
        monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Bootstrap first run"})
        assert res.status_code == 201
        payload = res.get_json()["data"]
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"
        assert payload["workflow"]["effective"]["routing"]["mode"] == "active_team_or_hub_default"
        assert payload["readiness"]["happy_path_ready"] is True
        goal_id = payload["goal"]["id"]

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["trace"]["trace_id"].startswith("goal-")
        assert detail["plan"]["plan"]["goal_id"] == goal_id
        product_actions = [log.action for log in audit_repo.get_all(limit=20)]
        assert "product_product_flow_started" in product_actions
        assert "product_goal_created" in product_actions
        assert "product_goal_planning_succeeded" in product_actions

    def test_create_goal_requires_planning_backend_when_templates_disabled(self, client, admin_auth_header, monkeypatch):
        cfg = dict(client.application.config.get("AGENT_CONFIG", {}) or {})
        llm_cfg = dict(cfg.get("llm_config", {}) or {})
        llm_cfg["provider"] = ""
        cfg["llm_config"] = llm_cfg
        monkeypatch.setitem(client.application.config, "AGENT_CONFIG", cfg)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Hard planner test", "use_template": False, "use_repo_context": False},
        )
        assert res.status_code == 412
        assert res.get_json()["message"] == "planning_backend_unavailable"

    def test_non_admin_cannot_override_policy_security(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
        base_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Base"})
        assert base_res.status_code == 201

        token = jwt.encode(
            {
                "sub": "policy-user",
                "role": "user",
                "team_id": "team-a",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}
        res = client.post(
            "/goals",
            headers=headers,
            json={"goal": "Try policy override", "workflow": {"policy": {"security_level": "strict"}}},
        )
        assert res.status_code == 403
        assert res.get_json()["message"] == "policy_override_requires_admin"
