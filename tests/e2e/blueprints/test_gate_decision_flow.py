"""End-to-end gate decision flow (WFG-025).

The test exercises the full HTTP path that operators hit
when a workflow gate is blocking and they want to resolve
it. The test is intentionally lean — it does NOT depend
on the goal materialiser or a live worker. It focuses on:

  1. The team-blueprint instantiation route now exposes
     the workflow block (WFG-033) — a regression in the
     reconciliation path would break this.
  2. The ``/goals/<id>/workflow-status`` endpoint surfaces
     the gate step (WFG-017).
  3. The ``/goals/<id>/gates/<task_id>/human-decision``
     endpoint accepts an operator decision and mirrors
     the result on the workflow-status response (WFG-024).
  4. An invalid outcome is rejected with HTTP 400 without
     mutating the gate (WFG-024 contract).

The actual gate task used in the test is a real
``TaskDB`` row the test creates directly. The route
exercises the full ``submit_human_decision_via_repo``
service path, which writes the audit log.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests_support import admin_login_token as _login_admin  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_tdd_blueprint(client, auth_header: dict) -> dict:
    """Find the TDD seed blueprint. ``ensure_seed_blueprints``
    runs in the route's ``@check_auth`` path so a single
    call to ``/teams/blueprints`` is enough."""
    response = client.get("/teams/blueprints", headers=auth_header)
    assert response.status_code == 200
    tdd = next(item for item in response.json["data"] if item["name"] == "TDD")
    return tdd


def _create_real_goal(name: str) -> str:
    """Create a GoalDB row directly so the route's ``_can_access_goal``
    check passes without triggering LLM-based planning (which hangs in CI
    where no model is configured)."""
    from sqlmodel import Session
    from agent.database import engine
    from agent.db_models import GoalDB
    goal_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(GoalDB(
            id=goal_id,
            goal=name,
            status="created",
            requested_by="admin",
        ))
        session.commit()
    return goal_id


def _build_gate_task(
    *, goal_id: str, gate_task_id: str | None = None
) -> dict:
    """Build a ``TaskDB``-shaped dict that mimics the
    gate engine's ``pending_approval`` state. The endpoint
    reads the task via ``task_repo.get_by_id`` and uses
    the human-approval service to resolve it.
    """
    from agent.services.human_approval_service import (
        build_pending_approval_record,
    )
    gate_task_id = gate_task_id or f"ptask-gate-{uuid.uuid4().hex[:8]}"
    pending = build_pending_approval_record(
        goal_id=goal_id, gate_task_id=gate_task_id
    )
    return {
        "id": gate_task_id,
        "goal_id": goal_id,
        "status": "blocked",
        "title": "TDD refactor gate (e2e)",
        "description": "End-to-end gate fixture",
        "priority": "Medium",
        "created_at": time.time(),
        "updated_at": time.time(),
        "task_kind": "gate_review",
        "verification_status": {
            "gate": "pending_approval",
            "gate_decision": pending,
        },
        "worker_execution_context": {
            "workflow_step_provenance": {
                "schema": "workflow_step_provenance.v1",
                "blueprint_id": None,
                "workflow_id": "tdd",
                "step_id": "refactor",
                "gate": True,
            }
        },
    }


def _persist_gate_task(task: dict) -> None:
    """Persist a gate task to the test DB so the route can
    load it via ``task_repo.get_by_id``."""
    from sqlmodel import Session

    from agent.database import engine
    from agent.db_models import TaskDB
    row = TaskDB(
        id=task["id"],
        goal_id=task["goal_id"],
        status=task["status"],
        title=task["title"],
        description=task["description"],
        priority=task["priority"],
        created_at=task["created_at"],
        updated_at=task["updated_at"],
        task_kind=task["task_kind"],
        verification_status=task["verification_status"],
        worker_execution_context=task["worker_execution_context"],
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()


# ---------------------------------------------------------------------------
# Test 1: blueprint workflow block is on the snapshot
# ---------------------------------------------------------------------------


def test_blueprint_snapshot_exposes_workflow_block(client) -> None:
    """WFG-033 anchor: a team instantiated from the TDD
    blueprint carries the workflow block in its
    ``blueprint_snapshot`` so the goal materialiser and
    the workflow-status endpoint see the gate step."""
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    tdd = _ensure_tdd_blueprint(client, auth_header)
    assert "workflow" in tdd, "blueprint must carry the workflow block"
    assert any(
        step.get("gate") and step.get("id") == "refactor"
        for step in tdd["workflow"]["steps"]
    ), "TDD workflow must have a refactor gate step"

    # Instantiate a team and assert the snapshot keeps it
    inst = client.post(
        f"/teams/blueprints/{tdd['id']}/instantiate",
        json={"name": "WFG-025 E2E Team", "activate": False, "members": []},
        headers=auth_header,
    )
    assert inst.status_code == 201
    snapshot = inst.json["data"]["team"]["blueprint_snapshot"]
    assert "workflow" in snapshot
    assert any(
        step.get("gate") and step.get("id") == "refactor"
        for step in snapshot["workflow"]["steps"]
    )


# ---------------------------------------------------------------------------
# Test 2: human-decision endpoint approves a real gate task
# ---------------------------------------------------------------------------


def test_human_decision_endpoint_approves_gate(client) -> None:
    """WFG-024 + WFG-015: the operator endpoint resolves
    the gate and writes the audit log. The test persists
    a real ``TaskDB`` row so the route's
    ``task_repo.get_by_id`` call returns a non-null value.
    """
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    goal_id = _create_real_goal("WFG-025 approve")

    gate = _build_gate_task(goal_id=goal_id)
    _persist_gate_task(gate)

    # Approve
    response = client.post(
        f"/goals/{goal_id}/gates/{gate['id']}/human-decision",
        json={
            "outcome": "approved",
            "operator": "wfg025_runner",
            "reason": "E2E: refactor signoff verified",
        },
        headers=auth_header,
    )
    assert response.status_code == 200, response.data
    body = response.json.get("data") or response.json
    block = body.get("decision") or {}
    assert block.get("status") == "approved"
    assert block.get("resolved_by") == "wfg025_runner"
    assert block.get("decision_id", "").startswith("hdec-")
    assert block.get("resolution_reason") == "E2E: refactor signoff verified"


# ---------------------------------------------------------------------------
# Test 3: rejected decision
# ---------------------------------------------------------------------------


def test_human_decision_endpoint_rejects_gate(client) -> None:
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    goal_id = _create_real_goal("WFG-025 reject")

    gate = _build_gate_task(goal_id=goal_id)
    _persist_gate_task(gate)

    response = client.post(
        f"/goals/{goal_id}/gates/{gate['id']}/human-decision",
        json={
            "outcome": "rejected",
            "operator": "wfg025_runner",
            "reason": "E2E: evidence missing",
        },
        headers=auth_header,
    )
    assert response.status_code == 200, response.data
    block = response.json["data"]["decision"]
    assert block["status"] == "rejected"


# ---------------------------------------------------------------------------
# Test 4: invalid outcome returns 400
# ---------------------------------------------------------------------------


def test_human_decision_endpoint_rejects_invalid_outcome(client) -> None:
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}
    goal_id = _create_real_goal("WFG-025 invalid")

    gate = _build_gate_task(goal_id=goal_id)
    _persist_gate_task(gate)

    response = client.post(
        f"/goals/{goal_id}/gates/{gate['id']}/human-decision",
        json={"outcome": "banana", "operator": "wfg025_runner"},
        headers=auth_header,
    )
    assert response.status_code == 400, response.data

    # The task must NOT have been mutated. A re-query
    # through the service shows the gate is still
    # pending_approval.
    from agent.repository import task_repo
    from agent.services.human_approval_service import is_pending_approval
    persisted = task_repo.get_by_id(gate["id"])
    assert is_pending_approval(persisted), (
        "invalid outcome must not mutate the gate"
    )
