from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from flask import g
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import ApprovalRequestDB, GoalDB, TaskDB
from agent.routes.approvals import _can_view_approval
from agent.services.approval_request_service import ApprovalRequestService
from agent.services.recovery_plan_contract import (
    calculate_recovery_materialization_inputs_digest,
    calculate_recovery_plan_digest,
    calculate_recovery_task_payload_digest,
)
from agent.services.task_recovery_planning_service import (
    RECOVERY_MATERIALIZE_TOOL,
    TaskRecoveryPlanningService,
)


class Record(SimpleNamespace):
    def model_dump(self):
        return dict(vars(self))


_DISPATCH_REQUEST_FINGERPRINT = "f" * 64


def _bind_recovery_dispatch_fixture(
    *,
    plan: Record,
    goal: Record,
    child: Record,
) -> Record:
    """Populate the complete approval-time binding required by the Gate."""

    for field, value in {
        "goal": "",
        "mode": "",
        "mode_data": {},
        "execution_preferences": {},
    }.items():
        if not hasattr(goal, field):
            setattr(goal, field, value)
    for field, value in {
        "title": "Approved recovery task",
        "description": "Implement and verify the approved recovery step.",
        "priority": "Medium",
        "plan_node_id": f"node-{child.id}",
        "parent_task_id": None,
        "derivation_depth": 1,
        "task_kind": "coding",
        "retrieval_intent": "",
        "required_context_scope": "",
        "preferred_bundle_mode": "",
        "required_capabilities": [],
        "context_bundle_id": None,
        "worker_execution_context": {},
        "worker_execution_contract": {},
        "expected_artifacts": [],
        "verification_spec": {},
        "depends_on": [],
    }.items():
        if not hasattr(child, field):
            setattr(child, field, value)
    for field, value in {
        "trace_id": "",
        "planning_mode": "generic",
    }.items():
        if not hasattr(plan, field):
            setattr(plan, field, value)

    node = Record(
        id=child.plan_node_id,
        plan_id=plan.id,
        node_key=f"node-key-{child.id}",
        title=child.title,
        description=child.description,
        priority=child.priority,
        position=0,
        depends_on=[],
        editable=True,
        rationale={
            "task_kind": child.task_kind,
            "retrieval_intent": child.retrieval_intent,
            "required_context_scope": (
                child.required_context_scope
            ),
            "preferred_bundle_mode": child.preferred_bundle_mode,
            "required_capabilities": list(
                child.required_capabilities
            ),
        },
        verification_spec=dict(child.verification_spec),
        materialized_task_id=child.id,
    )
    plan.rationale["materialization_inputs_digest"] = (
        calculate_recovery_materialization_inputs_digest(goal)
    )
    plan.rationale["plan_digest"] = (
        calculate_recovery_plan_digest(plan, [node])
    )
    child.status_reason_details[
        "model_recovery_release"
    ]["task_payload_digest"] = (
        calculate_recovery_task_payload_digest(child)
    )
    return node


class MemoryPlanRepo:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, plan_id):
        return self.rows.get(plan_id)

    def get_by_goal_id(self, goal_id):
        return [row for row in reversed(list(self.rows.values())) if row.goal_id == goal_id]

    def save(self, plan):
        self.rows[plan.id] = plan
        return plan


class MemoryNodeRepo:
    def __init__(self):
        self.rows = {}

    def get_by_plan_id(self, plan_id):
        return sorted(
            [row for row in self.rows.values() if row.plan_id == plan_id],
            key=lambda row: row.position,
        )

    def save(self, node):
        self.rows[node.id] = node
        return node


class MemoryTaskRepo:
    def __init__(self):
        self.rows = {}

    def get_by_id(self, task_id):
        return self.rows.get(task_id)

    def save(self, task):
        self.rows[task.id] = task
        return task

    def delete(self, task_id):
        return self.rows.pop(task_id, None) is not None


class MemoryApprovalService:
    def __init__(self):
        self.created = []
        self.consumed = []

    def create_pending_request(self, **values):
        for row in self.created:
            if (
                row.status in {"pending", "granted"}
                and row.task_id == values.get("task_id")
                and row.target_fingerprint == values["target_fingerprint"]
                and row.canonical_arguments == dict(values["arguments"])
            ):
                return row
        row = Record(
            id=f"approval-{len(self.created) + 1}",
            status="pending",
            expires_at=None,
            target_fingerprint=values["target_fingerprint"],
            canonical_arguments=dict(values["arguments"]),
            tool_name=values["tool_name"],
            task_id=values.get("task_id"),
            goal_id=values.get("goal_id"),
            trace_id=values.get("trace_id"),
            scope=dict(values.get("scope") or {}),
        )
        self.created.append(row)
        return row

    def get_request(self, request_id):
        return next(
            (row for row in self.created if row.id == request_id),
            None,
        )

    def consume_request(self, request_id):
        self.consumed.append(request_id)
        for row in self.created:
            if row.id == request_id:
                row.status = "consumed"
                return row
        return Record(id=request_id, status="consumed")


class MemoryPlanner:
    def __init__(self, repos):
        self.repos = repos
        self.calls = []
        self._stats = {"tasks_created": 0}

    def plan_goal(self, **values):
        self.calls.append(values)
        plan_number = len(self.calls)
        plan = Record(
            id=f"plan-recovery-{plan_number}",
            goal_id=values["goal_id"],
            trace_id=values["goal_trace_id"],
            status="draft",
            planning_mode="template",
            rationale=dict(values.get("initial_plan_rationale") or {}),
            updated_at=0.0,
        )
        self.repos.plan_repo.save(plan)
        for index, task_kind in enumerate(("coding", "testing", "review"), start=1):
            node = Record(
                id=f"node-{index}",
                plan_id=plan.id,
                node_key=f"{plan.id}-node-{index}",
                title=f"Recovery step {index}",
                description=f"Implement concrete file and run test command for step {index}.",
                priority="High",
                status="draft",
                position=index,
                depends_on=[] if index == 1 else [f"{plan.id}-node-{index - 1}"],
                rationale={
                    "task_kind": task_kind,
                    "required_capabilities": [],
                    "expected_artifacts": [],
                },
                editable=True,
                materialized_task_id=None,
                verification_spec={},
            )
            self.repos.plan_node_repo.rows[node.id] = node
        return {"plan_id": plan.id, "created_task_ids": [], "subtasks": [{}, {}, {}]}


class MemoryPlanningService:
    def __init__(self):
        self.calls = []
        self.lock_entries = []

    @contextlib.contextmanager
    def plan_mutation_lock(self, plan_id):
        self.lock_entries.append(plan_id)
        yield True

    def materialize_existing_plan(self, **values):
        self.calls.append(values)
        return {
            "status": "materialized",
            "reason_code": "approved_plan_materialized",
            "plan_id": values["plan_id"],
            "created_task_ids": ["child-1", "child-2"],
        }


def _fixture(*, routing_policy=None):
    task = Record(
        id="task-1",
        goal_id="goal-1",
        goal_trace_id="trace-1",
        team_id="team-1",
        title="Implement API endpoint",
        description="Create the endpoint and verify it with automated tests.",
        status="proposing",
        status_reason_details={},
        verification_status={},
        depends_on=["already-complete"],
        derivation_reason=None,
        worker_execution_context={
            "model_routing": {
                "preferred_profile_id": "local_ollama_phi4_mini",
                "fallback_group_id": "local_phi_to_gemma_reasoning",
                "context_recovery_strategies": [
                    "segment_planning",
                    "propose_task_plan",
                    "require_approval",
                    "stop",
                ],
                "require_approval_for_generated_plan": True,
            }
        },
    )
    goal = Record(
        id="goal-1",
        trace_id="trace-1",
        team_id="team-1",
        status="in_progress",
    )
    repos = Record(
        task_repo=Record(get_by_id=lambda task_id: task if task_id == task.id else None),
        goal_repo=Record(get_by_id=lambda goal_id: goal if goal_id == goal.id else None),
        plan_repo=MemoryPlanRepo(),
        plan_node_repo=MemoryNodeRepo(),
    )
    approvals = MemoryApprovalService()
    planner = MemoryPlanner(repos)
    planning = MemoryPlanningService()
    updates = []

    def update_task(task_id, status, **values):
        updates.append((task_id, status, values))
        if task_id != task.id:
            return
        task.status = status
        for key, value in values.items():
            if hasattr(task, key):
                setattr(task, key, value)

    service = TaskRecoveryPlanningService(
        role_provider=lambda: "hub",
        repository_provider=lambda: repos,
        planner_provider=lambda: planner,
        approval_service_provider=lambda: approvals,
        planning_service_provider=lambda: planning,
        routing_policy_provider=((lambda: dict(routing_policy)) if routing_policy is not None else None),
        task_status_updater=update_task,
    )
    failures = [
        {
            "failure_type": "invalid_proposal",
            "model_recovery_signal": {
                "schema": "model_recovery_signal.v1",
                "state": "exhausted",
                "reason_code": "model_fallback_exhausted",
                "terminal": True,
                "terminal_reason": "schema_validation_failed",
                "attempt_count": 5,
                "error_types": ["schema_validation_failed"],
                "failed_profile_ids": [
                    "local_ollama_phi4_mini",
                    "local_ollama_gemma4_e4b_reasoning",
                ],
            },
        }
    ]
    return service, task, repos, approvals, planner, planning, updates, failures


def test_exhaustion_creates_one_persisted_approval_gated_recovery_plan():
    service, task, repos, approvals, planner, _planning, _updates, failures = _fixture()

    first = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    second = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert first["status"] == "pending_approval"
    assert first["plan_id"] == "plan-recovery-1"
    assert first["node_count"] == 3
    assert second["reason_code"] == "recovery_plan_already_exists"
    assert len(planner.calls) == 1
    assert len(approvals.created) == 1
    assert approvals.created[0].tool_name == RECOVERY_MATERIALIZE_TOOL
    assert approvals.created[0].canonical_arguments["team_id"] == (
        "team-1"
    )
    assert approvals.created[0].scope["team_id"] == "team-1"
    assert repos.plan_repo.get_by_id(first["plan_id"]).status == "pending_approval"
    assert repos.plan_repo.get_by_id(
        first["plan_id"]
    ).rationale["team_id"] == "team-1"


def test_grant_materializes_once_and_requeues_parent_after_children():
    service, task, _repos, approvals, _planner, planning, updates, failures = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"

    result = service.handle_approval_decision(approval)

    assert result["status"] == "materialized"
    assert approvals.consumed == [approval.id]
    assert len(planning.calls) == 1
    assert planning.calls[0]["parent_task_id"] is None
    assert planning.calls[0]["source_task_id"] == "task-1"
    assert planning.calls[0]["initial_task_status"] == "paused"
    assert planning.calls[0]["expected_plan_digest"] == proposal["plan_digest"]
    assert [entry[0:2] for entry in updates[-3:]] == [
        ("task-1", "blocked_by_dependency"),
        ("child-1", "todo"),
        ("child-2", "blocked_by_dependency"),
    ]
    source_update = next(
        entry for entry in reversed(updates) if entry[0] == "task-1" and entry[1] == "blocked_by_dependency"
    )
    assert source_update[2]["depends_on"] == [
        "already-complete",
        "child-1",
        "child-2",
    ]
    recovery = source_update[2]["status_reason_details"]["model_recovery"]
    assert recovery["plan_id"] == proposal["plan_id"]
    assert recovery["recovery_depth"] == 1
    dependency_binding = recovery["dependency_binding"]
    assert dependency_binding[
        "preexisting_dependency_ids"
    ] == ["already-complete"]
    assert dependency_binding["child_task_ids"] == [
        "child-1",
        "child-2",
    ]
    assert dependency_binding[
        "authoritative_dependency_ids"
    ] == source_update[2]["depends_on"]
    assert len(dependency_binding["digest"]) == 64


def test_policy_revocation_consumes_grant_without_materializing():
    service, task, repos, approvals, _planner, planning, _updates, failures = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    task.worker_execution_context["model_routing"]["context_recovery_strategies"] = ["stop"]
    approval = approvals.created[0]
    approval.status = "granted"

    result = service.handle_approval_decision(approval)

    assert result["status"] == "stopped"
    assert result["reason_code"] == "recovery_policy_changed"
    assert approvals.consumed == [approval.id]
    assert planning.calls == []
    assert repos.plan_repo.get_by_id(proposal["plan_id"]).status == "rejected"


def test_terminal_source_after_slow_planning_is_not_resurrected():
    service, task, repos, approvals, planner, _planning, updates, failures = _fixture()
    original_plan_goal = planner.plan_goal

    def complete_source_during_planning(**values):
        result = original_plan_goal(**values)
        task.status = "completed"
        return result

    planner.plan_goal = complete_source_during_planning

    result = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert result["status"] == "stopped"
    assert result["reason_code"] == "recovery_source_terminal"
    assert task.status == "completed"
    assert approvals.created == []
    assert updates == []
    assert repos.plan_repo.get_by_id("plan-recovery-1").status == "rejected"


def test_terminal_source_during_materialization_cancels_children_only():
    service, task, _repos, approvals, _planner, planning, updates, failures = _fixture()
    service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    original_materialize = planning.materialize_existing_plan

    def complete_source_during_materialization(**values):
        result = original_materialize(**values)
        task.status = "completed"
        return result

    planning.materialize_existing_plan = complete_source_during_materialization
    approval = approvals.created[0]
    approval.status = "granted"

    result = service.handle_approval_decision(approval)

    assert result["status"] == "materialized"
    assert result["children_cancelled"] is True
    assert task.status == "completed"
    assert approvals.consumed == [approval.id]
    assert [entry[0:2] for entry in updates[-2:]] == [
        ("child-1", "cancelled"),
        ("child-2", "cancelled"),
    ]


def test_failed_approval_consumption_does_not_requeue_source():
    service, task, _repos, approvals, _planner, planning, updates, failures = _fixture()
    service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    approvals.consume_request = lambda _request_id: None

    result = service.handle_approval_decision(approval)

    assert result["status"] == "failed"
    assert result["reason_code"] == "approval_consume_failed"
    assert len(planning.calls) == 1
    assert task.status == "waiting_for_review"
    assert all(status != "todo" for _task_id, status, _values in updates)


def test_consumed_approval_resumes_interrupted_dag_release():
    service, task, repos, approvals, _planner, planning, updates, failures = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    plan = repos.plan_repo.get_by_id(proposal["plan_id"])
    plan.status = "materialized"
    nodes = repos.plan_node_repo.get_by_plan_id(plan.id)
    for index, node in enumerate(nodes, start=1):
        node.materialized_task_id = f"child-{index}"
    approval = approvals.created[0]
    approval.status = "consumed"

    result = service.handle_approval_decision(approval)

    assert result["status"] == "materialized"
    assert result["approval_status"] == "consumed"
    assert planning.calls == []
    assert [entry[0:2] for entry in updates[-4:]] == [
        ("task-1", "blocked_by_dependency"),
        ("child-1", "todo"),
        ("child-2", "blocked_by_dependency"),
        ("child-3", "blocked_by_dependency"),
    ]
    assert repos.plan_repo.get_by_id(plan.id).rationale["materialization_release_state"] == "completed"


def test_stale_plan_digest_creates_a_fresh_exact_approval():
    service, task, repos, approvals, _planner, planning, _updates, failures = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    repos.plan_node_repo.rows["node-1"].title = "Operator edited title"
    approval = approvals.created[0]
    approval.status = "granted"

    result = service.handle_approval_decision(approval)

    assert result["reason_code"] == "recovery_plan_digest_refreshed"
    assert planning.calls == []
    assert approvals.consumed == ["approval-1"]
    assert len(approvals.created) == 2
    assert result["approval_request_id"] == "approval-2"
    assert repos.plan_repo.get_by_id(proposal["plan_id"]).status == "pending_approval"
    assert task.status_reason_details["model_recovery"]["approval_request_id"] == "approval-2"


def test_planning_service_rechecks_digest_inside_materialization_lock(
    app,
    monkeypatch,
):
    from agent.services.planning_service import PlanningService

    _service, _task, repos, _approvals, planner, _planning, _updates, _failures = _fixture()
    planner.plan_goal(
        goal="recovery",
        goal_id="goal-1",
        goal_trace_id="trace-1",
    )
    plan = repos.plan_repo.get_by_id("plan-recovery-1")
    nodes = repos.plan_node_repo.get_by_plan_id(plan.id)
    plan.rationale = {
        "approval_request_id": "approval-1",
        "plan_digest": calculate_recovery_plan_digest(plan, nodes),
    }
    approved_digest = plan.rationale["plan_digest"]
    nodes[0].title = "edited after approval"

    monkeypatch.setattr(
        "agent.services.planning_service.get_repository_registry",
        lambda: repos,
    )
    result = PlanningService().materialize_existing_plan(
        planner=planner,
        plan_id=plan.id,
        approval_request_id="approval-1",
        expected_plan_digest=approved_digest,
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "recovery_plan_digest_stale"
    assert result["created_task_ids"] == []


def test_worker_role_and_recursive_recovery_fail_closed():
    service, task, _repos, _approvals, _planner, _planning, _updates, failures = _fixture()
    worker_service = TaskRecoveryPlanningService(role_provider=lambda: "worker")
    assert (
        worker_service.propose_after_model_exhaustion(
            task=task,
            strategy_failures=failures,
        )["reason_code"]
        == "hub_role_required"
    )

    task.derivation_reason = "goal_task_recovery"
    assert (
        service.propose_after_model_exhaustion(
            task=task,
            strategy_failures=failures,
        )["reason_code"]
        == "task_recovery_recursion_guard"
    )


def test_autopilot_terminal_guard_covers_non_success_terminal_states():
    from agent.routes.tasks.autopilot_task_dispatcher_helpers import (
        _is_terminal_status,
    )

    for status in (
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    ):
        assert _is_terminal_status(status) is True
    assert _is_terminal_status("waiting_for_review") is False


def test_recovery_materialization_decision_api_requires_admin(
    client,
    user_auth_header,
    admin_auth_header,
    monkeypatch,
):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    service = ApprovalRequestService()
    monkeypatch.setattr(
        "agent.services.approval_request_service._engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.routes.approvals.get_approval_request_service",
        lambda: service,
    )
    monkeypatch.setattr(
        "agent.services.approval_decision_dispatcher_service.ApprovalDecisionDispatcherService.dispatch",
        lambda self, approval: {
            "status": "ignored",
            "reason_code": "test_dispatch_skipped",
        },
    )
    approval = service.create_pending_request(
        task_id="task-api-approval",
        goal_id="goal-api-approval",
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        arguments={"plan_id": "plan-api-approval"},
        target_fingerprint="digest-api-approval",
    )

    forbidden = client.post(
        f"/api/approvals/{approval.id}/decision",
        headers=user_auth_header,
        json={"decision": "granted"},
    )

    assert forbidden.status_code == 403
    assert service.get_request(approval.id).status == "pending"

    granted = client.post(
        f"/api/approvals/{approval.id}/decision",
        headers=admin_auth_header,
        json={"decision": "granted"},
    )

    assert granted.status_code == 200
    assert granted.get_json()["status"] == "granted"


def test_granted_recovery_action_is_reconciled_after_dispatch_interruption(
    monkeypatch,
):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    service = ApprovalRequestService()
    monkeypatch.setattr(
        "agent.services.approval_request_service._engine",
        lambda: engine,
    )
    approval = service.create_pending_request(
        task_id="task-reconcile",
        goal_id="goal-reconcile",
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        arguments={"plan_id": "plan-reconcile"},
        target_fingerprint="digest-reconcile",
    )
    monkeypatch.setattr(
        "agent.services.approval_decision_dispatcher_service.ApprovalDecisionDispatcherService.dispatch",
        lambda self, row: {
            "status": "ignored",
            "reason_code": "simulated_process_interruption",
        },
    )
    service.decide_request(
        approval.id,
        decision="granted",
        decided_by="admin",
    )
    assert service.get_request(approval.id).status == "granted"

    dispatched = []

    def resume_dispatch(_self, row):
        dispatched.append(row.id)
        return {
            "status": "materialized",
            "reason_code": "approved_plan_materialized",
            "plan_id": "plan-reconcile",
        }

    monkeypatch.setattr(
        "agent.services.approval_decision_dispatcher_service.ApprovalDecisionDispatcherService.dispatch",
        resume_dispatch,
    )
    counts = service.reconcile_granted_domain_actions()

    assert counts == {
        "examined": 1,
        "completed": 1,
        "failed": 0,
        "in_progress": 0,
    }
    assert dispatched == [approval.id]
    persisted = service.get_request(approval.id)
    assert persisted.scope["decision_outcome"]["status"] == "materialized"


def test_recovery_approval_details_are_team_scoped(
    app,
    monkeypatch,
):
    goal = Record(id="goal-private", team_id="team-b")
    repos = Record(goal_repo=Record(get_by_id=lambda goal_id: (goal if goal_id == goal.id else None)))
    monkeypatch.setattr(
        "agent.services.repository_registry.get_repository_registry",
        lambda: repos,
    )
    approval = Record(
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        goal_id=goal.id,
    )

    with app.test_request_context("/api/approvals"):
        g.is_admin = False
        g.user = {"sub": "user-a", "team_id": "team-a"}
        assert _can_view_approval(approval) is False

        g.user = {"sub": "user-b", "team_id": "team-b"}
        assert _can_view_approval(approval) is True

        g.is_admin = True
        g.user = {"sub": "admin"}
        assert _can_view_approval(approval) is True


def test_global_recovery_policy_is_inherited_when_task_has_no_strategy_override():
    global_policy = {
        "context_recovery_strategies": [
            "segment_planning",
            "propose_task_plan",
            "require_approval",
            "stop",
        ],
        "require_approval_for_generated_plan": True,
    }
    service, task, _repos, approvals, planner, _planning, _updates, failures = _fixture(routing_policy=global_policy)
    task.worker_execution_context["model_routing"] = {
        "preferred_profile_id": "local_ollama_phi4_mini",
    }

    result = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert result["status"] == "pending_approval"
    assert len(planner.calls) == 1
    assert len(approvals.created) == 1


def test_explicit_empty_recovery_strategy_list_disables_global_inheritance():
    global_policy = {
        "context_recovery_strategies": [
            "propose_task_plan",
            "require_approval",
            "stop",
        ],
        "require_approval_for_generated_plan": True,
    }
    service, task, _repos, approvals, planner, _planning, _updates, failures = _fixture(routing_policy=global_policy)
    task.worker_execution_context["model_routing"] = {
        "preferred_profile_id": "local_ollama_phi4_mini",
        "context_recovery_strategies": [],
    }

    result = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert result == {
        "status": "ignored",
        "reason_code": "recovery_plan_not_configured",
    }
    assert planner.calls == []
    assert approvals.created == []


def test_invalid_explicit_routing_does_not_fall_back_to_global_recovery():
    global_policy = {
        "context_recovery_strategies": [
            "propose_task_plan",
            "require_approval",
            "stop",
        ],
        "require_approval_for_generated_plan": True,
    }
    service, task, _repos, approvals, planner, _planning, _updates, failures = _fixture(routing_policy=global_policy)
    task.worker_execution_context["model_routing"] = {
        "context_recovery_strategies": ["propose_task_plan"],
        "require_approval_for_generated_plan": False,
    }

    result = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert result == {
        "status": "ignored",
        "reason_code": "recovery_plan_not_configured",
    }
    assert planner.calls == []
    assert approvals.created == []


def test_policy_terminal_failure_blocks_recovery_even_with_recoverable_signal():
    service, task, _repos, approvals, planner, _planning, _updates, failures = _fixture()
    failures.append(
        {
            "failure_type": "invalid_proposal",
            "fallback_decisions": [
                {
                    "terminal": True,
                    "trigger": "policy_blocked",
                    "reason": "cloud_escalation_not_allowed",
                }
            ],
        }
    )

    result = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert result == {
        "status": "ignored",
        "reason_code": "model_exhaustion_signal_required",
    }
    assert planner.calls == []
    assert approvals.created == []


def test_granted_approval_does_not_resurrect_a_terminal_source_task():
    service, task, _repos, approvals, _planner, planning, updates, failures = _fixture()
    service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    task.status = "completed"
    update_count_before_grant = len(updates)

    result = service.handle_approval_decision(approval)

    assert result["status"] == "stopped"
    assert result["reason_code"] == "recovery_source_terminal"
    assert result["approval_status"] == "consumed"
    assert planning.calls == []
    assert approvals.consumed == [approval.id]
    assert len(updates) == update_count_before_grant


def test_granted_approval_treats_skipped_source_as_terminal():
    service, task, _repos, approvals, _planner, planning, updates, failures = _fixture()
    service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    task.status = "skipped"
    update_count_before_grant = len(updates)

    result = service.handle_approval_decision(approval)

    assert result["status"] == "stopped"
    assert result["reason_code"] == "recovery_source_terminal"
    assert result["approval_status"] == "consumed"
    assert planning.calls == []
    assert approvals.consumed == [approval.id]
    assert len(updates) == update_count_before_grant


def _partial_materialization_fixture(monkeypatch):
    from agent.services.planning_service import PlanningService

    plan = Record(
        id="plan-partial",
        goal_id="goal-partial",
        trace_id="trace-partial",
        status="pending_approval",
        planning_mode="task_recovery",
        rationale={"approval_request_id": "approval-partial"},
        updated_at=0.0,
    )
    node_repo = MemoryNodeRepo()
    nodes = []
    for index in (1, 2):
        node = Record(
            id=f"partial-node-{index}",
            plan_id=plan.id,
            node_key=f"partial-key-{index}",
            title=f"Partial step {index}",
            description=f"Create partial artifact {index}.",
            priority="High",
            status="pending",
            position=index,
            depends_on=([] if index == 1 else ["partial-key-1"]),
            rationale={
                "task_kind": "coding",
                "retrieval_intent": "repo",
                "required_context_scope": "task",
                "preferred_bundle_mode": "focused",
                "required_capabilities": ["coding"],
            },
            editable=True,
            materialized_task_id=None,
            verification_spec={"required": True},
            updated_at=0.0,
        )
        node_repo.save(node)
        nodes.append(node)
    plan.rationale["plan_digest"] = calculate_recovery_plan_digest(
        plan,
        nodes,
    )
    plan_repo = MemoryPlanRepo()
    plan_repo.save(plan)
    task_repo = MemoryTaskRepo()
    repos = Record(
        plan_repo=plan_repo,
        plan_node_repo=node_repo,
        task_repo=task_repo,
        goal_repo=Record(
            get_by_id=lambda goal_id: (
                Record(id=goal_id, status="planned")
                if goal_id == plan.goal_id
                else None
            )
        ),
    )
    service = PlanningService()
    staged = service._prepare_materialization(
        nodes,
        deterministic_seed=plan.id,
    )
    assert staged is not None

    def task_from_entry(entry):
        node = entry["node"]
        rationale = dict(node.rationale or {})
        return Record(
            id=entry["task_id"],
            goal_id=plan.goal_id,
            goal_trace_id=plan.trace_id,
            plan_id=plan.id,
            plan_node_id=node.id,
            team_id="team-partial",
            parent_task_id=None,
            source_task_id="source-partial",
            derivation_reason="goal_task_recovery",
            derivation_depth=1,
            status="paused",
            title=node.title,
            description=node.description,
            priority=node.priority,
            task_kind=rationale["task_kind"],
            retrieval_intent=rationale["retrieval_intent"],
            required_context_scope=rationale["required_context_scope"],
            preferred_bundle_mode=rationale["preferred_bundle_mode"],
            required_capabilities=list(rationale["required_capabilities"]),
            verification_spec=dict(node.verification_spec),
            depends_on=list(entry["depends_on"]),
        )

    task_repo.task_from_entry = task_from_entry
    task_repo.save(task_from_entry(staged[0]))
    lifecycle_calls = []

    class Lifecycle:
        def materialize_from_plan_node(self, **values):
            lifecycle_calls.append(values["task_id"])
            entry = next(item for item in staged if item["task_id"] == values["task_id"])
            task_repo.save(task_from_entry(entry))

    monkeypatch.setattr(
        "agent.services.planning_service.get_repository_registry",
        lambda: repos,
    )
    monkeypatch.setattr(
        "agent.services.planning_service.get_task_lifecycle_service",
        lambda: Lifecycle(),
    )
    monkeypatch.setattr(
        service,
        "_validate_existing_plan_for_materialization",
        lambda **_values: {
            "ok": True,
            "reason_code": "validated",
        },
    )
    return service, plan, nodes, staged, task_repo, lifecycle_calls


def test_partial_deterministic_materialization_repairs_and_resumes(
    monkeypatch,
):
    (
        service,
        plan,
        nodes,
        staged,
        _task_repo,
        lifecycle_calls,
    ) = _partial_materialization_fixture(monkeypatch)

    result = service.materialize_existing_plan(
        planner=Record(_stats={"tasks_created": 0}),
        plan_id=plan.id,
        approval_request_id="approval-partial",
        team_id="team-partial",
        source_task_id="source-partial",
        expected_plan_digest=plan.rationale["plan_digest"],
        initial_task_status="paused",
    )

    assert result["status"] == "materialized"
    assert result["created_task_ids"] == [entry["task_id"] for entry in staged]
    assert lifecycle_calls == [staged[1]["task_id"]]
    assert [node.materialized_task_id for node in nodes] == [entry["task_id"] for entry in staged]


def test_partial_materialization_binding_conflict_fails_closed(
    monkeypatch,
):
    (
        service,
        plan,
        _nodes,
        staged,
        task_repo,
        lifecycle_calls,
    ) = _partial_materialization_fixture(monkeypatch)
    task_repo.rows[staged[0]["task_id"]].team_id = "other-team"

    result = service.materialize_existing_plan(
        planner=Record(_stats={"tasks_created": 0}),
        plan_id=plan.id,
        approval_request_id="approval-partial",
        team_id="team-partial",
        source_task_id="source-partial",
        expected_plan_digest=plan.rationale["plan_digest"],
        initial_task_status="paused",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == ("materialization_binding_conflict")
    assert lifecycle_calls == []
    assert plan.status == "failed"


def test_existing_plan_repairs_interrupted_approval_source_saga():
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        planning,
        _updates,
        failures,
    ) = _fixture()
    first = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    plan = repos.plan_repo.get_by_id(first["plan_id"])
    plan.rationale.pop("approval_request_id")
    task.status = "proposing"
    task.status_reason_details = {}
    task.verification_status = {}

    resumed = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert resumed["reason_code"] == "recovery_plan_saga_resumed"
    assert resumed["approval_request_id"] == first["approval_request_id"]
    assert len(approvals.created) == 1
    assert task.status == "waiting_for_review"
    assert task.status_reason_details["model_recovery"]["approval_request_id"] == first["approval_request_id"]
    assert planning.lock_entries.count(plan.id) >= 2


def test_plan_is_discoverable_after_crash_immediately_after_persistence():
    (
        service,
        task,
        _repos,
        approvals,
        planner,
        _planning,
        _updates,
        failures,
    ) = _fixture()
    persist_plan = planner.plan_goal

    def crash_after_persistence(**values):
        persist_plan(**values)
        raise RuntimeError("simulated_hub_crash")

    planner.plan_goal = crash_after_persistence
    with pytest.raises(RuntimeError, match="simulated_hub_crash"):
        service.propose_after_model_exhaustion(
            task=task,
            strategy_failures=failures,
        )
    planner.plan_goal = persist_plan

    resumed = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )

    assert resumed["reason_code"] == "recovery_plan_saga_resumed"
    assert resumed["plan_id"] == "plan-recovery-1"
    assert len(planner.calls) == 1
    assert len(approvals.created) == 1


def test_authoritative_source_guard_rejects_second_failure_fingerprint():
    (
        service,
        task,
        repos,
        approvals,
        planner,
        _planning,
        _updates,
        failures,
    ) = _fixture()
    first = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    stale_task = Record(**task.model_dump())
    stale_task.status = "proposing"
    stale_task.status_reason_details = {}
    stale_task.verification_status = {}
    distinct_failures = [
        {
            **dict(failures[0]),
            "model_recovery_signal": {
                **dict(failures[0]["model_recovery_signal"]),
                "attempt_count": 6,
            },
        }
    ]

    second = service.propose_after_model_exhaustion(
        task=stale_task,
        strategy_failures=distinct_failures,
    )

    assert first["plan_id"] == "plan-recovery-1"
    assert second["reason_code"] == ("recovery_plan_already_exists_for_source")
    assert second["plan_id"] == first["plan_id"]
    assert len(planner.calls) == 2
    assert len(approvals.created) == 1
    assert repos.plan_repo.get_by_id("plan-recovery-2").status == ("rejected")


def test_release_cancels_children_when_source_cas_loses_terminal_race(
    monkeypatch,
):
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        _planning,
        updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    original_update = service._conditional_update_task

    def race_update(task_id, status, **values):
        if task_id == task.id and status == "blocked_by_dependency":
            task.status = "completed"
            return False
        return original_update(task_id, status, **values)

    monkeypatch.setattr(
        service,
        "_conditional_update_task",
        race_update,
    )

    result = service.handle_approval_decision(approval)

    assert result["reason_code"] == ("recovery_source_transition_conflict")
    assert result["children_cancelled"] is True
    assert task.status == "completed"
    assert [entry[0:2] for entry in updates[-2:]] == [
        ("child-1", "cancelled"),
        ("child-2", "cancelled"),
    ]
    assert repos.plan_repo.get_by_id(proposal["plan_id"]).rationale["materialization_release_state"] == "cancelled"


def test_cancelled_release_is_never_reclassified_completed():
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        planning,
        updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    plan = repos.plan_repo.get_by_id(proposal["plan_id"])
    plan.status = "materialized"
    plan.rationale["materialization_release_state"] = "cancelled"
    nodes = repos.plan_node_repo.get_by_plan_id(plan.id)
    for index, node in enumerate(nodes[:2], start=1):
        node.materialized_task_id = f"child-{index}"
    approval = approvals.created[0]
    approval.status = "consumed"
    update_count = len(updates)

    result = service.handle_approval_decision(approval)

    assert result["reason_code"] == "recovery_release_cancelled"
    assert result["children_cancelled"] is True
    assert planning.calls == []
    assert len(updates) == update_count
    assert plan.rationale["materialization_release_state"] == ("cancelled")


def test_recovery_grant_expiry_cannot_overwrite_consumed(
    monkeypatch,
):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    service = ApprovalRequestService()
    monkeypatch.setattr(
        "agent.services.approval_request_service._engine",
        lambda: engine,
    )
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    approval = service.create_pending_request(
        task_id="task-cas",
        goal_id="goal-cas",
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        arguments={"plan_id": "plan-cas"},
        target_fingerprint="digest-cas",
    )
    with Session(engine) as session:
        row = session.get(ApprovalRequestDB, approval.id)
        row.status = "granted"
        row.decided_at = time.time() - 10
        row.decided_by = "admin"
        row.expires_at = time.time() - 1
        session.add(row)
        session.commit()

    assert service.expire_old_requests() == 0
    assert service.consume_request(approval.id).status == "consumed"
    assert service.expire_old_requests() == 0
    assert service.get_request(approval.id).status == "consumed"


def test_denied_recovery_action_is_reconciled_after_dispatch_crash(
    monkeypatch,
):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    service = ApprovalRequestService()
    monkeypatch.setattr(
        "agent.services.approval_request_service._engine",
        lambda: engine,
    )
    monkeypatch.setattr(service, "_audit", lambda *_args, **_kwargs: None)
    approval = service.create_pending_request(
        task_id="task-denied",
        goal_id="goal-denied",
        tool_name=RECOVERY_MATERIALIZE_TOOL,
        arguments={"plan_id": "plan-denied"},
        target_fingerprint="digest-denied",
    )
    monkeypatch.setattr(
        "agent.services.approval_decision_dispatcher_service.ApprovalDecisionDispatcherService.dispatch",
        lambda self, row: {
            "status": "ignored",
            "reason_code": "simulated_crash_before_domain_effect",
        },
    )
    service.decide_request(
        approval.id,
        decision="denied",
        decided_by="admin",
    )
    dispatched = []

    def reconcile_denial(_self, row):
        dispatched.append(row.id)
        return {
            "status": "denied",
            "reason_code": "recovery_plan_denied",
            "plan_id": "plan-denied",
        }

    monkeypatch.setattr(
        "agent.services.approval_decision_dispatcher_service.ApprovalDecisionDispatcherService.dispatch",
        reconcile_denial,
    )

    counts = service.reconcile_granted_domain_actions()

    assert counts == {
        "examined": 1,
        "completed": 1,
        "failed": 0,
        "in_progress": 0,
    }
    assert dispatched == [approval.id]
    persisted = service.get_request(approval.id)
    assert persisted.status == "denied"
    assert persisted.scope["decision_outcome"]["status"] == "denied"


def test_denied_recovery_requires_confirmed_source_transition(
    monkeypatch,
):
    (
        service,
        task,
        _repos,
        approvals,
        _planner,
        _planning,
        _updates,
        failures,
    ) = _fixture()
    service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "denied"
    monkeypatch.setattr(
        service,
        "_conditional_update_task",
        lambda *_args, **_kwargs: False,
    )

    result = service.handle_approval_decision(approval)

    assert result["status"] == "failed"
    assert result["reason_code"] == ("recovery_source_transition_conflict")
    assert task.status == "waiting_for_review"


def test_task_status_compare_and_set_rejects_stale_source_state(
    monkeypatch,
):
    from agent.services.task_runtime_service import (
        compare_and_set_local_task_status,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="task-status-cas",
                status="waiting_for_review",
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.services.task_runtime_service._task_engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )

    assert (
        compare_and_set_local_task_status(
            "task-status-cas",
            "blocked_by_dependency",
            expected_statuses={"proposing"},
        )
        is False
    )
    assert (
        compare_and_set_local_task_status(
            "task-status-cas",
            "blocked_by_dependency",
            expected_statuses={"waiting_for_review"},
        )
        is True
    )
    with Session(engine) as session:
        assert session.get(TaskDB, "task-status-cas").status == ("blocked_by_dependency")


def test_release_commits_epoch_before_root_becomes_dispatchable(
    monkeypatch,
):
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        _planning,
        _updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    original_conditional_update = service._conditional_update_task
    root_observations = []

    def observe_release(task_id, status, **values):
        if task_id == "child-1" and status == "todo":
            rationale = dict(
                repos.plan_repo.get_by_id(
                    proposal["plan_id"]
                ).rationale
            )
            root_observations.append(
                {
                    "state": rationale.get(
                        "materialization_release_state"
                    ),
                    "epoch": rationale.get(
                        "materialization_release_epoch"
                    ),
                    "child_gate": dict(
                        values["status_reason_details"][
                            "model_recovery_release"
                        ]
                    ),
                }
            )
        return original_conditional_update(
            task_id,
            status,
            **values,
        )

    monkeypatch.setattr(
        service,
        "_conditional_update_task",
        observe_release,
    )

    result = service.handle_approval_decision(approval)

    assert result["status"] == "materialized"
    assert len(root_observations) == 1
    observed = root_observations[0]
    assert observed["state"] == "committed"
    assert observed["epoch"]
    assert observed["child_gate"]["release_epoch"] == observed["epoch"]
    assert observed["child_gate"]["team_id"] == "team-1"
    plan = repos.plan_repo.get_by_id(proposal["plan_id"])
    assert plan.rationale["materialization_release_state"] == (
        "completed"
    )
    source_recovery = task.status_reason_details["model_recovery"]
    assert source_recovery["release_epoch"] == observed["epoch"]


def test_recovery_dispatch_gate_requires_epoch_and_live_owners():
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateService,
    )

    release_epoch = "release-epoch-1"
    plan = Record(
        id="plan-gate",
        goal_id="goal-gate",
        status="materialized",
        rationale={
            "team_id": "team-gate",
            "source_task_id": "source-gate",
            "recovery_key": "recovery-key-gate",
            "materialization_release_state": "committed",
            "materialization_release_epoch": release_epoch,
            "materialization_release_source_task_id": (
                "source-gate"
            ),
            "materialization_release_goal_id": "goal-gate",
            "materialization_release_approval_id": (
                "approval-gate"
            ),
            "materialization_release_team_id": "team-gate",
        },
    )
    goal = Record(
        id="goal-gate",
        team_id="team-gate",
        status="in_progress",
    )
    source = Record(
        id="source-gate",
        goal_id=goal.id,
        team_id="team-gate",
        status="blocked_by_dependency",
        status_reason_details={
            "model_recovery": {
                "plan_id": plan.id,
                "release_epoch": release_epoch,
                "approval_request_id": "approval-gate",
                "recovery_key": "recovery-key-gate",
            }
        },
    )
    child = Record(
        id="child-gate",
        goal_id=goal.id,
        plan_id=plan.id,
        source_task_id=source.id,
        team_id="team-gate",
        derivation_reason="goal_task_recovery",
        status="todo",
        status_reason_details={
            "model_recovery_release": {
                "release_epoch": release_epoch,
                "plan_id": plan.id,
                "source_task_id": source.id,
                "goal_id": goal.id,
                "team_id": "team-gate",
                "approval_request_id": "approval-gate",
                "recovery_key": "recovery-key-gate",
            }
        },
    )
    node = _bind_recovery_dispatch_fixture(
        plan=plan,
        goal=goal,
        child=child,
    )
    tasks = {source.id: source, child.id: child}
    repos = Record(
        plan_repo=Record(
            get_by_id=lambda plan_id: (
                plan if plan_id == plan.id else None
            )
        ),
        task_repo=Record(
            get_by_id=lambda task_id: tasks.get(task_id)
        ),
        goal_repo=Record(
            get_by_id=lambda goal_id: (
                goal if goal_id == goal.id else None
            )
        ),
        plan_node_repo=Record(
            get_by_plan_id=lambda plan_id: (
                [node] if plan_id == plan.id else []
            )
        ),
    )
    gate = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
    )

    assert gate.evaluate_task(child).allowed is True
    plan.rationale["materialization_release_state"] = "preparing"
    assert gate.evaluate_task(child).reason_code == (
        "recovery_release_not_committed"
    )
    plan.rationale["materialization_release_state"] = "committed"
    child.status_reason_details[
        "model_recovery_release"
    ]["release_epoch"] = "stale-epoch"
    assert gate.evaluate_task(child).reason_code == (
        "recovery_release_epoch_mismatch"
    )
    child.status_reason_details[
        "model_recovery_release"
    ]["release_epoch"] = release_epoch
    source.status = "completed"
    assert gate.evaluate_task(child).reason_code == (
        "recovery_dispatch_owner_terminal"
    )


def _dispatch_lease_fixture():
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateService,
    )

    release_epoch = "release-lease-epoch"
    plan = Record(
        id="plan-lease",
        goal_id="goal-lease",
        status="materialized",
        rationale={
            "team_id": "team-lease",
            "source_task_id": "source-z",
            "recovery_key": "recovery-key-lease",
            "materialization_release_state": "committed",
            "materialization_release_epoch": release_epoch,
            "materialization_release_source_task_id": "source-z",
            "materialization_release_goal_id": "goal-lease",
            "materialization_release_approval_id": "approval-lease",
            "materialization_release_team_id": "team-lease",
        },
    )
    goal = Record(
        id="goal-lease",
        team_id="team-lease",
        status="in_progress",
    )
    source = Record(
        id="source-z",
        goal_id=goal.id,
        team_id="team-lease",
        status="blocked_by_dependency",
        status_reason_details={
            "model_recovery": {
                "plan_id": plan.id,
                "release_epoch": release_epoch,
                "approval_request_id": "approval-lease",
                "recovery_key": "recovery-key-lease",
            }
        },
    )
    child = Record(
        id="child-a",
        goal_id=goal.id,
        goal_trace_id="trace-lease",
        plan_id=plan.id,
        source_task_id=source.id,
        team_id="team-lease",
        derivation_reason="goal_task_recovery",
        status="todo",
        status_reason_code=None,
        required_capabilities=["coding"],
        verification_status={},
        history=[],
        last_output=None,
        last_exit_code=None,
        updated_at=0.0,
        status_reason_details={
            "model_recovery_release": {
                "release_epoch": release_epoch,
                "plan_id": plan.id,
                "source_task_id": source.id,
                "goal_id": goal.id,
                "team_id": "team-lease",
                "approval_request_id": "approval-lease",
                "recovery_key": "recovery-key-lease",
            }
        },
    )
    node = _bind_recovery_dispatch_fixture(
        plan=plan,
        goal=goal,
        child=child,
    )
    tasks = {source.id: source, child.id: child}

    class TaskRepo:
        def get_by_id(self, task_id):
            return tasks.get(task_id)

        def save(self, task):
            tasks[task.id] = task
            return task

    class LockPort:
        @contextlib.contextmanager
        def mutation_lock(self, _task_id):
            yield True

        @contextlib.contextmanager
        def mutation_locks(self, _task_ids):
            yield True

    repos = Record(
        task_repo=TaskRepo(),
        goal_repo=Record(
            get_by_id=lambda goal_id: (
                goal if goal_id == goal.id else None
            )
        ),
        plan_repo=Record(
            get_by_id=lambda plan_id: (
                plan if plan_id == plan.id else None
            )
        ),
        plan_node_repo=Record(
            get_by_plan_id=lambda plan_id: (
                [node] if plan_id == plan.id else []
            )
        ),
    )
    gate = RecoveryDispatchGateService(
        repository_provider=lambda: repos,
        mutation_lock_provider=lambda: LockPort(),
    )
    return gate, repos, goal, source, child


def test_recovery_dispatch_lease_lifecycle_is_bound_and_single_effect():
    from agent.services.recovery_dispatch_gate_service import (
        build_recovery_result_candidate,
    )

    gate, repos, _goal, _source, child = (
        _dispatch_lease_fixture()
    )

    lease = gate.acquire_dispatch_lease(
        child.id,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    assert lease.allowed is True
    assert lease.token
    persisted = child.status_reason_details[
        "recovery_dispatch_lease"
    ]
    assert lease.token not in str(persisted)
    assert persisted["state"] == "active"

    wrong = gate.admit_dispatch_lease(
        child.id,
        token="wrong-token",
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    )
    assert wrong.allowed is False
    assert wrong.reason_code == "recovery_dispatch_lease_mismatch"

    admitted = gate.admit_dispatch_lease(
        child.id,
        token=lease.token,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    )
    assert admitted.reason_code == (
        "recovery_dispatch_worker_admitted"
    )
    readmitted = gate.admit_dispatch_lease(
        child.id,
        token=lease.token,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    )
    assert readmitted.reason_code == (
        "recovery_dispatch_worker_readmitted"
    )

    persisted_lease = child.status_reason_details[
        "recovery_dispatch_lease"
    ]
    child.status = "in_progress"
    child.last_output = "Hub-verified recovery output"
    child.last_exit_code = 0
    child.verification_status = {
        "status": "passed",
        "record_id": "verification-record-lease",
        "results": {"final_passed": True},
    }
    child.status_reason_details[
        "recovery_result_candidate"
    ] = build_recovery_result_candidate(
        task_id=child.id,
        status="completed",
        verification_record_id="verification-record-lease",
        lease_revision=persisted_lease["revision"],
        lease_token_digest=persisted_lease["token_digest"],
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    repos.task_repo.save(child)

    with gate.result_guard(
        child.id,
        token=lease.token,
        phase="execute",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        worker_url="http://worker:5000",
    ) as accepted:
        assert accepted.allowed is True
    accepted_child = repos.task_repo.get_by_id(child.id)
    assert accepted_child.status == "completed"
    assert accepted_child.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "result_accepted"
    assert gate.revoke_dispatch_lease(
        child.id,
        reason_code="late_transport_timeout",
    ) is False
    assert repos.task_repo.get_by_id(
        child.id
    ).status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "result_accepted"
    replay = gate.admit_dispatch_lease(
        child.id,
        token=lease.token,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    )
    assert replay.allowed is False


def test_recovery_dispatch_lease_rechecks_terminal_owner_at_admission_and_result():
    gate, _repos, goal, source, child = (
        _dispatch_lease_fixture()
    )
    lease = gate.acquire_dispatch_lease(
        child.id,
        phase="propose",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    goal.status = "archived"
    denied = gate.admit_dispatch_lease(
        child.id,
        token=lease.token,
        phase="propose",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    )
    assert denied.reason_code == "recovery_dispatch_owner_terminal"

    goal.status = "in_progress"
    second = gate.acquire_dispatch_lease(
        child.id,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    assert second.allowed is False
    assert second.decision.reason_code == "recovery_dispatch_inflight"
    child.status_reason_details[
        "recovery_dispatch_lease"
    ]["expires_at"] = time.time() - 1
    replacement = gate.acquire_dispatch_lease(
        child.id,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    assert replacement.allowed is True
    assert gate.admit_dispatch_lease(
        child.id,
        token=replacement.token,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        trusted_local=True,
    ).allowed
    source.status = "completed"
    with gate.result_guard(
        child.id,
        token=replacement.token,
        phase="execute",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        worker_url="http://worker:5000",
    ) as result:
        assert result.allowed is False
        assert result.reason_code == (
            "recovery_dispatch_owner_terminal"
        )


def test_source_terminal_transition_and_recovery_admission_are_linearizable():
    from agent.services.task_mutation_lock_service import (
        TaskMutationLockPort,
    )

    gate, _repos, _goal, source, child = (
        _dispatch_lease_fixture()
    )
    port = TaskMutationLockPort(
        engine_provider=lambda: Record(
            dialect=Record(name="sqlite")
        )
    )
    gate._mutation_lock_provider = lambda: port
    lease = gate.acquire_dispatch_lease(
        child.id,
        phase="execute",
        worker_url="http://worker:5000",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
    )
    assert lease.allowed is True
    start = threading.Barrier(2)
    admission = {}
    failures = []

    def admit():
        try:
            start.wait(timeout=5)
            admission["decision"] = gate.admit_dispatch_lease(
                child.id,
                token=lease.token,
                phase="execute",
                worker_url="http://worker:5000",
                request_fingerprint=(
                    _DISPATCH_REQUEST_FINGERPRINT
                ),
                trusted_local=True,
            )
        except BaseException as exc:
            failures.append(exc)

    def terminalize_source():
        try:
            start.wait(timeout=5)
            with port.mutation_lock(source.id) as acquired:
                assert acquired is True
                source.status = "completed"
        except BaseException as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=admit),
        threading.Thread(target=terminalize_source),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    assert all(not thread.is_alive() for thread in threads)
    assert admission["decision"].reason_code in {
        "recovery_dispatch_worker_admitted",
        "recovery_dispatch_owner_terminal",
    }
    with gate.result_guard(
        child.id,
        token=lease.token,
        phase="execute",
        request_fingerprint=_DISPATCH_REQUEST_FINGERPRINT,
        worker_url="http://worker:5000",
    ) as result:
        assert result.allowed is False
        assert result.reason_code == (
            "recovery_dispatch_owner_terminal"
        )


def test_recovery_worker_readmission_returns_cached_outcome_without_reexecution(
    monkeypatch,
):
    from agent.services import _task_scoped_step_orchestrator as orchestration

    orchestration._RECOVERY_OUTCOME_CACHE.clear()
    token = "same-invocation-token"
    admissions = {"count": 0}
    executions = {"count": 0}
    expected = Record(status="success", data={"output": "once"})

    def admit(**_values):
        admissions["count"] += 1
        return {
            "error": None,
            "token": token,
            "worker_url": "http://worker:5000",
            "guard_local_result": False,
            "replayed": admissions["count"] > 1,
        }

    def execute(*_args, **_kwargs):
        executions["count"] += 1
        return expected

    monkeypatch.setattr(
        orchestration,
        "_admit_task_scoped_dispatch",
        admit,
    )
    monkeypatch.setattr(
        orchestration,
        "_run_execute_step_admitted",
        execute,
    )
    request = Record(
        dispatch_lease_token=token,
        dispatch_lease_phase="execute",
    )
    service = Record(
        _require_task=lambda _tid: {
            "id": "cached-child",
            "derivation_reason": "goal_task_recovery",
        }
    )
    first = orchestration.run_execute_step(
        service,
        "cached-child",
        request,
        forwarder=lambda *_args, **_kwargs: None,
    )
    second = orchestration.run_execute_step(
        service,
        "cached-child",
        request,
        forwarder=lambda *_args, **_kwargs: None,
    )
    assert first is expected
    assert second is expected
    assert executions["count"] == 1


def test_task_scoped_recovery_forward_preserves_lease_and_fences_result(
    app,
    monkeypatch,
):
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    lease_token = "recovery-propose-lease"
    worker_url = "http://worker-recovery:5001"
    forwarded = []
    accepted = []
    guarded = []

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

        @contextlib.contextmanager
        def result_guard(
            self,
            task_id,
            *,
            token,
            phase,
            request_fingerprint,
            worker_url,
        ):
            guarded.append(
                {
                    "task_id": task_id,
                    "token": token,
                    "phase": phase,
                    "request_fingerprint": request_fingerprint,
                    "worker_url": worker_url,
                }
            )
            yield Record(
                allowed=True,
                reason_code="recovery_dispatch_lease_valid",
            )

    gate = Gate()
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: gate,
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(
                    token="current-worker-token"
                )
            )
        ),
    )

    payload = {
        "task_id": "child-forward",
        "dispatch_lease_token": lease_token,
        "dispatch_lease_phase": "propose",
    }

    def forwarder(url, endpoint, data, *, token):
        forwarded.append(
            {
                "url": url,
                "endpoint": endpoint,
                "payload": dict(data),
                "token": token,
            }
        )
        return {"status": "completed", "output": "accepted"}

    with app.app_context():
        response = forwarding.forward_task_request_if_remote(
            tid="child-forward",
            task={
                "id": "child-forward",
                "assigned_agent_url": worker_url,
                "assigned_agent_token": "stale-worker-token",
                "derivation_reason": "goal_task_recovery",
            },
            endpoint="/tasks/child-forward/step/propose",
            payload=payload,
            forwarder=forwarder,
            on_success=lambda data, task: accepted.append(
                (dict(data), dict(task))
            ),
        )

    assert response is not None
    assert response.code == 200
    assert response.data["output"] == "accepted"
    assert forwarded == [
        {
            "url": worker_url,
            "endpoint": "/tasks/child-forward/step/propose",
            "payload": payload,
            "token": "current-worker-token",
        }
    ]
    assert guarded == [
        {
            "task_id": "child-forward",
            "token": lease_token,
            "phase": "propose",
            "request_fingerprint": (
                recovery_dispatch_gate_service
                .recovery_dispatch_request_fingerprint(
                    "propose",
                    payload,
                )
            ),
            "worker_url": worker_url,
        }
    ]
    assert len(accepted) == 1


def test_task_scoped_recovery_forward_without_lease_fails_before_transport(
    app,
    monkeypatch,
):
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import mail_task_service, recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    mail_claims = []
    monkeypatch.setattr(
        mail_task_service,
        "get_mail_task_service",
        lambda: Record(
            claim_for_delegation=lambda **values: mail_claims.append(
                dict(values)
            )
        ),
    )
    forwarded = []
    accepted = []

    with app.app_context():
        response = forwarding.forward_task_request_if_remote(
            tid="child-without-lease",
            task={
                "id": "child-without-lease",
                "assigned_agent_url": "http://worker:5001",
                "derivation_reason": "goal_task_recovery",
                "task_kind": "mail_operation",
            },
            endpoint="/tasks/child-without-lease/step/execute",
            payload={"task_id": "child-without-lease"},
            forwarder=lambda *_args, **_kwargs: forwarded.append(True),
            on_success=lambda *_args: accepted.append(True),
        )

    assert response is not None
    assert response.code == 409
    assert response.data["reason"] == "recovery_dispatch_lease_missing"
    assert mail_claims == []
    assert forwarded == []
    assert accepted == []


@pytest.mark.parametrize(
    ("guard_allowed", "callback_fails", "guard_commit_fails"),
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
def test_recovery_mail_forward_closes_guard_before_lease_resolution(
    app,
    monkeypatch,
    guard_allowed,
    callback_fails,
    guard_commit_fails,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import mail_task_service, recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

        @contextlib.contextmanager
        def result_guard(self, *_args, **_kwargs):
            events.append("guard_enter")
            try:
                yield Record(
                    allowed=guard_allowed,
                    reason_code=(
                        "recovery_dispatch_lease_valid"
                        if guard_allowed
                        else "recovery_dispatch_lease_stale"
                    ),
                )
            finally:
                events.append("guard_exit")
                if guard_commit_fails:
                    raise RuntimeError("result guard commit failed")

    events = []
    claims = []
    releases = []

    def release_lease(**values):
        events.append("release")
        releases.append(dict(values))
        return True

    mail_service = Record(
        claim_for_delegation=lambda **values: (
            claims.append(dict(values))
            or {"fencing_token": 17}
        ),
        release_lease=release_lease,
    )
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        mail_task_service,
        "get_mail_task_service",
        lambda: mail_service,
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(token="worker-token")
            )
        ),
    )
    accepted = []

    def accept_result(*_args):
        events.append("persist")
        if callback_fails:
            raise RuntimeError("result persistence failed")
        accepted.append(True)

    def call():
        return forwarding.forward_task_request_if_remote(
            tid="recovery-mail-child",
            task={
                "id": "recovery-mail-child",
                "assigned_agent_url": "http://worker:5001",
                "derivation_reason": "goal_task_recovery",
                "task_kind": "mail_operation",
            },
            endpoint="/tasks/recovery-mail-child/step/execute",
            payload={
                "task_id": "recovery-mail-child",
                "dispatch_lease_token": "execute-lease",
                "dispatch_lease_phase": "execute",
            },
            forwarder=lambda *_args, **_kwargs: {
                "status": "completed",
                "output": "guarded",
            },
            on_success=accept_result,
        )

    with app.app_context():
        if callback_fails or guard_commit_fails:
            with pytest.raises(WorkerForwardingError) as raised:
                call()
            assert raised.value.details["details"] == (
                "result persistence failed"
                if callback_fails
                else "result guard commit failed"
            )
            response = None
        else:
            response = call()

    assert len(claims) == 1
    owner_ref = claims[0]["owner_ref"]
    assert claims == [
        {
            "job_id": "recovery-mail-child",
            "owner_ref": owner_ref,
        }
    ]
    assert owner_ref.startswith("hub-worker:")
    if callback_fails or guard_commit_fails:
        assert response is None
        assert accepted == ([] if callback_fails else [True])
        assert releases == []
        assert events == ["guard_enter", "persist", "guard_exit"]
    elif guard_allowed:
        assert releases == [
            {
                "job_id": "recovery-mail-child",
                "fencing_token": 17,
                "owner_ref": owner_ref,
            }
        ]
        assert response.code == 200
        assert accepted == [True]
        assert events == [
            "guard_enter",
            "persist",
            "guard_exit",
            "release",
        ]
    else:
        assert releases == [
            {
                "job_id": "recovery-mail-child",
                "fencing_token": 17,
                "owner_ref": owner_ref,
            }
        ]
        assert response.code == 409
        assert response.data["reason"] == "recovery_dispatch_lease_stale"
        assert accepted == []
        assert events == ["guard_enter", "guard_exit", "release"]


def test_recovery_mail_persistence_defers_release_until_guard_commit(
    monkeypatch,
):
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import (
        mail_task_service,
        recovery_worker_result_service,
    )

    task_id = "recovery-mail-persist"
    authoritative = Record(
        id=task_id,
        status="in_progress",
        history=[],
        last_proposal={},
        verification_status={},
        status_reason_details={},
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            task_repo=Record(
                get_by_id=lambda _task_id: authoritative,
            ),
        ),
    )
    monkeypatch.setattr(
        recovery_worker_result_service,
        "get_recovery_worker_result_service",
        lambda: Record(
            merge_response=lambda **_values: {},
        ),
    )
    validated = []
    released = []

    def validate_worker_result(*, job_id, result):
        validated.append((job_id, dict(result)))
        return dict(result)

    monkeypatch.setattr(
        mail_task_service,
        "get_mail_task_service",
        lambda: Record(
            validate_worker_result=validate_worker_result,
            release_lease=lambda **values: released.append(
                dict(values)
            ),
        ),
    )

    def fail_persistence(*_args, **_kwargs):
        raise RuntimeError("result persistence failed")

    monkeypatch.setattr(
        forwarding,
        "update_local_task_status",
        fail_persistence,
    )
    response = {
        "schema": "ananta.mail_task_result.v1",
        "job_id": task_id,
        "idempotency_key": "mail-idempotency-key",
        "operation": "send",
        "status": "completed",
        "reason_code": None,
        "retryable": False,
        "retry_after_ms": None,
        "provider": "jmap",
        "result_refs": [],
        "counters": {},
        "lease_fencing_token": 17,
    }

    with pytest.raises(RuntimeError, match="result persistence failed"):
        forwarding.persist_forwarded_execution(
            tid=task_id,
            response=response,
            task={
                "id": task_id,
                "task_kind": "mail_operation",
                "derivation_reason": "goal_task_recovery",
            },
            request_data=Record(command=None),
        )

    assert validated == [(task_id, response)]
    assert released == []


def test_task_scoped_recovery_forward_never_retries_without_worker_token(
    app,
    monkeypatch,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(token="worker-token")
            )
        ),
    )
    calls = []

    def unauthorized(_url, _endpoint, data, *, token):
        calls.append((dict(data), token))
        raise RuntimeError("401 unauthorized")

    with app.app_context(), pytest.raises(WorkerForwardingError):
        forwarding.forward_task_request_if_remote(
            tid="child-auth-fenced",
            task={
                "id": "child-auth-fenced",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": "stale-token",
                "derivation_reason": "goal_task_recovery",
            },
            endpoint="/tasks/child-auth-fenced/step/execute",
            payload={
                "task_id": "child-auth-fenced",
                "dispatch_lease_token": "execute-lease",
                "dispatch_lease_phase": "execute",
            },
            forwarder=unauthorized,
            on_success=lambda *_args: None,
        )

    assert calls == [
        (
            {
                "task_id": "child-auth-fenced",
                "dispatch_lease_token": "execute-lease",
                "dispatch_lease_phase": "execute",
            },
            "worker-token",
        )
    ]


@pytest.mark.parametrize(
    "failure_mode",
    ["empty", "unauthorized_response", "not_found", "unauthorized_exception"],
)
def test_vector_index_forward_never_retries_anonymously_or_falls_back_locally(
    app,
    monkeypatch,
    failure_mode,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import (
        recovery_dispatch_gate_service,
        vector_index_task_service,
    )

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(
                    token="current-vector-worker-token"
                )
            )
        ),
    )
    monkeypatch.setattr(
        vector_index_task_service,
        "get_vector_index_task_service",
        lambda: Record(
            issue_dispatch_attempt=lambda **_values: {
                "schema": "ananta.vector_index_task.v1",
                "job_id": "vector-forward-fenced",
            }
        ),
    )
    calls: list[str | None] = []

    def forwarder(_url, _endpoint, _payload, *, token):
        calls.append(token)
        if failure_mode == "empty":
            return None
        if failure_mode == "unauthorized_response":
            return {
                "status": "error",
                "http_status": 401,
                "message": "unauthorized",
            }
        if failure_mode == "not_found":
            return {
                "status": "error",
                "http_status": 404,
                "message": "task not found",
            }
        raise RuntimeError("401 unauthorized")

    with app.app_context(), pytest.raises(WorkerForwardingError):
        forwarding.forward_task_request_if_remote(
            tid="vector-forward-fenced",
            task={
                "id": "vector-forward-fenced",
                "task_kind": "vector_index_operation",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": "stale-vector-worker-token",
                "worker_execution_context": {
                    "vector_index_task": {
                        "schema": "ananta.vector_index_task.v1",
                    }
                },
            },
            endpoint=(
                "/tasks/vector-forward-fenced/step/execute"
            ),
            payload={"task_id": "vector-forward-fenced"},
            forwarder=forwarder,
            on_success=lambda *_args: (_ for _ in ()).throw(
                ValueError("vector result schema required")
            ),
        )

    assert calls == ["current-vector-worker-token"]


def _governed_codecompass_forward_task(
    *,
    worker_url="http://worker:5001",
    schema="ananta.knowledge_index_execution_job.v2",
    include_context=True,
    include_manifest=False,
):
    task = {
        "id": "codecompass-forward-fenced",
        "task_kind": "codecompass_index_build",
        "assigned_agent_url": worker_url,
        "assigned_agent_token": "stale-codecompass-token",
    }
    if include_context:
        job = {
            "schema": schema,
            "resources": {"max_runtime_seconds": 60},
        }
        if include_manifest:
            job["source_access_enforcement_manifest"] = {
                "schema": "ananta.source-control.enforcement-manifest.v1"
            }
        task["worker_execution_context"] = {
            "knowledge_index_job": job,
            "destination_selection": {
                "worker_id": "worker-index-01"
            },
        }
    return task


@pytest.mark.parametrize(
    "failure_mode",
    [
        "empty",
        "unauthorized_response",
        "forbidden_response",
        "not_found",
        "unauthorized_exception",
    ],
)
def test_codecompass_index_forward_never_retries_anonymously_or_falls_back_locally(
    app,
    monkeypatch,
    failure_mode,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service
    from agent.services.worker_forward_transport import (
        WorkerTransportDeadline,
    )

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(
                    name="worker-index-01",
                    url="http://worker:5001",
                    token="current-codecompass-worker-token",
                    registration_validated=True,
                    role="worker",
                    status="online",
                )
            )
        ),
    )
    authorization_calls = []

    def authorize_dispatch(**values):
        authorization_calls.append(values)
        return {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "resources": {"max_runtime_seconds": 60},
                "source_access_enforcement_manifest": {
                    "schema": "test-enforcement-manifest"
                },
            }
        }

    monkeypatch.setattr(
        forwarding,
        "_governed_source_control_index_job_service",
        lambda: Record(
            authorize_bound_worker_dispatch=authorize_dispatch
        ),
    )
    deadline = WorkerTransportDeadline.after_seconds(90)
    monkeypatch.setattr(
        forwarding,
        "_codecompass_execute_deadline",
        lambda **_kwargs: deadline,
    )
    calls: list[str | None] = []

    def forwarder(
        _url,
        _endpoint,
        _payload,
        *,
        token,
        transport_deadline=None,
    ):
        calls.append((token, transport_deadline))
        if failure_mode == "empty":
            return None
        if failure_mode == "unauthorized_response":
            return {
                "status": "error",
                "http_status": 401,
                "message": "unauthorized",
            }
        if failure_mode == "forbidden_response":
            return {
                "status": "error",
                "http_status": 403,
                "message": "forbidden",
            }
        if failure_mode == "not_found":
            return {
                "status": "error",
                "http_status": 404,
                "message": "task not found",
            }
        raise RuntimeError("401 unauthorized")

    with app.app_context(), pytest.raises(WorkerForwardingError) as error:
        forwarding.forward_task_request_if_remote(
            tid="codecompass-forward-fenced",
            task={
                "id": "codecompass-forward-fenced",
                "task_kind": "codecompass_index_build",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": "stale-codecompass-token",
                "worker_execution_context": {
                    "knowledge_index_job": {
                        "schema": "ananta.knowledge_index_execution_job.v2",
                    },
                    "destination_selection": {
                        "worker_id": "worker-index-01"
                    },
                },
            },
            endpoint="/tasks/codecompass-forward-fenced/step/execute",
            payload={"task_id": "codecompass-forward-fenced"},
            forwarder=forwarder,
            on_success=lambda *_args: None,
        )

    assert calls == [("current-codecompass-worker-token", deadline)]
    if failure_mode in {
        "unauthorized_response",
        "forbidden_response",
        "not_found",
    }:
        assert error.value.status_code == {
            "unauthorized_response": 401,
            "forbidden_response": 403,
            "not_found": 404,
        }[failure_mode]
        assert error.value.retryable is False
    else:
        assert error.value.status_code == 502
        assert error.value.retryable is True
    assert authorization_calls == [
        {
            "job_id": "codecompass-forward-fenced",
            "authenticated_worker_id": "worker-index-01",
            "destination_selection": {
                "worker_id": "worker-index-01"
            },
            "dispatch_phase": "execute",
        }
    ]


@pytest.mark.parametrize(
    "task",
    [
        _governed_codecompass_forward_task(include_context=False),
        _governed_codecompass_forward_task(
            schema="ananta.knowledge_index_job.unknown"
        ),
        _governed_codecompass_forward_task(worker_url=None),
    ],
)
def test_codecompass_forward_rejects_missing_governed_binding_or_worker(
    app,
    monkeypatch,
    task,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    calls = []

    with app.app_context(), pytest.raises(WorkerForwardingError) as error:
        forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert calls == []
    assert error.value.status_code == 409
    assert error.value.retryable is False


def test_public_codecompass_v1_keeps_legacy_forwarder_boundary(
    app,
    monkeypatch,
):
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    task = _governed_codecompass_forward_task(
        schema="ananta.knowledge_index_job.v1"
    )
    calls = []
    accepted = []

    def legacy_forwarder(url, endpoint, payload, token=None):
        calls.append((url, endpoint, dict(payload), token))
        return {"status": "success", "data": {"status": "completed"}}

    with app.app_context():
        result = forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=legacy_forwarder,
            on_success=lambda *args: accepted.append(args),
        )

    assert result is not None
    assert len(calls) == 1
    assert calls[0][2] == {"task_id": task["id"]}
    assert calls[0][3] == "stale-codecompass-token"
    assert len(accepted) == 1


@pytest.mark.parametrize(
    "worker_url",
    [
        "http://localhost:5000",
        "http://localhost.:5000",
        "http://[::1]:5000",
        "http://[::ffff:127.0.0.1]:5000",
    ],
)
def test_codecompass_forward_rejects_local_hub_alias(
    app,
    monkeypatch,
    worker_url,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(settings, "port", 5000)
    task = _governed_codecompass_forward_task(
        worker_url=worker_url
    )
    calls = []

    with app.app_context(), pytest.raises(
        WorkerForwardingError,
        match="assigned_worker_must_be_remote",
    ):
        forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert calls == []


def test_codecompass_worker_executes_its_bound_assignment_locally(
    app,
    monkeypatch,
):
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding

    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(settings, "agent_url", "http://worker:5001")
    monkeypatch.setattr(settings, "port", 5001)
    task = _governed_codecompass_forward_task(
        worker_url="http://worker:5001",
    )
    calls = []

    with app.app_context():
        result = forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert result is None
    assert calls == []


@pytest.mark.parametrize(
    ("endpoint_phase", "payload_phase"),
    [("execute", "propose"), ("propose", "execute")],
)
def test_codecompass_forward_rejects_spoofed_dispatch_phase(
    app,
    monkeypatch,
    endpoint_phase,
    payload_phase,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    task = _governed_codecompass_forward_task()
    authorizer_calls = []
    forwarder_calls = []
    monkeypatch.setattr(
        forwarding,
        "_governed_source_control_index_job_service",
        lambda: authorizer_calls.append(True),
    )

    with app.app_context(), pytest.raises(
        WorkerForwardingError,
        match="knowledge_index_dispatch_phase_mismatch",
    ) as error:
        forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=(
                f"/tasks/{task['id']}/step/{endpoint_phase}"
            ),
            payload={
                "task_id": task["id"],
                "dispatch_lease_phase": payload_phase,
            },
            forwarder=lambda *args, **kwargs: forwarder_calls.append(
                (args, kwargs)
            ),
            on_success=lambda *_args: None,
        )

    assert error.value.status_code == 409
    assert error.value.retryable is False
    assert authorizer_calls == []
    assert forwarder_calls == []


@pytest.mark.parametrize(
    ("worker_response", "acceptance_error", "expected_successes", "expected_failures"),
    [
        (
            {"status": "success", "data": {"status": "completed"}},
            None,
            1,
            0,
        ),
        ({"status": "success", "data": []}, None, 0, 1),
        (
            {"status": "success", "data": {"status": "completed"}},
            "result_contract_rejected",
            0,
            1,
        ),
    ],
)
def test_forwarded_worker_outcome_recorded_only_after_result_acceptance(
    app,
    monkeypatch,
    worker_response,
    acceptance_error,
    expected_successes,
    expected_failures,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    class Recorder:
        def __init__(self):
            self.successes = []
            self.failures = []

        def record_worker_forward_success(self, worker_url):
            self.successes.append(worker_url)

        def record_worker_forward_failure(
            self,
            worker_url,
            reason,
            *,
            task_id=None,
            endpoint=None,
        ):
            self.failures.append(
                (worker_url, reason, task_id, endpoint)
            )

    recorder = Recorder()
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_worker_forward_outcome_recorder",
        lambda: recorder,
    )

    def accept(_response, _task):
        if acceptance_error:
            raise ValueError(acceptance_error)

    context = (
        pytest.raises(WorkerForwardingError)
        if expected_failures
        else contextlib.nullcontext()
    )
    with app.app_context(), context:
        forwarding.forward_task_request_if_remote(
            tid="outcome-recorder-task",
            task={
                "id": "outcome-recorder-task",
                "task_kind": "analysis",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": "worker-token",
            },
            endpoint="/tasks/outcome-recorder-task/step/execute",
            payload={"task_id": "outcome-recorder-task"},
            forwarder=lambda *_args, **_kwargs: worker_response,
            on_success=accept,
        )

    assert len(recorder.successes) == expected_successes
    assert len(recorder.failures) == expected_failures
    if expected_successes:
        assert recorder.successes == ["http://worker:5001"]
    if expected_failures:
        assert recorder.failures == [
            (
                "http://worker:5001",
                "forwarded_worker_transport_failed",
                "outcome-recorder-task",
                "/tasks/outcome-recorder-task/step/execute",
            )
        ]


def test_codecompass_projection_pending_is_hub_local_and_never_redispatched(
    app,
    monkeypatch,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service
    from agent.services.knowledge_index_job_service import (
        KnowledgeIndexCompletionProjectionPending,
    )
    from agent.services.worker_forward_transport import (
        WorkerTransportDeadline,
    )

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    class Recorder:
        def __init__(self):
            self.successes = []
            self.failures = []

        def record_worker_forward_success(self, worker_url):
            self.successes.append(worker_url)

        def record_worker_forward_failure(self, *args, **kwargs):
            self.failures.append((args, kwargs))

    recorder = Recorder()
    task = _governed_codecompass_forward_task()
    authorization_calls = []
    transport_calls = []

    def authorize_dispatch(**values):
        authorization_calls.append(dict(values))
        if len(authorization_calls) > 1:
            raise ValueError("knowledge_index_execution_not_dispatchable")
        return {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "resources": {"max_runtime_seconds": 60},
                "source_access_enforcement_manifest": {
                    "schema": "test-enforcement-manifest"
                },
            }
        }

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(
                    name="worker-index-01",
                    url="http://worker:5001",
                    token="current-codecompass-worker-token",
                    registration_validated=True,
                    role="worker",
                    status="online",
                )
            )
        ),
    )

    monkeypatch.setattr(
        forwarding,
        "_governed_source_control_index_job_service",
        lambda: Record(
            authorize_bound_worker_dispatch=authorize_dispatch
        ),
    )
    monkeypatch.setattr(
        forwarding,
        "get_worker_forward_outcome_recorder",
        lambda: recorder,
    )
    monkeypatch.setattr(
        forwarding,
        "_codecompass_execute_deadline",
        lambda **_kwargs: WorkerTransportDeadline.after_seconds(90),
    )

    def forwarder(*_args, **_kwargs):
        transport_calls.append(True)
        return {
            "schema": "ananta.knowledge_index_execution_result.v2",
            "job_id": task["id"],
            "status": "completed",
        }

    def projection_pending(*_args, **_kwargs):
        raise KnowledgeIndexCompletionProjectionPending(
            RuntimeError("source projector unavailable")
        )

    with app.app_context():
        result = forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=forwarder,
            on_success=projection_pending,
        )

        with pytest.raises(
            WorkerForwardingError,
            match="knowledge_index_execution_not_dispatchable",
        ) as retry_error:
            forwarding.forward_task_request_if_remote(
                tid=task["id"],
                task=task,
                endpoint=f"/tasks/{task['id']}/step/execute",
                payload={"task_id": task["id"]},
                forwarder=forwarder,
                on_success=projection_pending,
            )

    assert result.code == 202
    assert result.status == "pending"
    assert result.data == {
        "status": "completion_projection_pending",
        "reason_code": "knowledge_index_source_projection_pending",
        "task_id": task["id"],
        "worker_result_accepted": True,
        "worker_dispatch_retry_allowed": False,
        "reconciliation_required": True,
    }
    assert recorder.successes == ["http://worker:5001"]
    assert recorder.failures == []
    assert transport_calls == [True]
    assert len(authorization_calls) == 2
    assert retry_error.value.status_code == 409
    assert retry_error.value.retryable is False


def test_parallel_codecompass_execute_dispatch_claim_forwards_once(
    app,
    monkeypatch,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service
    from agent.services.worker_forward_transport import (
        WorkerTransportDeadline,
    )

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(
                    name="worker-index-01",
                    url="http://worker:5001",
                    token="current-codecompass-worker-token",
                    registration_validated=True,
                    role="worker",
                    status="online",
                )
            )
        ),
    )
    claim_lock = threading.Lock()
    claimed = False
    authorization_calls = []

    def authorize_dispatch(**values):
        nonlocal claimed
        with claim_lock:
            authorization_calls.append(values)
            if claimed:
                raise ValueError(
                    "knowledge_index_execute_dispatch_already_claimed"
                )
            claimed = True
        return {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "resources": {"max_runtime_seconds": 60},
                "source_access_enforcement_manifest": {
                    "schema": "test-enforcement-manifest"
                },
            }
        }

    monkeypatch.setattr(
        forwarding,
        "_governed_source_control_index_job_service",
        lambda: Record(
            authorize_bound_worker_dispatch=authorize_dispatch
        ),
    )
    deadline = WorkerTransportDeadline.after_seconds(90)
    monkeypatch.setattr(
        forwarding,
        "_codecompass_execute_deadline",
        lambda **_kwargs: deadline,
    )
    forward_calls = []
    accepted_deadlines = []
    forward_lock = threading.Lock()

    def forwarder(
        _url,
        _endpoint,
        _payload,
        *,
        token,
        transport_deadline=None,
    ):
        with forward_lock:
            forward_calls.append((token, transport_deadline))
        return {"status": "success", "data": {"status": "completed"}}

    def accept_success(
        _response,
        _task,
        *,
        transport_deadline,
    ):
        accepted_deadlines.append(transport_deadline)

    start = threading.Barrier(2)

    def dispatch():
        with app.app_context():
            start.wait(timeout=2)
            try:
                result = forwarding.forward_task_request_if_remote(
                    tid="codecompass-forward-fenced",
                    task=_governed_codecompass_forward_task(),
                    endpoint=(
                        "/tasks/codecompass-forward-fenced/step/execute"
                    ),
                    payload={"task_id": "codecompass-forward-fenced"},
                    forwarder=forwarder,
                    on_success=accept_success,
                )
                return ("forwarded", result)
            except WorkerForwardingError as exc:
                return ("rejected", exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=5)
            for future in (executor.submit(dispatch), executor.submit(dispatch))
        ]

    assert sorted(outcome[0] for outcome in outcomes) == [
        "forwarded",
        "rejected",
    ]
    rejected = next(
        outcome[1] for outcome in outcomes if outcome[0] == "rejected"
    )
    assert rejected.status_code == 409
    assert rejected.retryable is False
    assert str(rejected) == "knowledge_index_execute_dispatch_already_claimed"
    assert forward_calls == [
        ("current-codecompass-worker-token", deadline)
    ]
    assert accepted_deadlines == [deadline]
    assert len(authorization_calls) == 2
    assert all(
        call["dispatch_phase"] == "execute"
        for call in authorization_calls
    )


@pytest.mark.parametrize(
    "registered_agent",
    [
        None,
        Record(
            name="worker-index-01",
            url="http://worker:5001",
            token="current-codecompass-worker-token",
            registration_validated=False,
            role="worker",
            status="online",
        ),
        Record(
            name="worker-index-01",
            url="http://worker:5001",
            token="current-codecompass-worker-token",
            registration_validated=True,
            role="hub",
            status="online",
        ),
    ],
)
def test_codecompass_persisted_manifest_still_requires_live_registered_worker(
    app,
    monkeypatch,
    registered_agent,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: registered_agent
            )
        ),
    )
    authorization_calls = []
    monkeypatch.setattr(
        forwarding,
        "_governed_source_control_index_job_service",
        lambda: Record(
            authorize_bound_worker_dispatch=lambda **values: (
                authorization_calls.append(values)
            )
        ),
    )
    task = _governed_codecompass_forward_task(include_manifest=True)
    calls = []

    with app.app_context(), pytest.raises(WorkerForwardingError):
        forwarding.forward_task_request_if_remote(
            tid=task["id"],
            task=task,
            endpoint=f"/tasks/{task['id']}/step/execute",
            payload={"task_id": task["id"]},
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert authorization_calls == []
    assert calls == []


def test_recovery_mail_missing_worker_token_does_not_claim_mail_lease(
    app,
    monkeypatch,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import mail_task_service, recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(token=None)
            )
        ),
    )
    claims = []
    monkeypatch.setattr(
        mail_task_service,
        "get_mail_task_service",
        lambda: Record(
            claim_for_delegation=lambda **values: claims.append(values)
        ),
    )
    calls = []

    with app.app_context(), pytest.raises(
        WorkerForwardingError,
        match="assigned_worker_token_missing",
    ):
        forwarding.forward_task_request_if_remote(
            tid="mail-recovery-no-token",
            task={
                "id": "mail-recovery-no-token",
                "task_kind": "mail_operation",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": None,
                "derivation_reason": "goal_task_recovery",
            },
            endpoint="/tasks/mail-recovery-no-token/step/execute",
            payload={
                "task_id": "mail-recovery-no-token",
                "dispatch_lease_token": "recovery-mail-lease",
                "dispatch_lease_phase": "execute",
            },
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert claims == []
    assert calls == []


def test_generic_remote_forward_without_worker_token_fails_before_transport(
    app,
    monkeypatch,
):
    from agent.common.errors import WorkerForwardingError
    from agent.config import settings
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import recovery_dispatch_gate_service

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return False

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "agent_url", "http://hub:5000")
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        forwarding,
        "get_repository_registry",
        lambda: Record(
            agent_repo=Record(
                get_by_url=lambda _url: Record(token=None)
            )
        ),
    )
    calls = []

    with app.app_context(), pytest.raises(
        WorkerForwardingError,
        match="assigned_worker_token_missing",
    ):
        forwarding.forward_task_request_if_remote(
            tid="generic-no-worker-token",
            task={
                "id": "generic-no-worker-token",
                "task_kind": "analysis",
                "assigned_agent_url": "http://worker:5001",
                "assigned_agent_token": None,
            },
            endpoint="/tasks/generic-no-worker-token/step/execute",
            payload={"task_id": "generic-no-worker-token"},
            forwarder=lambda *args, **kwargs: calls.append((args, kwargs)),
            on_success=lambda *_args: None,
        )

    assert calls == []


def test_task_repository_rejects_stale_recovery_save_after_owner_terminal(
    monkeypatch,
):
    from agent.common.recovery_owner_terminal_write_boundary import (
        authorize_recovery_owner_terminal_write,
    )
    from agent.repositories.tasks import TaskRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    release = {
        "release_epoch": "sticky-epoch",
        "plan_id": "sticky-plan",
        "source_task_id": "source-z-sticky",
    }
    lease = {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "token_digest": "a" * 64,
        "phase": "execute",
        "state": "worker_admitted",
        "revision": 3,
        "worker_url": "http://worker:5000",
        "source_task_id": "source-z-sticky",
        "plan_id": "sticky-plan",
        "release_epoch": "sticky-epoch",
    }
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="source-z-sticky",
                goal_id="goal-sticky",
                status="completed",
                status_reason_details={
                    "model_recovery": {
                        "plan_id": "sticky-plan"
                    }
                },
            )
        )
        session.add(
            TaskDB(
                id="child-a-sticky",
                goal_id="goal-sticky",
                source_task_id="source-z-sticky",
                derivation_reason="goal_task_recovery",
                status="todo",
                status_reason_details={
                    "model_recovery_release": release,
                    "recovery_dispatch_lease": lease,
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repo = TaskRepository()
    stale = repo.get_by_id("child-a-sticky")

    stale.status = "pending_approval"
    stale.status_reason_code = "approval_request_pending"
    stale.status_reason_details = {}
    stale.updated_at = time.time() + 10
    rejected = repo.save(stale)
    assert rejected.status == "todo"
    assert rejected.status_reason_code is None
    assert rejected.status_reason_details[
        "model_recovery_release"
    ] == release
    assert rejected.status_reason_details[
        "recovery_dispatch_lease"
    ]["state"] == "worker_admitted"

    terminal = repo.get_by_id("child-a-sticky")
    terminal.status = "cancelled"
    terminal.status_reason_code = "recovery_parent_terminal"
    terminal_at = time.time() + 20
    owner_marker = {
        "schema": (
            "ananta.recovery_owner_terminal_invalidation.v1"
        ),
        "task_id": terminal.id,
        "goal_id": terminal.goal_id,
        "goal_status": "cancelled",
        "previous_status": "todo",
        "target_status": "cancelled",
        "reason_code": "goal_terminal:cancelled",
        "invalidated_at": terminal_at,
    }
    terminal.status_reason_details = {
        **dict(terminal.status_reason_details or {}),
        "recovery_owner_terminal_invalidation": owner_marker,
    }
    terminal.updated_at = terminal_at
    with authorize_recovery_owner_terminal_write(
        task_id=terminal.id,
        marker=owner_marker,
    ):
        repo.save(terminal)
    stale.status = "todo"
    stale.updated_at = time.time() + 30
    still_terminal = repo.save(stale)
    assert still_terminal.status == "cancelled"
    assert still_terminal.status_reason_code == (
        "recovery_parent_terminal"
    )


def test_task_repository_rejects_recovery_binding_mutation_without_corruption(
    monkeypatch,
):
    from agent.repositories.tasks import TaskRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            TaskDB(
                id="source-binding-original",
                goal_id="goal-binding-original",
                team_id="team-binding-original",
                status="blocked_by_dependency",
            )
        )
        session.add(
            TaskDB(
                id="child-binding-immutable",
                source_task_id="source-binding-original",
                goal_id="goal-binding-original",
                plan_id="plan-binding-original",
                team_id="team-binding-original",
                derivation_reason="goal_task_recovery",
                derivation_depth=1,
                status="todo",
                status_reason_details={
                    "model_recovery_release": {
                        "source_task_id": "source-binding-original",
                        "goal_id": "goal-binding-original",
                        "plan_id": "plan-binding-original",
                        "team_id": "team-binding-original",
                        "release_epoch": "binding-epoch",
                    }
                },
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    repo = TaskRepository()
    malicious = repo.get_by_id("child-binding-immutable")
    malicious.source_task_id = "source-binding-attacker"
    malicious.goal_id = "goal-binding-attacker"
    malicious.plan_id = "plan-binding-attacker"
    malicious.team_id = "team-binding-attacker"
    malicious.derivation_reason = "manual"
    malicious.derivation_depth = 99
    malicious.status = "completed"
    malicious.updated_at = time.time() + 10

    with pytest.raises(
        ValueError,
        match="recovery_task_binding_immutable",
    ):
        repo.save(malicious)

    persisted = repo.get_by_id("child-binding-immutable")
    assert persisted.source_task_id == "source-binding-original"
    assert persisted.goal_id == "goal-binding-original"
    assert persisted.plan_id == "plan-binding-original"
    assert persisted.team_id == "team-binding-original"
    assert persisted.derivation_reason == "goal_task_recovery"
    assert persisted.derivation_depth == 1
    assert persisted.status == "todo"
    assert persisted.status_reason_details[
        "model_recovery_release"
    ]["release_epoch"] == "binding-epoch"


def test_goal_repository_terminal_status_is_sticky_against_detached_save(
    monkeypatch,
):
    from agent.repositories.goals import GoalRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            GoalDB(
                id="sticky-goal",
                goal="Do not revive",
                status="in_progress",
                execution_preferences={"owner": "hub"},
            )
        )
        session.commit()
    monkeypatch.setattr(
        "agent.repositories.goals._engine",
        lambda: engine,
    )
    repo = GoalRepository()
    stale = repo.get_by_id("sticky-goal")
    terminal = repo.get_by_id("sticky-goal")
    terminal.status = "archived"
    terminal.execution_preferences = {
        "last_status_reason": "archived_by_admin"
    }
    repo.save(terminal)

    stale.status = "planned"
    stale.execution_preferences = {"stale": True}
    persisted = repo.save(stale)
    assert persisted.status == "archived"
    assert persisted.execution_preferences[
        "last_status_reason"
    ] == "archived_by_admin"


def test_task_queue_claim_honors_recovery_gate(
    monkeypatch,
):
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateDecision,
    )
    from agent.services.task_queue_service import TaskQueueService

    task = Record(id="claim-gated", status="todo")
    invalidated = []
    cas_calls = []

    class Gate:
        def evaluate_task(self, candidate):
            assert candidate.id == task.id
            return RecoveryDispatchGateDecision(
                False,
                "recovery_release_not_committed",
                source_task_id="source-claim",
                plan_id="plan-claim",
            )

        def invalidate_task(self, task_id, *, reason_code):
            invalidated.append((task_id, reason_code))
            return True

    monkeypatch.setattr(
        "agent.services.task_queue_service.get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )
    monkeypatch.setattr(
        "agent.services.task_queue_service.task_repo",
        Record(get_by_id=lambda task_id: task),
    )
    monkeypatch.setattr(
        "agent.services.task_queue_service.compare_and_set_local_task_status",
        lambda *args, **kwargs: cas_calls.append(
            (args, kwargs)
        )
        or True,
    )

    class LockPort:
        @contextlib.contextmanager
        def mutation_locks(self, task_ids):
            assert set(task_ids) == {task.id}
            yield True

    monkeypatch.setattr(
        "agent.services.task_mutation_lock_service.get_task_mutation_lock_port",
        lambda: LockPort(),
    )

    claimed = TaskQueueService().claim_task(
        task_id=task.id,
        agent_url="http://worker",
        lease_until=time.time() + 30,
    )

    assert claimed is False
    assert invalidated == []
    assert cas_calls == []


def test_task_mutation_advisory_lock_is_reentrant_per_thread():
    from agent.services.task_mutation_lock_service import (
        TaskMutationLockPort,
    )

    class Connection:
        def __init__(self):
            self.statements = []
            self.closed = False

        def execute(self, statement, _parameters):
            self.statements.append(str(statement))
            return Record()

        def close(self):
            self.closed = True

    connection = Connection()
    engine = Record(
        dialect=Record(name="postgresql"),
        connect_calls=0,
    )

    def connect():
        engine.connect_calls += 1
        return connection

    engine.connect = connect
    port = TaskMutationLockPort(
        engine_provider=lambda: engine,
    )

    with port.mutation_lock("source-lock") as outer:
        assert outer is True
        with port.mutation_lock("source-lock") as nested:
            assert nested is True

    assert engine.connect_calls == 1
    assert len(connection.statements) == 2
    assert "pg_advisory_lock" in connection.statements[0]
    assert "pg_advisory_unlock" in connection.statements[1]
    assert connection.closed is True


def test_plan_lock_does_not_mask_body_exception(monkeypatch):
    from agent.services.planning_service import PlanningService

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr("agent.database.engine", engine)

    with pytest.raises(ValueError, match="body-failure"):
        with PlanningService._distributed_materialization_lock(
            "plan-body-error"
        ) as acquired:
            assert acquired is True
            raise ValueError("body-failure")
    recovery = TaskRecoveryPlanningService(
        role_provider=lambda: "hub",
    )
    with pytest.raises(ValueError, match="recovery-body-failure"):
        with recovery._distributed_advisory_lock(
            namespace="test-recovery-lock",
            key="source-body-error",
        ) as acquired:
            assert acquired is True
            raise ValueError("recovery-body-failure")


def test_terminal_source_cas_cancels_children_and_finalizes_goal(
    monkeypatch,
):
    from agent.services.task_runtime_service import (
        compare_and_set_local_task_status,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            GoalDB(
                id="goal-terminal-cascade",
                goal="Finish a recovery chain",
                status="in_progress",
            )
        )
        session.add(
            TaskDB(
                id="source-terminal-cascade",
                goal_id="goal-terminal-cascade",
                status="in_progress",
            )
        )
        for index in (1, 2):
            session.add(
                TaskDB(
                    id=f"child-terminal-cascade-{index}",
                    goal_id="goal-terminal-cascade",
                    source_task_id="source-terminal-cascade",
                    derivation_reason="goal_task_recovery",
                    status="paused",
                )
            )
        session.commit()

    class Audit:
        def emit_workflow_transition(self, **_values):
            return None

        def emit_write_operation(self, **_values):
            return None

    monkeypatch.setattr(
        "agent.services.task_runtime_service._task_engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.repositories.goals._engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        "agent.services.execution_audit_service.get_execution_audit_service",
        lambda: Audit(),
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )

    assert compare_and_set_local_task_status(
        "source-terminal-cascade",
        "completed",
        expected_statuses={"in_progress"},
        event_type="source_completed",
        force=True,
    )

    with Session(engine) as session:
        assert session.get(
            TaskDB,
            "child-terminal-cascade-1",
        ).status == "cancelled"
        assert session.get(
            TaskDB,
            "child-terminal-cascade-2",
        ).status == "cancelled"
        assert session.get(
            GoalDB,
            "goal-terminal-cascade",
        ).status == "completed"


def test_stale_digest_refresh_retries_source_cas_without_duplicate(
    monkeypatch,
):
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        planning,
        _updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    repos.plan_node_repo.rows["node-1"].title = (
        "Operator edited before refresh CAS"
    )
    stale = approvals.created[0]
    stale.status = "granted"
    original_mark = service._mark_source_waiting_for_approval
    fail_once = {"active": True}

    def fail_refresh_source_once(**values):
        if (
            fail_once["active"]
            and values["approval_request_id"] == "approval-2"
        ):
            fail_once["active"] = False
            return False
        return original_mark(**values)

    monkeypatch.setattr(
        service,
        "_mark_source_waiting_for_approval",
        fail_refresh_source_once,
    )
    first = service.handle_approval_decision(stale)

    assert first["reason_code"] == (
        "recovery_source_transition_conflict"
    )
    assert len(approvals.created) == 2
    assert stale.status == "granted"
    plan = repos.plan_repo.get_by_id(proposal["plan_id"])
    assert plan.rationale["approval_request_id"] == "approval-2"
    assert plan.rationale["approval_refresh"]["state"] == (
        "approval_bound"
    )
    assert planning.calls == []

    second = service.handle_approval_decision(stale)

    assert second["reason_code"] == (
        "recovery_plan_digest_refreshed"
    )
    assert len(approvals.created) == 2
    assert stale.status == "consumed"
    assert task.status_reason_details["model_recovery"][
        "approval_request_id"
    ] == "approval-2"
    assert plan.rationale["approval_refresh"]["state"] == (
        "completed"
    )


def test_stale_digest_refresh_recovers_after_consume_crash(
    monkeypatch,
):
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        _planning,
        _updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    repos.plan_node_repo.rows["node-1"].description = (
        "Operator edited before consume crash."
    )
    stale = approvals.created[0]
    stale.status = "granted"
    original_consume = approvals.consume_request
    fail_once = {"active": True}

    def crash_before_stale_consume(request_id):
        if fail_once["active"] and request_id == stale.id:
            fail_once["active"] = False
            raise RuntimeError("simulated_consume_crash")
        return original_consume(request_id)

    monkeypatch.setattr(
        approvals,
        "consume_request",
        crash_before_stale_consume,
    )
    first = service.handle_approval_decision(stale)

    assert first["reason_code"] == "approval_consume_failed"
    assert len(approvals.created) == 2
    assert stale.status == "granted"
    assert task.status_reason_details["model_recovery"][
        "approval_request_id"
    ] == "approval-2"
    plan = repos.plan_repo.get_by_id(proposal["plan_id"])
    assert plan.rationale["approval_refresh"]["state"] == (
        "source_bound"
    )

    second = service.handle_approval_decision(stale)

    assert second["reason_code"] == (
        "recovery_plan_digest_refreshed"
    )
    assert len(approvals.created) == 2
    assert stale.status == "consumed"
    assert plan.rationale["approval_refresh"]["state"] == (
        "completed"
    )


def test_partial_materialization_transient_error_is_retryable(
    monkeypatch,
):
    (
        service,
        plan,
        nodes,
        staged,
        task_repo,
        lifecycle_calls,
    ) = _partial_materialization_fixture(monkeypatch)
    approved_digest = plan.rationale["plan_digest"]
    fail_once = {"active": True}

    class FlakyLifecycle:
        def materialize_from_plan_node(self, **values):
            lifecycle_calls.append(values["task_id"])
            if fail_once["active"]:
                fail_once["active"] = False
                raise RuntimeError("transient_create_failure")
            entry = next(
                item
                for item in staged
                if item["task_id"] == values["task_id"]
            )
            task_repo.save(task_repo.task_from_entry(entry))

    monkeypatch.setattr(
        "agent.services.planning_service.get_task_lifecycle_service",
        lambda: FlakyLifecycle(),
    )
    planner = Record(_stats={"tasks_created": 0})

    first = service.materialize_existing_plan(
        planner=planner,
        plan_id=plan.id,
        approval_request_id="approval-partial",
        team_id="team-partial",
        source_task_id="source-partial",
        expected_plan_digest=approved_digest,
        initial_task_status="paused",
    )

    assert first["status"] == "failed"
    assert first["reason_code"] == "materialization_failed"
    assert first["retryable"] is True
    assert plan.status == "pending_approval"
    assert plan.rationale[
        "materialization_survivor_task_ids"
    ] == [staged[0]["task_id"]]
    assert nodes[0].materialized_task_id == staged[0]["task_id"]
    assert nodes[1].materialized_task_id is None

    second = service.materialize_existing_plan(
        planner=planner,
        plan_id=plan.id,
        approval_request_id="approval-partial",
        team_id="team-partial",
        source_task_id="source-partial",
        expected_plan_digest=approved_digest,
        initial_task_status="paused",
    )

    assert second["status"] == "materialized"
    assert second["created_task_ids"] == [
        entry["task_id"] for entry in staged
    ]
    assert lifecycle_calls == [
        staged[1]["task_id"],
        staged[1]["task_id"],
    ]


def test_team_change_invalidates_exact_recovery_grant():
    (
        service,
        task,
        repos,
        approvals,
        _planner,
        planning,
        _updates,
        failures,
    ) = _fixture()
    proposal = service.propose_after_model_exhaustion(
        task=task,
        strategy_failures=failures,
    )
    approval = approvals.created[0]
    approval.status = "granted"
    goal = repos.goal_repo.get_by_id("goal-1")
    goal.team_id = "team-2"

    result = service.handle_approval_decision(approval)

    assert result["status"] == "stopped"
    assert result["reason_code"] == (
        "recovery_team_binding_changed"
    )
    assert result["approval_status"] == "consumed"
    assert approval.status == "consumed"
    assert planning.calls == []
    assert repos.plan_repo.get_by_id(
        proposal["plan_id"]
    ).status == "rejected"


@pytest.mark.parametrize(
    "terminal_goal_status",
    ["cancelled", "failed", "aborted", "timeout"],
)
def test_terminal_goal_transition_fences_and_cancels_recovery_children(
    monkeypatch,
    terminal_goal_status,
):
    from agent.common.recovery_dispatch_invalidation_write_boundary import (
        recovery_dispatch_invalidation_write_authorized,
    )
    from agent.common.recovery_owner_terminal_write_boundary import (
        recovery_owner_terminal_write_authorized,
    )
    from agent.services.lifecycle_service import GoalLifecycleService

    events = []
    lock_state = {"held": False}
    goal = Record(id="goal-fenced", status="in_progress")
    source = Record(
        id="source-fenced",
        goal_id=goal.id,
        status="waiting_for_review",
        status_reason_details={
            "model_recovery": {"plan_id": "plan-fenced"}
        },
        derivation_reason=None,
        source_task_id=None,
    )
    child_lease = {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "task_id": "child-fenced",
        "source_task_id": source.id,
        "goal_id": goal.id,
        "plan_id": "plan-fenced",
        "team_id": "team-fenced",
        "release_epoch": "release-fenced",
        "state": "worker_admitted",
        "phase": "execute",
        "revision": 1,
        "token_digest": "a" * 64,
        "request_fingerprint": "b" * 64,
        "worker_url": "http://worker:5000",
        "issued_at": 1.0,
        "expires_at": time.time() + 300,
        "admitted_at": 2.0,
        "admitted_worker_url": "http://worker:5000",
    }
    child = Record(
        id="child-fenced",
        goal_id=goal.id,
        status="todo",
        status_reason_details={
            "recovery_dispatch_lease": child_lease,
        },
        derivation_reason="goal_task_recovery",
        source_task_id=source.id,
    )

    class LockPort:
        @contextlib.contextmanager
        def mutation_locks(self, task_ids):
            assert set(task_ids) == {
                source.id,
                child.id,
                "goal-task-materialization:goal-fenced",
            }
            assert lock_state["held"] is False
            lock_state["held"] = True
            events.append("lock_enter")
            try:
                yield True
            finally:
                events.append("lock_exit")
                lock_state["held"] = False

    def save_goal(record):
        assert lock_state["held"] is True
        events.append(f"goal_saved:{record.status}")
        return record

    task_updates = []

    def update_task(task_id, status, **values):
        assert lock_state["held"] is True
        assert goal.status == terminal_goal_status
        marker = values["status_reason_details"][
            "recovery_owner_terminal_invalidation"
        ]
        assert recovery_owner_terminal_write_authorized(
            task_id=task_id,
            marker=marker,
        )
        assert marker["goal_status"] == terminal_goal_status
        assert marker["target_status"] == status
        if task_id == child.id:
            proposed_lease = values[
                "status_reason_details"
            ]["recovery_dispatch_lease"]
            assert (
                recovery_dispatch_invalidation_write_authorized(
                    task_id=task_id,
                    current_lease=child_lease,
                    proposed_lease=proposed_lease,
                )
            )
            assert proposed_lease["state"] == "revoked"
            assert proposed_lease["revision"] == 2
        events.append(f"task_updated:{task_id}:{status}")
        task_updates.append((task_id, status, values))

    def cancel_goal_requests(**_values):
        assert lock_state["held"] is False
        events.append("cancel_goal")

    monkeypatch.setattr(
        "agent.services.lifecycle_service.task_repo",
        Record(get_all=lambda: [source, child]),
    )
    monkeypatch.setattr(
        "agent.services.lifecycle_service.goal_repo",
        Record(save=save_goal),
    )
    monkeypatch.setattr(
        "agent.services.lifecycle_service.update_local_task_status",
        update_task,
    )
    monkeypatch.setattr(
        "agent.services.task_mutation_lock_service.get_task_mutation_lock_port",
        lambda: LockPort(),
    )
    monkeypatch.setattr(
        "agent.services.lifecycle_service.get_request_cancellation_service",
        lambda: Record(cancel_goal_requests=cancel_goal_requests),
    )

    result = GoalLifecycleService().transition_goal(
        goal,
        target_status=terminal_goal_status,
    )

    assert result.status == terminal_goal_status
    assert events[0] == "lock_enter"
    assert events[1] == f"goal_saved:{terminal_goal_status}"
    assert events[-2:] == ["lock_exit", "cancel_goal"]
    expected_task_status = (
        "failed"
        if terminal_goal_status == "failed"
        else "cancelled"
    )
    assert [entry[:2] for entry in task_updates] == [
        (source.id, expected_task_status),
        (child.id, expected_task_status),
    ]
    assert all(entry[2]["force"] is True for entry in task_updates)
