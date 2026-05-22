import time
from types import SimpleNamespace

import jwt

from agent.config import settings
from agent.db_models import AgentInfoDB, GoalDB, TaskDB
from agent.repository import agent_repo, audit_repo, goal_repo, plan_node_repo, task_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.utils import _get_local_task_status


def _mock_goal_planning_llm(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan release","description":"Prepare release artifacts","priority":"High"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)


def _wait_goal_status(client, headers, goal_id: str, *, timeout_s: float = 5.0) -> str:
    deadline = time.time() + timeout_s
    status = "unknown"
    while time.time() < deadline:
        payload = client.get(f"/goals/{goal_id}", headers=headers).get_json()["data"]
        status = str(payload.get("status") or "unknown")
        if status not in {"planning_queued", "planning_running", "planning"}:
            return status
        time.sleep(0.05)
    return status


def test_plan_quality_from_task_ids_uses_task_kind_field(monkeypatch) -> None:
    from agent.routes.tasks.goals import _plan_quality_from_task_ids

    class _Repo:
        @staticmethod
        def get_by_id(_tid):
            return SimpleNamespace(
                title="Implement endpoint",
                description="Implement API endpoint in Python code.",
                task_kind="coding",
            )

    class _Repos:
        task_repo = _Repo()

    monkeypatch.setattr("agent.routes.tasks.goals._repos", lambda: _Repos())
    ok, reason = _plan_quality_from_task_ids(
        task_ids=["t1", "t2", "t3"],
        mode="generic",
        planning_policy={},
        team_id=None,
    )
    assert ok is True
    assert reason == "ok"


def test_soft_planning_quality_failure_allows_generic_task_overflow() -> None:
    from agent.routes.tasks.goals import _is_soft_planning_quality_failure

    assert _is_soft_planning_quality_failure(
        quality_reason="too_many_generic_tasks:4/0"
    ) is True
    assert _is_soft_planning_quality_failure(
        quality_reason="missing_categories:review:0/1|too_many_generic_tasks:2/0"
    ) is True


def test_planning_slot_capacity_reads_config(app) -> None:
    from agent.routes.tasks.goals import _planning_slot_capacity_from_config

    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "planning_policy": {"parallel_goal_planning_max_concurrency": 3},
    }
    with app.app_context():
        assert _planning_slot_capacity_from_config() == 3


def test_planning_slots_respect_capacity_one(app) -> None:
    from agent.routes.tasks.goals import _acquire_planning_slot, _release_planning_slot

    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "planning_policy": {"parallel_goal_planning_max_concurrency": 1},
    }
    with app.app_context():
        first, cap = _acquire_planning_slot(timeout_s=1)
        second, _ = _acquire_planning_slot(timeout_s=1)
        try:
            assert first is True
            assert cap == 1
            assert second is False
        finally:
            if first:
                _release_planning_slot()


def test_planning_slots_use_explicit_capacity_override() -> None:
    from agent.routes.tasks.goals import _acquire_planning_slot, _release_planning_slot

    first = second = third = False
    try:
        first, cap = _acquire_planning_slot(timeout_s=1, capacity=2)
        second, _ = _acquire_planning_slot(timeout_s=1, capacity=2)
        third, _ = _acquire_planning_slot(timeout_s=1, capacity=2)
        assert first is True
        assert second is True
        assert third is False
        assert cap == 2
    finally:
        if second:
            _release_planning_slot()
        if first:
            _release_planning_slot()


class TestGoalsAPI:
    def test_goal_purge_deletes_goal_and_tasks(self, client, admin_auth_header, monkeypatch):
        goal = goal_repo.save(
            GoalDB(
                goal="purge me",
                summary="purge me",
                status="planned",
                source="test",
                requested_by="admin",
            )
        )
        task_repo.save(
            TaskDB(
                id="purge-task-1",
                title="t1",
                status="todo",
                goal_id=goal.id,
                goal_trace_id=goal.trace_id,
            )
        )
        monkeypatch.setattr(
            "agent.services.goal_purge_service.get_prompt_trace_service",
            lambda: SimpleNamespace(delete_by_goal_id=lambda _gid: 0),
        )
        monkeypatch.setattr(
            "agent.services.goal_purge_service.get_task_admin_service",
            lambda: SimpleNamespace(intervene_task=lambda **_kwargs: (True, "ok", {})),
        )

        res = client.delete(f"/goals/{goal.id}/purge", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert payload["goal_id"] == goal.id
        assert int(payload["deleted"].get("goal") or 0) == 1
        assert int(payload["deleted"].get("tasks") or 0) >= 1
        assert int((payload.get("task_cancel_summary") or {}).get("attempted") or 0) >= 1
        assert goal_repo.get_by_id(goal.id) is None
        assert task_repo.get_by_id("purge-task-1") is None

    def test_goal_purge_cancels_all_tasks_before_delete(self, client, admin_auth_header, monkeypatch):
        goal = goal_repo.save(
            GoalDB(
                goal="purge cancel all",
                summary="purge cancel all",
                status="running",
                source="test",
                requested_by="admin",
            )
        )
        task_repo.save(TaskDB(id="purge-task-a", title="a", status="running", goal_id=goal.id, goal_trace_id=goal.trace_id))
        task_repo.save(TaskDB(id="purge-task-b", title="b", status="todo", goal_id=goal.id, goal_trace_id=goal.trace_id))

        cancelled: list[str] = []

        def _intervene_task(**kwargs):
            cancelled.append(str(kwargs.get("task_id") or ""))
            return True, "ok", {}

        monkeypatch.setattr(
            "agent.services.goal_purge_service.get_task_admin_service",
            lambda: SimpleNamespace(intervene_task=_intervene_task),
        )
        monkeypatch.setattr(
            "agent.services.goal_purge_service.get_prompt_trace_service",
            lambda: SimpleNamespace(delete_by_goal_id=lambda _gid: 0),
        )

        res = client.delete(f"/goals/{goal.id}/purge", headers=admin_auth_header)
        assert res.status_code == 200
        payload = res.get_json()["data"]
        assert set(cancelled) == {"purge-task-a", "purge-task-b"}
        assert int(payload["task_cancel_summary"]["attempted"]) == 2
        assert int(payload["task_cancel_summary"]["succeeded"]) == 2

    def test_goal_purge_returns_already_deleted_for_unknown_goal(self, client, admin_auth_header):
        # Purge is idempotent: an already-deleted (or never-existing) goal returns 200.
        res = client.delete("/goals/not-a-goal/purge", headers=admin_auth_header)
        assert res.status_code == 200
        data = res.get_json()["data"]
        assert data["already_deleted"] is True
        assert data["goal_id"] == "not-a-goal"

    def test_generic_software_goal_soft_quality_miss_does_not_fail(self, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr(
            "agent.routes.tasks.auto_planner.auto_planner.plan_goal",
            lambda **_kwargs: {"subtasks": [{"title": "x", "description": "y"}], "created_task_ids": ["task-soft"], "plan_id": "plan-soft"},
        )
        monkeypatch.setattr(
            "agent.routes.tasks.goals._plan_quality_from_task_ids",
            lambda **_kwargs: (False, "missing_categories:analysis:0/1,review:0/1"),
        )

        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Create a Python Fibonacci backend API with tests and run commands"},
        )
        assert res.status_code in (201, 202)
        goal_id = res.get_json()["data"]["goal"]["id"]
        status = _wait_goal_status(client, admin_auth_header, goal_id)
        assert status in {"planned", "running", "completed", "queued"}

    def test_generic_software_goal_hard_quality_fail_still_fails(self, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr(
            "agent.routes.tasks.auto_planner.auto_planner.plan_goal",
            lambda **_kwargs: {"subtasks": [{"title": "x", "description": "y"}], "created_task_ids": ["task-hard"], "plan_id": "plan-hard"},
        )
        monkeypatch.setattr(
            "agent.routes.tasks.goals._plan_quality_from_task_ids",
            lambda **_kwargs: (False, "too_few_tasks:1/5|missing_categories:implementation:0/1"),
        )

        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Create a Python Fibonacci backend API with tests and run commands"},
        )
        assert res.status_code in (201, 202)
        goal_id = res.get_json()["data"]["goal"]["id"]
        status = _wait_goal_status(client, admin_auth_header, goal_id)
        assert status == "failed"
        goal_payload = client.get(f"/goals/{goal_id}", headers=admin_auth_header).get_json()["data"]
        assert goal_payload["execution_preferences"]["last_status_reason"] == "planning_insufficient_task_detail"

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
        assert res.status_code in (201, 202)
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
        assert res.status_code in (201, 202)
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
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["success_weighted_recommendations"]["schema"] == "deterministic_success_weighted_recommendation_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["llm_escalation_decision"]["schema"] == "deterministic_llm_escalation_decision_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["path_visibility"]["schema"] == "deterministic_path_visibility_v1"
        assert persisted_goal.mode_data["deterministic_repair_foundation"]["rollout_plan"]["schema"] == "deterministic_repair_rollout_plan_v1"
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

    def test_create_goal_starts_autopilot_even_without_created_tasks(self, client, admin_auth_header, monkeypatch):
        calls: list[dict] = []

        def _fake_plan_goal(**_kwargs):
            return {"subtasks": [], "created_task_ids": [], "plan_id": "plan-empty", "plan_node_ids": []}

        def _fake_start(**kwargs):
            calls.append(kwargs)
            return {"running": True}

        monkeypatch.setattr("agent.routes.tasks.auto_planner.auto_planner.plan_goal", _fake_plan_goal)
        monkeypatch.setattr("agent.services.autopilot_runtime_service.AutopilotRuntimeService.start", lambda self, **kwargs: _fake_start(**kwargs))

        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Small goal without immediate tasks"})
        assert res.status_code in (201, 202)
        assert len(calls) == 1
        assert calls[0].get("goal")

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
        assert res.status_code in (201, 202)
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
        assert any("Referenzprofile dienen nur als Guidance" in item for item in persisted_goal.constraints)
        assert "Projekt-Blueprint" in persisted_goal.acceptance_criteria[0]
        assert "Referenzprofil-Auswahl und Auswahlgrund" in persisted_goal.acceptance_criteria[2]
        assert "REFERENZKONTEXT" in persisted_goal.context
        reference_plan = persisted_goal.mode_data["reference_profile_plan"]
        selected_profile = reference_plan["selection"]["selected_profile"]
        assert selected_profile["profile_id"] in {"ref.python.ananta_backend", "ref.angular.ananta_frontend"}
        assert reference_plan["selection"]["selected_reason"]["summary"]
        assert workflow["routing"]["reference_profile_id"] == selected_profile["profile_id"]
        assert workflow["planning"]["blueprint_hint"] == reference_plan["integration_hints"]["blueprint_name"]
        assert workflow["routing"]["work_profile"] == reference_plan["integration_hints"]["work_profile"]
        assert reference_plan["skeleton_guidance"]["guidance_lines"]

        assert goal_payload["reference_profile"]["profile_id"] == selected_profile["profile_id"]
        assert goal_payload["reference_profile"]["audit_marker"]["task_or_goal_id"] == goal_payload["id"]
        assert any(log.action == "reference_profile_used" for log in audit_repo.get_all(limit=40))
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

    def test_create_goal_new_project_uses_payload_goal_as_project_idea_fallback(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        raw_goal = "Create a real Fibonacci backend in Python with API and tests"
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "new_software_project",
                "goal": raw_goal,
                "mode_data": {
                    "target_users": "Developers",
                    "platform": "API",
                },
            },
        )
        assert res.status_code in (201, 202)
        goal_payload = res.get_json()["data"]["goal"]
        assert "Fibonacci backend" in goal_payload["goal"]

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
        assert res.status_code in (201, 202)
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
        assert workflow["routing"]["reference_profile_id"] in {
            "ref.python.ananta_backend",
            "ref.angular.ananta_frontend",
            "ref.java.keycloak",
        }
        persisted_goal = goal_repo.get_by_id(goal_payload["id"])
        assert persisted_goal is not None
        assert "MODUSKONTEXT: Existierendes Softwareprojekt weiterentwickeln" in persisted_goal.context
        assert "Referenzprofil-Empfehlung" in persisted_goal.context
        assert "Reference-Fit-Diagnose" in persisted_goal.context
        assert "frontend-angular, agent/services" in persisted_goal.context
        assert "Keine Worker-zu-Worker-Orchestrierung" in persisted_goal.constraints
        assert any("Referenzprofile bleiben advisory" in item for item in persisted_goal.constraints)
        assert "Referenzprofil-Empfehlung, Hinweise und Fit-Diagnose" in persisted_goal.acceptance_criteria[2]
        reference_plan = persisted_goal.mode_data["reference_profile_plan"]
        assert reference_plan["selection"]["selected_profile"]["profile_id"] == goal_payload["reference_profile"]["profile_id"]
        assert reference_plan["evolution_hints"]["actionable_hints"]
        assert reference_plan["mismatch_diagnostics"]["fit_level"] in {"high_fit", "partial_fit", "low_fit"}
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
        mode_entry = next(item for item in client.get("/goals/modes", headers=admin_auth_header).get_json()["data"] if item["id"] == "project_evolution")
        assert "aktive Weiterentwicklung" in mode_entry["description"]
        field_names = {field.get("name") for field in mode_entry["fields"]}
        assert "reference_profile_id" in field_names

    def test_project_evolution_reference_mismatch_diagnostics_visible(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={
                "mode": "project_evolution",
                "mode_data": {
                    "change_goal": "Refactor backend policy execution pipeline",
                    "affected_areas": "agent/services, agent/routes/tasks",
                    "risk_level": "mittel",
                    "reference_profile_id": "ref.angular.ananta_frontend",
                },
            },
        )
        assert res.status_code in (201, 202)
        goal_payload = res.get_json()["data"]["goal"]
        assert goal_payload["reference_profile"]["fit_level"] == "low_fit"
        assert "frontend_profile_for_backend_change" in goal_payload["reference_profile"]["mismatch_signals"]

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


# APR-001: Planning recovery diagnostics visible in goal detail
def test_goal_detail_shows_planning_recovery_when_present(client, admin_auth_header, monkeypatch, app):
    from agent.repository import goal_repo
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan","description":"Do it","priority":"Medium"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Recovery visibility test"},
    )
    assert res.status_code == 201
    goal_id = res.get_json()["data"]["goal"]["id"]

    # Manually inject planning_recovery into execution_preferences
    with app.app_context():
        goal = goal_repo.get_by_id(goal_id)
        prefs = dict(goal.execution_preferences or {})
        prefs["planning_recovery"] = {
            "attempts": 1,
            "last_reason": "stalled_planning_no_tasks",
            "last_attempt_at": 1234567890.0,
        }
        goal.execution_preferences = prefs
        goal_repo.save(goal)

    res2 = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
    assert res2.status_code == 200
    data = res2.get_json()["data"]
    recovery = data.get("planning_recovery") or {}
    assert recovery["attempts"] == 1
    assert recovery["last_reason"] == "stalled_planning_no_tasks"


def test_goal_detail_planning_recovery_none_when_no_recovery_occurred(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan","description":"Do it","priority":"Medium"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "No recovery goal"},
    )
    assert res.status_code == 201
    goal_id = res.get_json()["data"]["goal"]["id"]

    res2 = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
    assert res2.status_code == 200
    data = res2.get_json()["data"]
    assert data.get("planning_recovery") is None


# APR-002: Service-level recovery method testable independently of Flask route
def test_recover_stalled_planning_goal_increments_attempts(app):
    from agent.services.lifecycle_service import get_goal_lifecycle_service

    with app.app_context():
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        goal = GoalDB(
            goal="Test stalled goal",
            status="planning",
            summary="test",
            updated_at=0.0,  # stale
        )
        goal = goal_repo.save(goal)
        svc = get_goal_lifecycle_service()
        result = svc.recover_stalled_planning_goal(goal)
        prefs = dict(result.execution_preferences or {})
        recovery = prefs.get("planning_recovery") or {}
        assert int(recovery.get("attempts") or 0) == 1


def test_recover_stalled_planning_goal_caps_at_two_attempts(app):
    from agent.services.lifecycle_service import get_goal_lifecycle_service

    with app.app_context():
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        goal = GoalDB(
            goal="Capped recovery",
            status="planning",
            summary="test",
            updated_at=0.0,
            execution_preferences={"planning_recovery": {"attempts": 2}},
        )
        goal = goal_repo.save(goal)
        svc = get_goal_lifecycle_service()
        result = svc.recover_stalled_planning_goal(goal)
        prefs = dict(result.execution_preferences or {})
        recovery = prefs.get("planning_recovery") or {}
        # Should stay at 2, not increment further
        assert int(recovery.get("attempts") or 0) == 2


def test_recover_stalled_planning_goal_skips_non_planning_status(app):
    from agent.services.lifecycle_service import get_goal_lifecycle_service

    with app.app_context():
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        goal = GoalDB(
            goal="Planned goal",
            status="planned",
            summary="test",
            updated_at=0.0,
        )
        goal = goal_repo.save(goal)
        svc = get_goal_lifecycle_service()
        result = svc.recover_stalled_planning_goal(goal)
        prefs = dict(result.execution_preferences or {})
        # Should not touch planning_recovery for non-planning goals
        assert prefs.get("planning_recovery") is None


def test_recover_stalled_planning_goal_no_tasks_first_attempt_stays_planning(app, monkeypatch):
    from agent.services.lifecycle_service import get_goal_lifecycle_service

    with app.app_context():
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        goal = GoalDB(
            goal="Recoverable stalled goal",
            status="planning",
            summary="test",
            updated_at=0.0,
        )
        goal = goal_repo.save(goal)
        from agent.routes.tasks import auto_planner as ap_module
        monkeypatch.setattr(
            ap_module.auto_planner,
            "plan_goal",
            lambda **kwargs: {"created_task_ids": [], "subtasks": []},
        )
        svc = get_goal_lifecycle_service()
        result = svc.recover_stalled_planning_goal(goal)
        assert str(result.status) == "planning"
        prefs = dict(result.execution_preferences or {})
        assert str(prefs.get("last_status_reason") or "") == "planning_recovery_retry_scheduled"


def test_recover_stalled_planning_goal_no_tasks_second_attempt_fails(app, monkeypatch):
    from agent.services.lifecycle_service import get_goal_lifecycle_service

    with app.app_context():
        from agent.db_models import GoalDB
        from agent.repository import goal_repo
        now = time.time()
        goal = GoalDB(
            goal="Recoverable stalled goal second attempt",
            status="planning",
            summary="test",
            updated_at=0.0,
            execution_preferences={"planning_recovery": {"attempts": 1, "last_attempt_at": now - 120}},
        )
        goal = goal_repo.save(goal)
        from agent.routes.tasks import auto_planner as ap_module
        monkeypatch.setattr(
            ap_module.auto_planner,
            "plan_goal",
            lambda **kwargs: {"created_task_ids": [], "subtasks": []},
        )
        svc = get_goal_lifecycle_service()
        result = svc.recover_stalled_planning_goal(goal)
        assert str(result.status) == "failed"
        prefs = dict(result.execution_preferences or {})
        assert str(prefs.get("last_status_reason") or "") == "planning_recovery_no_tasks_created"
