import pytest
from sqlmodel import Session, select
from agent.db_models import RepairExecutionRecordDB, AgentInfoDB
from worker.core.runtime_target import WorkerSelectionMode, SelectionDecisionStatus, WorkerKind, WorkerRuntimeKind
from worker.core.execution_envelope import RepairExecutionResult, RepairResultVerdict

def test_repair_preview_selection_logic(client, admin_token, session):
    """DRR-T050/DRR-T055: Verify that preview returns deterministic selection."""

    # Setup a mock agent in DB
    agent = AgentInfoDB(
        url="http://test-worker:5000",
        name="Test Worker",
        role="worker",
        status="online",
        capabilities=["patch_apply", "shell_execute"],
        runtime_targets=[{
            "runtime_target_id": "test-target",
            "runtime_kind": "local_process",
            "health_state": "ready"
        }]
    )
    session.add(agent)
    session.commit()

    preview_payload = {
        "matching_outcome": {"outcome": "matched", "best_problem_class": "python_syntax_error"},
        "policy": {
            "mode": "automatic",
            "required_capabilities": ["patch_apply"]
        }
    }

    resp = client.post("/repair/preview", json=preview_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    selection = data.get("worker_selection", {})
    decision = selection.get("decision", {})

    assert decision["decision_status"] == "selected"
    assert decision["selected_worker_id"] == "http://test-worker:5000"
    assert decision["selected_runtime_target_id"] == "test-target"
    assert "policy_decision_ref" in decision

def test_repair_outcome_persistence_with_selection(session):
    """DRR-T049: Verify that selection metadata is persisted in RepairExecutionRecordDB."""
    from agent.services.repair_outcome_service import persist_repair_execution_result
    from worker.core.runtime_target import SelectedWorkerRuntimeRef

    result = RepairExecutionResult(
        plan_id="plan-123",
        procedure_id="proc-456",
        status=RepairResultVerdict.success,
        outcome_label="fixed",
        selected_worker_runtime=SelectedWorkerRuntimeRef(
            selected_worker_id="worker-1",
            selected_worker_kind=WorkerKind.native_ananta_worker,
            selected_runtime_target_id="target-1",
            selected_runtime_kind=WorkerRuntimeKind.local_process,
            selection_mode=WorkerSelectionMode.automatic,
            selection_reason="Test reason",
            selection_decision_ref="ref-789"
        ),
        actual_worker_runtime=SelectedWorkerRuntimeRef(
            selected_worker_id="worker-1",
            selected_worker_kind=WorkerKind.native_ananta_worker,
            selected_runtime_target_id="target-1",
            selected_runtime_kind=WorkerRuntimeKind.local_process,
            selection_mode=WorkerSelectionMode.automatic
        )
    )

    db_record = persist_repair_execution_result(session, result)
    session.commit()

    assert db_record.selected_worker_id == "worker-1"
    assert db_record.selected_runtime_kind == "local_process"
    assert db_record.selection_reason == "Test reason"
    assert db_record.selection_decision_ref == "ref-789"
    assert db_record.actual_worker_id == "worker-1"

def test_list_candidates_api(client, admin_token, session):
    """DRR-T050: Verify candidates listing API."""

    # Ensure at least one agent is there
    agent = AgentInfoDB(
        url="http://candidate-worker:5000",
        name="Candidate",
        role="worker",
        status="online",
        capabilities=["repair.execute"],
        runtime_targets=[{"runtime_target_id": "rt-1", "runtime_kind": "docker_container"}]
    )
    session.add(agent)
    session.commit()

    resp = client.post("/repair/candidates", json={
        "policy": {"mode": "automatic", "required_capabilities": ["repair.execute"]}
    }, headers={"Authorization": f"Bearer {admin_token}"})

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["candidate_count"] >= 1
    assert any(c["worker_id"] == "http://candidate-worker:5000" for c in data["candidates"])

def test_list_runtime_targets_api(client, admin_token):
    """DRR-T050: Verify runtime targets listing API."""
    resp = client.get("/repair/runtime-targets", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "runtime_targets" in data
    # Should at least have the defaults
    ids = [rt["runtime_target_id"] for rt in data["runtime_targets"]]
    assert "local-process-default" in ids
    assert "docker-worker-default" in ids
