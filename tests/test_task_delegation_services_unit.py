from types import SimpleNamespace

from agent.services.task_delegation_services import (
    DelegationRequest,
    RoutingDecision,
    TaskDelegationPlan,
    TaskDelegationPlanner,
    TaskDelegationResultWriter,
    WorkerExecutionContextFactory,
    WorkerExecutionBundle,
)
from agent.services.task_orchestration_service import CompletionOutcome, TaskOrchestrationService


class _Dependencies:
    def __init__(self, *, workers=None, routing_hint=None, forward_result=None, forward_error=None):
        self.workers = workers or []
        self.routing_hint = routing_hint
        self.forward_result = forward_result or {"status": "success", "data": {"accepted": True}}
        self.forward_error = forward_error
        self.forward_calls = []
        self.update_calls = []

    def repository_registry(self):
        return SimpleNamespace(agent_repo=SimpleNamespace(get_all=lambda: list(self.workers)))

    def routing_advisor(self):
        return SimpleNamespace(
            resolve_routing_hint=lambda **_kwargs: self.routing_hint,
        )

    def context_policy_service(self):
        return SimpleNamespace(
            build_context_policy=lambda **_kwargs: (
                {
                    "mode": "standard",
                    "retrieval_intent": "execution_focused_context",
                    "required_context_scope": "task_and_direct_neighbors",
                    "preferred_bundle_mode": "standard",
                    "neighbor_task_ids": [],
                },
                {
                    "retrieval_intent": "execution_focused_context",
                    "required_context_scope": "task_and_direct_neighbors",
                    "preferred_bundle_mode": "standard",
                },
                {"neighbor_task_ids": []},
            )
        )

    def forward_task_to_worker(self, agent_url, endpoint, data, token=None):
        self.forward_calls.append({"agent_url": agent_url, "endpoint": endpoint, "data": data, "token": token})
        if self.forward_error:
            raise self.forward_error
        return self.forward_result

    def update_task_status(self, *args, **kwargs):
        self.update_calls.append({"args": args, "kwargs": kwargs})


class _AgentRegistry:
    def build_directory_entry(self, *, agent, timeout, now=None):
        payload = dict(agent)
        payload.setdefault("available_for_routing", True)
        payload.setdefault("liveness", {"status": payload.get("status", "online"), "available_for_routing": True})
        return payload


class _WorkerJobService:
    def __init__(self):
        self.context_calls = []
        self.job_calls = []

    def create_context_bundle(self, **kwargs):
        self.context_calls.append(kwargs)
        return SimpleNamespace(
            id="ctx-1",
            context_text="context",
            chunks=[],
            token_estimate=42,
            bundle_metadata={"query": kwargs["query"]},
        )

    def create_worker_job(self, **kwargs):
        self.job_calls.append(kwargs)
        return SimpleNamespace(id="job-1")


class _WorkerContractService:
    def build_routing_decision(self, **kwargs):
        return {
            "worker_url": kwargs["agent_url"],
            "selected_by_policy": kwargs["selected_by_policy"],
            "task_kind": kwargs["task_kind"],
            "required_capabilities": list(kwargs["required_capabilities"] or []),
            "matched_capabilities": list(getattr(kwargs.get("selection"), "matched_capabilities", []) or []),
            "matched_roles": list(getattr(kwargs.get("selection"), "matched_roles", []) or []),
            "preferred_backend": kwargs.get("preferred_backend"),
        }

    def build_job_metadata(self, **kwargs):
        return {
            "routing_decision": dict(kwargs["routing_decision"]),
            "task_kind": kwargs["task_kind"],
            "required_capabilities": list(kwargs["required_capabilities"] or []),
            "context_policy": dict(kwargs.get("context_policy") or {}),
            **dict(kwargs.get("extra_metadata") or {}),
        }

    def build_execution_context(self, **kwargs):
        return {
            "kind": "worker_execution_context",
            "instructions": kwargs["instructions"],
            "context_bundle_id": kwargs["context_bundle"].id,
            "context_policy": dict(kwargs["context_policy"]),
            "workspace": dict(kwargs["workspace"]),
            "artifact_sync": dict(kwargs["artifact_sync"]),
            "allowed_tools": list(kwargs["allowed_tools"]),
            "expected_output_schema": dict(kwargs["expected_output_schema"]),
            "routing": dict(kwargs["routing_decision"]),
        }

    def build_worker_todo_contract(self, **kwargs):
        return {
            "schema": "worker_todo_contract.v1",
            "task_id": kwargs["task_id"],
            "goal_id": kwargs["goal_id"],
            "trace_id": kwargs["trace_id"],
            "worker": {
                "executor_kind": kwargs["executor_kind"],
                "worker_profile": kwargs["worker_profile"],
                "profile_source": kwargs["profile_source"],
            },
            "todo": {
                "version": kwargs.get("todo_version", "1.0"),
                "track": kwargs.get("track", "worker_subplan"),
                "tasks": list(kwargs.get("tasks") or []),
            },
            "execution": {
                "mode": kwargs.get("mode", "assistant_execute"),
                "allowed_tools": list(kwargs.get("allowed_tools") or []),
                "enforce_artifacts": bool(kwargs.get("enforce_artifacts", True)),
                "max_steps": int(kwargs.get("max_steps") or 20),
            },
            "control_manifest": {
                "trace_id": kwargs["trace_id"],
                "capability_id": kwargs["capability_id"],
                "context_hash": kwargs["context_hash"],
            },
            "expected_result_schema": "worker_todo_result.v1",
        }


def _request(**overrides):
    data = SimpleNamespace(
        agent_url="",
        agent_token="worker-token",
        task_kind="planning",
        required_capabilities=["planning"],
        context_query="",
        subtask_description="Create a plan",
        priority="high",
        expected_output_schema={"type": "object"},
        allowed_tools=["sgpt", "codex"],
    )
    for key, value in overrides.pop("data_overrides", {}).items():
        setattr(data, key, value)
    parent_task = {
        "id": "parent-1",
        "title": "Parent",
        "description": "Parent description",
        "status": "todo",
        "goal_id": "goal-1",
        "goal_trace_id": "trace-1",
        "team_id": "team-1",
    }
    parent_task.update(overrides.pop("parent_overrides", {}))
    return DelegationRequest(task_id="parent-1", parent_task=parent_task, data=data)


def test_task_delegation_planner_selects_capable_worker_with_routing_hint():
    deps = _Dependencies(
        workers=[
            {"url": "http://planner:5000", "status": "online", "capabilities": ["planning"], "worker_roles": ["planner"]},
            {"url": "http://coder:5000", "status": "online", "capabilities": ["coding"], "worker_roles": ["coder"]},
        ],
        routing_hint={"preferred_worker_url": "http://planner:5000", "reason": "planner role"},
    )
    plan = TaskDelegationPlanner(deps).plan(request=_request(), agent_registry_service=_AgentRegistry())

    assert isinstance(plan, TaskDelegationPlan)
    assert plan.agent_url == "http://planner:5000"
    assert plan.selected_by_policy is True
    assert plan.effective_task_kind == "planning"
    assert plan.effective_required_capabilities == ["planning"]
    assert plan.routing_hint == {"preferred_worker_url": "http://planner:5000", "reason": "planner role"}


def test_task_delegation_planner_keeps_manual_override_without_policy_selection():
    request = _request(data_overrides={"agent_url": "http://manual:5000", "required_capabilities": []})
    deps = _Dependencies(workers=[])
    plan = TaskDelegationPlanner(deps).plan(request=request, agent_registry_service=_AgentRegistry())

    assert isinstance(plan, TaskDelegationPlan)
    assert plan.agent_url == "http://manual:5000"
    assert plan.selected_by_policy is False
    assert plan.selection is None
    assert plan.effective_required_capabilities == []


def test_task_delegation_planner_returns_no_worker_available_for_empty_directory():
    request = _request(data_overrides={"agent_url": "", "required_capabilities": ["planning"]})
    result = TaskDelegationPlanner(_Dependencies(workers=[])).plan(request=request, agent_registry_service=_AgentRegistry())

    assert result["error"] == "no_worker_available"
    assert result["code"] == 409
    assert "reasons" in result["data"]


def test_worker_execution_context_factory_builds_context_job_workspace_and_payload():
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=SimpleNamespace(reasons=["capability_match"], matched_capabilities=["planning"], matched_roles=["planner"]),
        policy_decision=SimpleNamespace(id="policy-1"),
        routing_hint={"preferred_worker_url": "http://planner:5000"},
        effective_task_kind="planning",
        effective_required_capabilities=["planning"],
        preferred_backend=None,
    )
    worker_jobs = _WorkerJobService()

    bundle = WorkerExecutionContextFactory(_Dependencies()).build(
        request=request,
        plan=plan,
        worker_job_service=worker_jobs,
        worker_contract_service=_WorkerContractService(),
    )

    assert isinstance(bundle, WorkerExecutionBundle)
    assert bundle.context_bundle.id == "ctx-1"
    assert bundle.worker_job.id == "job-1"
    assert bundle.allowed_tools == ["sgpt", "codex"]
    assert bundle.expected_output_schema == {"type": "object"}
    assert bundle.routing_decision.as_dict()["copilot_hint"]["preferred_worker_url"] == "http://planner:5000"
    assert bundle.worker_execution_context["workspace"]["scope_mode"] == "goal_worker"
    assert bundle.worker_execution_context["todo_contract"]["schema"] == "worker_todo_contract.v1"
    assert bundle.worker_execution_context["todo_contract_generation"]["enabled"] is True
    assert bundle.delegation_payload["parent_task_id"] == "parent-1"
    assert bundle.delegation_payload["context_bundle_policy"]["mode"] == "standard"
    assert worker_jobs.context_calls[0]["query"] == "Parent Parent description Create a plan"
    assert worker_jobs.job_calls[0]["metadata"]["selected_by_policy"] is True


def test_worker_execution_context_factory_uses_explicit_context_query_and_empty_optional_schema():
    request = _request(
        data_overrides={
            "context_query": "explicit context query",
            "allowed_tools": None,
            "expected_output_schema": None,
        }
    )
    plan = TaskDelegationPlan(
        agent_url="http://manual:5000",
        selected_by_policy=False,
        selection=None,
        policy_decision=None,
        routing_hint=None,
        effective_task_kind=None,
        effective_required_capabilities=[],
        preferred_backend=None,
    )
    worker_jobs = _WorkerJobService()

    bundle = WorkerExecutionContextFactory(_Dependencies()).build(
        request=request,
        plan=plan,
        worker_job_service=worker_jobs,
        worker_contract_service=_WorkerContractService(),
    )

    assert worker_jobs.context_calls[0]["query"] == "explicit context query"
    assert bundle.allowed_tools == []
    assert bundle.expected_output_schema == {}
    assert bundle.routing_decision.as_dict()["selected_by_policy"] is False


def test_task_delegation_result_writer_forwards_then_updates_parent_and_returns_stable_model():
    deps = _Dependencies(forward_result={"data": {"accepted": True, "task_id": "sub-1"}})
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=SimpleNamespace(reasons=["capability_match"]),
        policy_decision=SimpleNamespace(id="policy-1"),
        routing_hint=None,
        effective_task_kind="planning",
        effective_required_capabilities=["planning"],
        preferred_backend=None,
    )
    bundle = WorkerExecutionBundle(
        subtask_id="sub-1",
        context_bundle=SimpleNamespace(id="ctx-1"),
        context_policy={"mode": "standard"},
        retrieval_hints={"retrieval_intent": "execution_focused_context"},
        task_neighborhood={"neighbor_task_ids": []},
        expected_output_schema={},
        allowed_tools=[],
        routing_decision=RoutingDecision({"worker_url": "http://planner:5000"}),
        worker_job=SimpleNamespace(id="job-1"),
        workspace_scope={"mode": "goal_worker"},
        worker_execution_context={"kind": "worker_execution_context"},
        delegation_payload={"id": "sub-1"},
    )

    response = TaskDelegationResultWriter(deps).forward_and_write(request=request, plan=plan, bundle=bundle)

    assert deps.forward_calls[0]["endpoint"] == "/tasks"
    assert deps.forward_calls[0]["token"] == "worker-token"
    assert deps.update_calls[0]["args"] == ("parent-1", "todo")
    assert deps.update_calls[0]["kwargs"]["event_type"] == "task_delegated"
    assert deps.update_calls[0]["kwargs"]["event_details"]["policy_decision_id"] == "policy-1"
    assert response["data"]["status"] == "delegated"
    assert response["data"]["worker_selection"] == {"worker_url": "http://planner:5000"}


def test_task_delegation_result_writer_reports_forwarding_failure_without_parent_update():
    deps = _Dependencies(forward_error=RuntimeError("worker unavailable"))
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=None,
        routing_hint=None,
        effective_task_kind="planning",
        effective_required_capabilities=["planning"],
        preferred_backend=None,
    )
    bundle = WorkerExecutionBundle(
        subtask_id="sub-1",
        context_bundle=SimpleNamespace(id="ctx-1"),
        context_policy={},
        retrieval_hints={},
        task_neighborhood={},
        expected_output_schema={},
        allowed_tools=[],
        routing_decision=RoutingDecision({}),
        worker_job=SimpleNamespace(id="job-1"),
        workspace_scope={},
        worker_execution_context={},
        delegation_payload={},
    )

    response = TaskDelegationResultWriter(deps).forward_and_write(request=request, plan=plan, bundle=bundle)

    assert response["error"] == "delegation_failed"
    assert response["code"] == 502
    assert deps.update_calls == []


def test_routing_decision_returns_copy_not_mutable_internal_payload():
    decision = RoutingDecision({"worker_url": "http://worker:5000", "selected_by_policy": True})
    payload = decision.as_dict()
    payload["worker_url"] = "mutated"

    assert decision.as_dict()["worker_url"] == "http://worker:5000"


def test_completion_outcome_derivation_is_explicit_for_passed_and_failed_paths():
    passed = TaskOrchestrationService._derive_completion_outcome({"gate_results": {"passed": True}})
    failed = TaskOrchestrationService._derive_completion_outcome({"gate_results": {"passed": False, "reason": "lint"}})

    assert isinstance(passed, CompletionOutcome)
    assert passed.gates_passed is True
    assert passed.final_status == "completed"
    assert passed.exit_code == 0
    assert failed.gates_passed is False
    assert failed.final_status == "verification_failed"
    assert failed.exit_code == 1
    assert failed.gate_results["reason"] == "lint"


def test_completion_outcome_defaults_missing_gate_results_to_failed():
    outcome = TaskOrchestrationService._derive_completion_outcome({})

    assert outcome.gate_results == {}
    assert outcome.gates_passed is False
    assert outcome.final_status == "verification_failed"
    assert outcome.exit_code == 1
