"""WFG-004: tests for the workflow definition service."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import BlueprintWorkflowStepDB, TeamBlueprintDB
from agent.services.workflow_definition_service import (
    WorkflowDefinitionError,
    WorkflowDefinitionService,
)
from agent.services.workflow_settings import (
    reset_workflow_settings_cache,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("ANANTA_WORKFLOW_"):
            monkeypatch.delenv(key, raising=False)
    reset_workflow_settings_cache()
    yield
    reset_workflow_settings_cache()


def _blueprint(session: Session, name: str = "bp") -> TeamBlueprintDB:
    bp = TeamBlueprintDB(name=name, description="x", is_seed=True)
    session.add(bp)
    session.commit()
    session.refresh(bp)
    return bp


def test_reconcile_steps_persists_validated_block(session: Session) -> None:
    bp = _blueprint(session)
    block = {
        "mode": "gated",
        "steps": [
            {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0},
            {"id": "build", "role": "Developer", "task_kind": "coding", "depends_on": ["plan"], "sort_order": 1},
            {"id": "review", "role": "Reviewer", "task_kind": "gate_review", "gate": True, "checks": {"min_artifacts": ["x"]}, "depends_on": ["build"], "sort_order": 2},
        ],
    }
    svc = WorkflowDefinitionService()
    rows = svc.reconcile_steps(session, bp, block)
    assert len(rows) == 3
    assert [r.step_id for r in rows] == ["plan", "build", "review"]
    assert rows[2].gate is True
    assert rows[2].checks == {"min_artifacts": ["x"]}


def test_reconcile_steps_is_idempotent(session: Session) -> None:
    bp = _blueprint(session)
    block = {"mode": "gated", "steps": [
        {"id": "a", "role": "X", "task_kind": "coding", "sort_order": 0},
        {"id": "b", "role": "X", "task_kind": "coding", "depends_on": ["a"], "sort_order": 1},
    ]}
    svc = WorkflowDefinitionService()
    rows1 = svc.reconcile_steps(session, bp, block)
    rows2 = svc.reconcile_steps(session, bp, block)
    assert len(rows1) == 2 and len(rows2) == 2
    # No duplicates
    all_rows = svc.get_steps(session, bp.id)
    assert len(all_rows) == 2


def test_reconcile_steps_off_mode_clears_existing(session: Session) -> None:
    bp = _blueprint(session)
    block = {"mode": "gated", "steps": [
        {"id": "a", "role": "X", "task_kind": "coding", "sort_order": 0},
    ]}
    svc = WorkflowDefinitionService()
    svc.reconcile_steps(session, bp, block)
    assert len(svc.get_steps(session, bp.id)) == 1

    os.environ["ANANTA_WORKFLOW_MODE"] = "off"
    reset_workflow_settings_cache()
    rows = svc.reconcile_steps(session, bp, block)
    assert rows == []
    assert svc.get_steps(session, bp.id) == []


def test_reconcile_steps_rejects_missing_steps(session: Session) -> None:
    bp = _blueprint(session)
    svc = WorkflowDefinitionService()
    with pytest.raises(WorkflowDefinitionError, match="no steps"):
        svc.reconcile_steps(session, bp, {"mode": "gated"})


def test_reconcile_steps_rejects_duplicate_id(session: Session) -> None:
    bp = _blueprint(session)
    svc = WorkflowDefinitionService()
    block = {"mode": "gated", "steps": [
        {"id": "a", "role": "X"},
        {"id": "a", "role": "X"},
    ]}
    with pytest.raises(WorkflowDefinitionError, match="duplicate"):
        svc.reconcile_steps(session, bp, block)


def test_reconcile_steps_rejects_missing_role(session: Session) -> None:
    bp = _blueprint(session)
    svc = WorkflowDefinitionService()
    block = {"mode": "gated", "steps": [{"id": "a"}]}
    with pytest.raises(WorkflowDefinitionError, match="missing 'role'"):
        svc.reconcile_steps(session, bp, block)


def test_reconcile_steps_rejects_unknown_dependency(session: Session) -> None:
    bp = _blueprint(session)
    svc = WorkflowDefinitionService()
    block = {"mode": "gated", "steps": [
        {"id": "a", "role": "X", "depends_on": ["ghost"]},
    ]}
    with pytest.raises(WorkflowDefinitionError, match="unknown step"):
        svc.reconcile_steps(session, bp, block)


def test_get_steps_returns_sorted_by_sort_order(session: Session) -> None:
    bp = _blueprint(session)
    # Insert out of order to verify sort_order is the source of truth
    block = {"mode": "gated", "steps": [
        {"id": "b", "role": "X", "sort_order": 5},
        {"id": "a", "role": "X", "sort_order": 1},
        {"id": "c", "role": "X", "sort_order": 9},
    ]}
    svc = WorkflowDefinitionService()
    svc.reconcile_steps(session, bp, block)
    rows = svc.get_steps(session, bp.id)
    assert [r.step_id for r in rows] == ["a", "b", "c"]


def test_topological_order_follows_depends_on(session: Session) -> None:
    bp = _blueprint(session)
    # Sort order does NOT match topological order
    block = {"mode": "gated", "steps": [
        {"id": "b", "role": "X", "depends_on": ["a"], "sort_order": 1},
        {"id": "a", "role": "X", "sort_order": 0},
    ]}
    svc = WorkflowDefinitionService()
    rows = svc.reconcile_steps(session, bp, block)
    topo = svc.topological_order(rows)
    assert [r.step_id for r in topo] == ["a", "b"]


def test_topological_order_raises_on_cycle(session: Session) -> None:
    # Bypass the catalog normalizer to feed a cycle directly
    a = BlueprintWorkflowStepDB(
        id=str(uuid.uuid4()), blueprint_id="x", step_id="a", role_name="X",
        depends_on=["b"], sort_order=0,
    )
    b = BlueprintWorkflowStepDB(
        id=str(uuid.uuid4()), blueprint_id="x", step_id="b", role_name="X",
        depends_on=["a"], sort_order=1,
    )
    svc = WorkflowDefinitionService()
    with pytest.raises(WorkflowDefinitionError, match="cycle"):
        svc.topological_order([a, b])


def test_topological_order_raises_on_unknown_dep(session: Session) -> None:
    a = BlueprintWorkflowStepDB(
        id=str(uuid.uuid4()), blueprint_id="x", step_id="a", role_name="X",
        depends_on=["ghost"], sort_order=0,
    )
    svc = WorkflowDefinitionService()
    with pytest.raises(WorkflowDefinitionError, match="unknown step"):
        svc.topological_order([a])


def test_topological_order_empty_returns_empty() -> None:
    assert WorkflowDefinitionService().topological_order([]) == []


# ── WFG-008: default-gate insertion tests ─────────────────────────────


def test_insert_default_gates_empty_returns_empty() -> None:
    assert WorkflowDefinitionService().insert_default_gates([], blueprint_name="x") == []


def test_insert_default_gates_no_planner_returns_unchanged() -> None:
    steps = [
        {"id": "a", "role": "Reviewer", "task_kind": "review", "sort_order": 0, "depends_on": []},
    ]
    assert WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x"
    ) == steps


def test_insert_default_gates_no_developer_returns_unchanged() -> None:
    steps = [
        {"id": "a", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
    ]
    assert WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x"
    ) == steps


def test_insert_default_gates_planner_to_developer_adds_reviewer() -> None:
    """The classic case: Planner writes spec, Developer writes code.
    A Reviewer gate must sit between them."""
    steps = [
        {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
        {"id": "build", "role": "Developer", "task_kind": "coding", "sort_order": 1, "depends_on": ["plan"]},
    ]
    out = WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x"
    )
    assert len(out) == 3
    gate = next(s for s in out if s["task_kind"] == "gate_review")
    assert gate["role"] == "Reviewer"
    assert gate["gate"] is True
    assert gate["depends_on"] == ["plan"]
    assert gate["checks"]["policy"] == "wfg008_default_planner_to_developer"
    # Developer's dependency was rewired to point at the gate
    build = next(s for s in out if s["id"] == "build")
    assert build["depends_on"] == [gate["id"]]


def test_insert_default_gates_idempotent_when_explicit_gate_present() -> None:
    """If the workflow already has a gate between Planner and Developer,
    no default gate is inserted."""
    steps = [
        {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
        {"id": "gate", "role": "Reviewer", "task_kind": "gate_review", "gate": True,
         "sort_order": 1, "depends_on": ["plan"],
         "checks": {"min_artifacts": ["x"]}, "failure_policy": "block"},
        {"id": "build", "role": "Developer", "task_kind": "coding", "sort_order": 2, "depends_on": ["gate"]},
    ]
    out = WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x"
    )
    # No new step inserted, and the explicit gate's id is preserved
    assert len(out) == 3
    assert sum(1 for s in out if s["task_kind"] == "gate_review") == 1
    assert any(s["id"] == "gate" for s in out)


def test_insert_default_gates_uses_deployment_default_policy() -> None:
    from agent.services.workflow_settings import (
        GateFailurePolicy,
        WorkflowMode,
        WorkflowSettings,
    )
    settings = WorkflowSettings(
        mode=WorkflowMode.AUTO,
        default_gate_policy=GateFailurePolicy.SKIP,
        gate_timeout_seconds=86400,
        audit_enabled=True,
        artifact_flow_enforced=True,
    )
    steps = [
        {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
        {"id": "build", "role": "Developer", "task_kind": "coding", "sort_order": 1, "depends_on": ["plan"]},
    ]
    out = WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x", settings=settings
    )
    gate = next(s for s in out if s["task_kind"] == "gate_review")
    assert gate["failure_policy"] == "skip"


def test_insert_default_gates_per_step_policy_overrides() -> None:
    """A per-step failure_policy on the developer step is used (this is
    a pragmatic place to look because the developer step is the one
    being gated; the catalog normalizer is responsible for the broader
    policy resolution)."""
    from agent.services.workflow_settings import (
        GateFailurePolicy,
        WorkflowMode,
        WorkflowSettings,
    )
    settings = WorkflowSettings(
        mode=WorkflowMode.AUTO,
        default_gate_policy=GateFailurePolicy.BLOCK,
        gate_timeout_seconds=86400,
        audit_enabled=True,
        artifact_flow_enforced=True,
    )
    steps = [
        {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
        {"id": "build", "role": "Developer", "task_kind": "coding",
         "failure_policy": "manual", "sort_order": 1, "depends_on": ["plan"]},
    ]
    out = WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x", settings=settings
    )
    gate = next(s for s in out if s["task_kind"] == "gate_review")
    assert gate["failure_policy"] == "manual"


def test_insert_default_gates_preserves_dag_validity() -> None:
    """After insertion, the workflow DAG must still be acyclic with all
    references resolvable."""
    steps = [
        {"id": "plan", "role": "Planner", "task_kind": "planning", "sort_order": 0, "depends_on": []},
        {"id": "build", "role": "Developer", "task_kind": "coding", "sort_order": 1, "depends_on": ["plan"]},
        {"id": "deploy", "role": "Operator", "task_kind": "ops", "sort_order": 2, "depends_on": ["build"]},
    ]
    out = WorkflowDefinitionService().insert_default_gates(
        steps, blueprint_name="x"
    )
    # Validate DAG
    ids = {s["id"] for s in out}
    for s in out:
        for d in s.get("depends_on", []):
            assert d in ids, f"step {s['id']!r} has unknown dep {d!r}"
    # Validate acyclic
    in_deg = {s["id"]: 0 for s in out}
    edges = {s["id"]: [] for s in out}
    for s in out:
        for d in s.get("depends_on", []):
            in_deg[s["id"]] += 1
            edges[d].append(s["id"])
    from collections import deque
    ready = deque(sid for sid, deg in in_deg.items() if deg == 0)
    seen = 0
    while ready:
        sid = ready.popleft()
        seen += 1
        for succ in edges[sid]:
            in_deg[succ] -= 1
            if in_deg[succ] == 0:
                ready.append(succ)
    assert seen == len(out), "default-gate insertion introduced a cycle"
