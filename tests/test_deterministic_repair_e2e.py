from __future__ import annotations

from agent.services.deterministic_repair_handler import DeterministicRepairHandler


_HANDLER = DeterministicRepairHandler()


def _make_admin_repair_task() -> dict:
    return {
        "task_id": "e2e-repair-001",
        "goal_id": "goal-repair-001",
        "task_kind": "admin_repair",
        "mode_data": {
            "issue_symptom": "Docker Compose service 'api' fails to start: port 5000 already in use",
            "execution_scope": "bounded_repair",
            "platform_target": "ubuntu",
            "evidence_sources": ["error_logs", "port_state", "service_status"],
            "affected_targets": ["docker", "api-service"],
            "dry_run": True,
            "deterministic_repair_foundation": {
                "repair_procedure": {
                    "id": "repair-procedure-port_conflict-v1",
                    "problem_class": "port_conflict",
                    "safety_class": "review_first",
                    "steps": [
                        {
                            "id": "repair-step-01",
                            "title": "Prepare bounded repair preview",
                            "mutation_candidate": False,
                            "dry_run_supported": True,
                        },
                        {
                            "id": "repair-step-02",
                            "title": "Execute port conflict resolution",
                            "mutation_candidate": True,
                            "dry_run_supported": True,
                        },
                        {
                            "id": "repair-step-03",
                            "title": "Run post-repair verification checks",
                            "mutation_candidate": False,
                            "dry_run_supported": False,
                        },
                    ],
                },
                "repair_preview": {
                    "step_count": 3,
                    "mutation_step_ids": ["repair-step-02"],
                    "dry_run_default": True,
                },
                "diagnosis_artifact": {
                    "problem_class": "port_conflict",
                    "confidence": 0.85,
                    "likely_causes": ["port_already_bound", "service_bind_config_incorrect"],
                },
            },
        },
    }


def test_deterministic_repair_handler_propose_returns_structured_action() -> None:
    task = _make_admin_repair_task()
    result = _HANDLER.propose(task=task)
    assert result is not None, "Handler should return a proposal for admin_repair tasks"


def test_deterministic_repair_handler_propose_contains_structured_action() -> None:
    task = _make_admin_repair_task()
    result = _HANDLER.propose(task=task)
    structured = result.get("structured_action")
    assert structured is not None
    assert structured.get("action") == "deterministic_repair"
    assert structured.get("execution_mode") == "step_confirmed"
    procedure = structured.get("procedure", {})
    steps = procedure.get("steps", [])
    assert len(steps) > 0, "Procedure must contain at least one step"
    step_ids = [s.get("step_id") for s in steps]
    assert all(sid for sid in step_ids), "Each step must have a step_id"
    assert any(s.get("mutation_candidate") for s in steps), "At least one step should be a mutation candidate"


def test_deterministic_repair_handler_propose_includes_review() -> None:
    task = _make_admin_repair_task()
    result = _HANDLER.propose(task=task)
    review = result.get("review", {})
    assert review.get("status") == "pending"
    assert "required" in review


def test_deterministic_repair_handler_execute_returns_step() -> None:
    task = _make_admin_repair_task()
    result = _HANDLER.execute(task=task, request_data=None)
    assert result is not None
    assert result.get("execution_mode") == "deterministic"
    assert result.get("executed_step_id") is not None


def test_deterministic_repair_handler_execute_supports_step_selection() -> None:
    task = _make_admin_repair_task()
    first = _HANDLER.execute(task=task, request_data={"step_id": "repair-step-01"})
    assert first is not None
    assert first.get("executed_step_id") == "repair-step-01"


def test_deterministic_repair_handler_returns_none_for_non_repair_task() -> None:
    task = {"task_id": "regular-001", "worker_execution_context": {}}
    result = _HANDLER.propose(task=task)
    assert result is None, "Non-repair tasks should return None"
