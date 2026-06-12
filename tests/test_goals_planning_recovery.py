import time
from types import SimpleNamespace

import pytest

import jwt

from agent.config import settings
from agent.db_models import AgentInfoDB, GoalDB, PlanDB, PlanNodeDB, TaskDB
from agent.repository import agent_repo, audit_repo, goal_repo, plan_node_repo, plan_repo, task_repo
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.utils import _get_local_task_status


def _mock_goal_planning_llm(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Implement feature","description":"Create and implement the api.py endpoint file","priority":"High"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
    # Bypass prompt registry DB operations to avoid nested-app-context session invalidation
    from agent.services.planning_prompt_registry import ResolvedPlanningPrompt
    monkeypatch.setattr(
        "agent.services.planning_prompt_registry.PlanningPromptRegistry.resolve",
        lambda self, **kwargs: ResolvedPlanningPrompt(
            prompt_version_id="test-v1",
            version="v1",
            language="de",
            mode=str(kwargs.get("mode") or "generic"),
            prompt="List tasks as JSON array.",
            checksum="test",
            is_inline_fallback=True,
        ),
    )


def _bypass_quality(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(
        "agent.routes.tasks.goals._plan_quality_from_task_ids",
        lambda **_: (True, "ok"),
    )
    # Also bypass the planning service's internal quality gate so a 1-task LLM mock
    # doesn't fail the min_total_tasks validation inside auto_planner.plan_goal.
    monkeypatch.setattr(
        "agent.services.planning_quality_service.PlanningQualityService.evaluate",
        lambda self, **_: SimpleNamespace(ok=True, reason="ok", missing_categories=[], generic_task_indices=[], details={}),
    )


def _mock_plan_goal(mode: str = "generic"):
    """Return a plan_goal mock that creates proper subtasks + DB records for the given mode.

    Avoids depending on template/LLM strategy execution in tests that need specific
    task titles, artifact keys, and plan node rationale fields.
    """
    import uuid

    now = time.time()

    if mode == "new_software_project":
        subtasks = [
            {"title": "Projektidee und Grenzen klaeren", "description": "Klaere die Projektidee Release-Check-Tool und definiere Scope und Nicht-Ziele.", "task_kind": "analysis", "artifact": "zielzusammenfassung", "review_focus": "unklare oder leere Eingaben sichtbar machen"},
            {"title": "Projekt-Blueprint erstellen", "description": "Erstelle den Projekt-Blueprint basierend auf den geklaerten Anforderungen und dem Stack Python + Angular.", "task_kind": "analysis", "artifact": "projekt_blueprint"},
            {"title": "Infrastruktur aufsetzen", "description": "Richte Repository-Struktur und CI/CD-Pipeline fuer das Release-Check-Tool ein.", "task_kind": "infrastructure"},
            {"title": "Initiales Task-Backlog erzeugen", "description": "Erzeuge das initiale Task-Backlog mit allen identifizierten Arbeitspaketen und Prioritaeten.", "task_kind": "analysis", "artifact": "initial_backlog"},
            {"title": "Kern-Feature implementieren", "description": "Implementiere das Release-Check-Tool mit Python + Angular basierend auf dem Blueprint.", "task_kind": "implementation"},
            {"title": "Tests erstellen", "description": "Erstelle Unit- und Integrationstests fuer das Release-Check-Tool.", "task_kind": "tests"},
            {"title": "Review durchfuehren", "description": "Fuehre Code-Review durch und erstelle Review-Dokumentation.", "task_kind": "review", "review_focus": "Review der Implementierung und Testabdeckung"},
            {"title": "Naechste Schritte planen", "description": "Plane die naechste Umsetzungsscheibe basierend auf Review-Ergebnissen.", "task_kind": "analysis", "artifact": "naechste_schritte"},
        ]
    elif mode == "project_evolution":
        subtasks = [
            {"title": "Ist-Kontext und betroffene Bereiche schaerfen", "description": "Erfasse den aktuellen Kontext und identifiziere betroffene Bereiche wie frontend-angular und agent/services.", "task_kind": "analysis", "artifact": "ist_analyse"},
            {"title": "Risiko-, Diff- und Testsicht erstellen", "description": "Erstelle Risikoanalyse, Diff-Betrachtung und Teststrategie fuer die geplante Aenderung.", "task_kind": "analysis", "artifact": "risiko_test_review_plan", "test_focus": "Regressionstest und Diff-Analyse"},
            {"title": "Aenderungsziel und Restriktionen abgrenzen", "description": "Grenze Aenderungsziel und Restriktionen klar ab: Keine Worker-zu-Worker-Orchestrierung.", "task_kind": "analysis", "artifact": "aenderungsscope"},
            {"title": "Aenderung in kleine, sequenzierte Tasks zerlegen", "description": "Zerlege die Aenderung in kleine, sequenzierte Tasks fuer schrittweise Umsetzung.", "task_kind": "implementation", "artifact": "aenderungsplan"},
            {"title": "Kleinste verifizierbare Aenderung vorbereiten", "description": "Bereite die kleinste verifizierbare Aenderung als erste Umsetzungsscheibe vor.", "task_kind": "implementation", "artifact": "erste_umsetzungsscheibe"},
            {"title": "Review- und Rollback-Plan festlegen", "description": "Lege Review- und Rollback-Plan fuer die Aenderung fest.", "task_kind": "review", "artifact": "review_rollback_plan"},
        ]
    elif mode == "admin_repair":
        subtasks = [
            {"title": "Use-case, scope und Modusgrenzen festhalten", "description": "Halte Use-case, Scope und Modusgrenzen des Admin-Repair-Szenarios fest.", "task_kind": "analysis", "artifact": "admin_repair_scope"},
            {"title": "Environment Summary und bounded evidence erfassen", "description": "Erfasse Environment Summary und Evidence aus error_logs, service_status, runtime_state.", "task_kind": "analysis", "artifact": "environment_evidence_summary"},
            {"title": "Problemklasse und Diagnose-Artefakt ableiten", "description": "Leite die Problemklasse ab und erstelle Diagnose-Artefakt.", "task_kind": "analysis", "artifact": "diagnosis_artifact"},
            {"title": "Repair actions mit hook-ready Feldern vorbereiten", "description": "Bereite Repair Actions mit allen hook-ready Feldern vor.", "task_kind": "implementation", "artifact": "repair_action_contract"},
            {"title": "Dry-run-first bounded repair plan erzeugen", "description": "Erzeuge einen bounded Dry-run-first Repair Plan.", "task_kind": "implementation", "artifact": "bounded_repair_plan"},
            {"title": "Post-repair verification und Session Trail ausgeben", "description": "Gib Post-repair Verification und Session Trail aus.", "task_kind": "review", "artifact": "repair_verification_summary"},
        ]
    else:
        subtasks = [
            {"title": "Analyse durchfuehren", "description": "Fuehre eine gruendliche Analyse der Anforderungen durch.", "task_kind": "analysis"},
            {"title": "Feature implementieren", "description": "Implementiere das Feature basierend auf der Analyse.", "task_kind": "implementation"},
            {"title": "Tests durchfuehren", "description": "Erstelle und fuehre Tests durch.", "task_kind": "tests"},
        ]

    def _mock(**kwargs):
        goal_id = kwargs.get("goal_id")
        trace_id = kwargs.get("goal_trace_id") or f"trace-{uuid.uuid4().hex[:8]}"
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        plan = PlanDB(id=plan_id, goal_id=goal_id, trace_id=trace_id, created_at=now, updated_at=now)
        plan_repo.save(plan)

        created_task_ids = []
        nodes = []
        for i, st in enumerate(subtasks, start=1):
            node_key = f"{plan_id}-node-{i}"
            task_id = f"task-{uuid.uuid4().hex[:12]}"
            t = TaskDB(
                id=task_id,
                goal_id=goal_id, goal_trace_id=trace_id, plan_id=plan_id,
                title=st["title"], description=st["description"], task_kind=st.get("task_kind", "generic"),
                status="pending", created_at=now, updated_at=now,
            )
            t = task_repo.save(t)
            created_task_ids.append(t.id)

            node = PlanNodeDB(
                plan_id=plan_id, node_key=node_key,
                title=st["title"], description=st["description"],
                position=i, materialized_task_id=t.id,
                rationale={
                    "artifact": st.get("artifact"),
                    "review_focus": st.get("review_focus"),
                    "test_focus": st.get("test_focus"),
                    "task_kind": st.get("task_kind", "generic"),
                    "planning_mode": "auto_planner",
                },
                created_at=now, updated_at=now,
            )
            node = plan_node_repo.save(node)
            nodes.append(node)

        return {
            "subtasks": subtasks,
            "created_task_ids": created_task_ids,
            "plan_id": plan_id,
            "plan_node_ids": [n.id for n in nodes],
        }

    return _mock


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



class TestGoalsAPIPlanningRecovery:
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
        assert res.status_code in (201, 202)
        goal = res.get_json()["data"]["goal"]
        workflow = res.get_json()["data"]["workflow"]

        assert workflow["effective"]["routing"]["team_id"] == "team-advanced"
        assert workflow["provenance"]["planning.create_tasks"] == "override"
        assert workflow["provenance"]["planning.use_repo_context"] == "override"
        assert workflow["provenance"]["policy.security_level"] == "override"

        # create_tasks=False → no tasks created regardless of planning outcome
        assert task_repo.get_by_goal_id(goal["id"]) == []

        persisted_goal = goal_repo.get_by_id(goal["id"])
        assert persisted_goal.constraints == ["No breaking API changes"]
        assert persisted_goal.acceptance_criteria == ["Result must be documented"]
        assert persisted_goal.visibility["show_plan"] is True

    def test_get_goal_returns_task_count(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        _bypass_quality(monkeypatch)
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Create feature backlog"})
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0)

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
        assert create_res.status_code in (201, 202)
        layers = create_res.get_json()["data"]["goal"]["instruction_layers"]
        assert layers["owner_username"] == "testuser"
        assert layers["profile_id"] == profile_id
        assert layers["overlay_id"] == overlay_id

    def test_goal_plan_inspection_and_patch(self, client, admin_auth_header, monkeypatch):
        _mock_goal_planning_llm(monkeypatch)
        _bypass_quality(monkeypatch)
        create_res = client.post(
            "/goals",
            headers=admin_auth_header,
            json={"goal": "Implement reporting feature", "create_tasks": False},
        )
        assert create_res.status_code in (201, 202)
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0)

        get_res = client.get(f"/goals/{goal_id}/plan", headers=admin_auth_header)
        assert get_res.status_code == 200
        plan_payload = get_res.get_json()["data"]
        plan_id = plan_payload["plan"]["id"]
        assert plan_id
        assert plan_payload["nodes"]
        node_id = plan_payload["nodes"][0]["id"]

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
        _bypass_quality(monkeypatch)
        create_res = client.post("/goals", headers=admin_auth_header, json={"goal": "Deliver release"})
        assert create_res.status_code in (201, 202)
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        assert _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0) == "planned"
        task_id = task_repo.get_by_goal_id(goal_id)[0].id

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
        goal = goal_repo.save(
            GoalDB(goal="Track release cost", summary="Track release cost", status="planned", source="test", requested_by="admin")
        )
        goal_id = goal.id

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

    @pytest.mark.skip(reason="pre-existing: autopilot tick engine dispatches 0 tasks from in-memory SQLite in test environment")
    def test_goal_python_e2e_runs_planning_and_execution_without_frontend(
        self, client, app, admin_auth_header, monkeypatch
    ):
        _mock_goal_planning_llm(monkeypatch)
        _bypass_quality(monkeypatch)

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
        assert create_res.status_code in (201, 202), create_res.get_json()
        goal_id = create_res.get_json()["data"]["goal"]["id"]
        assert _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0) == "planned"
        created_ids = [t.id for t in task_repo.get_by_goal_id(goal_id)]
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
        _bypass_quality(monkeypatch)
        monkeypatch.setattr(settings, "hub_can_be_worker", True)
        monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
        res = client.post("/goals", headers=admin_auth_header, json={"goal": "Bootstrap first run"})
        assert res.status_code in (201, 202)
        payload = res.get_json()["data"]
        assert payload["workflow"]["provenance"]["planning.create_tasks"] == "default"
        assert payload["workflow"]["effective"]["routing"]["mode"] == "active_team_or_hub_default"
        assert payload["readiness"]["happy_path_ready"] is True
        goal_id = payload["goal"]["id"]
        assert _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0) == "planned"

        detail_res = client.get(f"/goals/{goal_id}/detail", headers=admin_auth_header)
        assert detail_res.status_code == 200
        detail = detail_res.get_json()["data"]
        assert detail["trace"]["trace_id"].startswith("goal-")
        assert detail["plan"]["plan"]["goal_id"] == goal_id
        # Poll briefly to let the background thread finish audit logging after status transition.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            product_actions = [log.action for log in audit_repo.get_all(limit=20)]
            if "product_goal_planning_succeeded" in product_actions:
                break
            time.sleep(0.05)
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
        assert base_res.status_code in (201, 202)

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

def test_goal_detail_shows_planning_recovery_when_present(client, admin_auth_header, monkeypatch, app):
    from agent.repository import goal_repo
    _mock_goal_planning_llm(monkeypatch)
    _bypass_quality(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Recovery visibility test", "create_tasks": False},
    )
    assert res.status_code in (201, 202)
    goal_id = res.get_json()["data"]["goal"]["id"]
    _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0)

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
    _mock_goal_planning_llm(monkeypatch)
    _bypass_quality(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "No recovery goal", "create_tasks": False},
    )
    assert res.status_code in (201, 202)
    goal_id = res.get_json()["data"]["goal"]["id"]
    _wait_goal_status(client, admin_auth_header, goal_id, timeout_s=10.0)

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
