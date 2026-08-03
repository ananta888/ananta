import base64
import contextlib
from types import SimpleNamespace

from agent.common.gateways.worker_gateway import HttpWorkerGateway
from agent.services.organization_research_delegation_policy_service import (
    AuthoritativeResearchContext,
)
from agent.services.organization_research_dispatch_capability_service import (
    OrganizationResearchDispatchCapabilityIssuer,
)
from agent.services.task_delegation_services import (
    DelegationRequest,
    RoutingDecision,
    TaskDelegationPlan,
    TaskDelegationPlanner,
    TaskDelegationResultWriter,
    WorkerExecutionBundle,
    WorkerExecutionContextFactory,
)
from agent.services.task_orchestration_service import CompletionOutcome, TaskOrchestrationService
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
)

_RESEARCH_DISPATCH_ISSUER = (
    OrganizationResearchDispatchCapabilityIssuer(
        Ed25519SigningKeyRing(
            {
                "hub-research-test": base64.b64encode(b"h" * 32)
            },
            active_key_id="hub-research-test",
        )
    )
)


class _Dependencies:
    def __init__(self, *, workers=None, routing_hint=None, forward_result=None, forward_error=None):
        self.workers = workers or []
        self.routing_hint = routing_hint
        self.forward_result = forward_result or {
            "status": "success",
            "data": {"accepted": True, "task_id": "sub-1"},
        }
        self.forward_error = forward_error
        self.forward_calls = []
        self.update_calls = []

    def repository_registry(self):
        return SimpleNamespace(
            agent_repo=SimpleNamespace(
                get_all=lambda: list(self.workers),
                get_by_url=lambda worker_url: SimpleNamespace(
                    url=worker_url,
                    token="worker-specific-service-token-1234567890",
                ),
            )
        )

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
        self.failed_dispatches = []

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

    def fail_dispatch(self, **kwargs):
        self.failed_dispatches.append(kwargs)


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


def _isolate_result_writer_repositories(monkeypatch):
    from agent.services import repository_registry

    parent = SimpleNamespace(
        id="parent-1",
        status="todo",
        derivation_reason=None,
        status_reason_details={},
    )
    worker = SimpleNamespace(
        url="http://planner:5000",
        token="worker-token",
    )
    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda task_id: (
                    parent if task_id == parent.id else None
                )
            ),
            agent_repo=SimpleNamespace(
                get_by_url=lambda worker_url: (
                    worker if worker_url == worker.url else None
                )
            ),
        ),
    )


def test_task_delegation_planner_selects_capable_worker_with_routing_hint():
    deps = _Dependencies(
        workers=[
            {
                "url": "http://planner:5000",
                "status": "online",
                "capabilities": ["planning"],
                "worker_roles": ["planner"],
            },
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
    result = TaskDelegationPlanner(_Dependencies(workers=[])).plan(
        request=request,
        agent_registry_service=_AgentRegistry(),
    )

    assert result["error"] == "no_worker_available"
    assert result["code"] == 409
    assert "reasons" in result["data"]


def test_organization_track_delegation_uses_only_persisted_dispatch_binding():
    request = _request(
        data_overrides={"agent_url": "http://caller-hint:5000"},
        parent_overrides={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "unit_id": "unit-1",
            "role_slot_id": "slot-1",
            "worker_execution_context": {
                "planning_lineage": {
                    "schema": "organization_planning_lineage.v1"
                }
            },
        },
    )
    resolver = SimpleNamespace(resolve=lambda _task: "http://hub-routed:5000")

    plan = TaskDelegationPlanner(
        _Dependencies(workers=[]),
        organization_binding_resolver=resolver,
    ).plan(request=request, agent_registry_service=_AgentRegistry())

    assert isinstance(plan, TaskDelegationPlan)
    assert plan.agent_url == "http://hub-routed:5000"
    assert plan.selected_by_policy is True


def test_organization_track_delegation_fails_closed_without_outbox_binding():
    request = _request(
        parent_overrides={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "unit_id": "unit-1",
            "role_slot_id": "slot-1",
            "worker_execution_context": {
                "planning_lineage": {
                    "schema": "organization_planning_lineage.v1"
                }
            },
        },
    )
    resolver = SimpleNamespace(resolve=lambda _task: None)

    result = TaskDelegationPlanner(
        _Dependencies(workers=[]),
        organization_binding_resolver=resolver,
    ).plan(request=request, agent_registry_service=_AgentRegistry())

    assert result == {
        "error": "organization_planning_dispatch_binding_required",
        "code": 409,
        "data": {},
    }


def test_every_organization_task_requires_authoritative_dispatch_binding():
    request = _request(
        parent_overrides={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "unit_id": "unit-1",
            "role_slot_id": "slot-1",
            "worker_execution_context": {},
        },
    )

    result = TaskDelegationPlanner(
        _Dependencies(workers=[]),
        organization_binding_resolver=SimpleNamespace(
            resolve=lambda _task: None
        ),
    ).plan(request=request, agent_registry_service=_AgentRegistry())

    assert result == {
        "error": "organization_planning_dispatch_binding_required",
        "code": 409,
        "data": {},
    }


def test_organization_category_research_routing_keeps_hub_required_capabilities():
    request = _request(
        data_overrides={
            "agent_url": "http://caller-hint:5000",
            "task_kind": "planning",
            "required_capabilities": [],
        },
        parent_overrides={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "task_kind": "planning_research",
            "required_capabilities": [
                "planning",
                "research",
                "source_analysis",
            ],
        },
    )
    resolver = SimpleNamespace(
        resolve=lambda _task: "http://hub-routed:5000"
    )

    plan = TaskDelegationPlanner(
        _Dependencies(workers=[]),
        organization_binding_resolver=resolver,
    ).plan(request=request, agent_registry_service=_AgentRegistry())

    assert isinstance(plan, TaskDelegationPlan)
    assert plan.effective_task_kind == "planning_research"
    assert plan.effective_required_capabilities == [
        "planning",
        "research",
        "source_analysis",
    ]


def test_worker_execution_context_factory_builds_context_job_workspace_and_payload():
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=SimpleNamespace(
            reasons=["capability_match"],
            matched_capabilities=["planning"],
            matched_roles=["planner"],
        ),
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


def test_category_research_factory_reuses_authoritative_bundle_without_generic_rag():
    authoritative_bundle = SimpleNamespace(
        id="ctx-authoritative",
        retrieval_run_id="retrieval-authoritative",
        task_id="parent-1",
        bundle_type="worker_execution_context",
        context_text="authoritative source text",
        chunks=[],
        token_estimate=12,
        bundle_metadata={"schema": "organization_source_catalog_context.v1"},
    )

    class ResearchPolicy:
        def resolve_context(self, task):
            assert task["task_kind"] == "planning_research"
            return AuthoritativeResearchContext(
                bundle=authoritative_bundle,
                context_policy={
                    "schema": "organization_research_source_context_policy.v1",
                    "context_bundle_id": authoritative_bundle.id,
                    "llm_scope": "local_only",
                },
            )

        def resolve_destination_binding(self, **kwargs):
            assert kwargs["worker_url"] == "http://planner:5000"
            return {
                "schema": "organization_research_destination_binding.v1",
                "destination_id": "dst-current",
                "binding_digest": "d" * 64,
                "worker_kind": "worker",
                "runtime_target_id": "runtime-target-alpha",
                "runtime_kind": "docker_container",
                "provider_id": "codex",
                "llm_scope": "local_only",
            }

    request = _request(
        parent_overrides={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "organization_id": "org-1",
            "task_kind": "planning_research",
            "required_capabilities": [
                "planning",
                "research",
                "source_analysis",
            ],
            "context_bundle_id": authoritative_bundle.id,
                "worker_execution_context": {
                    "context_bundle_id": authoritative_bundle.id,
                    "llm_scope": "local_only",
                    "source_context_policy": {
                        "schema": "organization_research_source_context_policy.v1",
                        "authority": "hub",
                        "mode": "authoritative_source_catalog_bundle",
                        "context_bundle_id": authoritative_bundle.id,
                        "context_bundle_digest": "c" * 64,
                        "llm_scope": "local_only",
                    },
                    "planning_research_assignment": {
                        "schema": "organization_category_research_assignment_binding.v1",
                        "assignment_id": "assignment-1",
                        "agent_url": "http://planner:5000",
                    },
                "allowed_tools": [],
                "expected_output_schema": {
                    "schema_ref": "todos/todo.schema.json"
                },
            },
        }
    )
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=SimpleNamespace(id="policy-1"),
        routing_hint=None,
        effective_task_kind="planning_research",
        effective_required_capabilities=["planning", "research"],
        preferred_backend="codex",
    )
    worker_jobs = _WorkerJobService()

    bundle = WorkerExecutionContextFactory(
        _Dependencies(),
        research_delegation_policy=ResearchPolicy(),
        research_dispatch_issuer=_RESEARCH_DISPATCH_ISSUER,
    ).build(
        request=request,
        plan=plan,
        worker_job_service=worker_jobs,
        worker_contract_service=_WorkerContractService(),
    )

    assert bundle.context_bundle is authoritative_bundle
    assert worker_jobs.context_calls == []
    assert worker_jobs.job_calls[0]["context_bundle_id"] == authoritative_bundle.id
    assert bundle.allowed_tools == []
    assert bundle.expected_output_schema == {
        "schema_ref": "todos/todo.schema.json"
    }
    assert worker_jobs.job_calls[0]["selection_decision"] == {
        "selected_worker_id": "http://planner:5000",
        "selected_worker_kind": "worker",
        "selected_runtime_target_id": "runtime-target-alpha",
        "selected_runtime_kind": "docker_container",
        "selection_mode": "organization_research_destination",
        "policy_decision_ref": "d" * 64,
    }
    assert bundle.context_policy["mode"] == "authoritative_source_catalog_bundle"
    assert bundle.context_policy["llm_scope"] == "local_only"
    assert bundle.retrieval_hints == {
        "retrieval_intent": "authoritative_source_catalog",
        "required_context_scope": "exact_task_context_bundle",
        "preferred_bundle_mode": "authoritative",
    }
    assert (
        bundle.worker_execution_context["research_destination_binding"]
        == bundle.context_policy["research_destination_binding"]
    )
    assert bundle.delegation_payload["context_bundle_id"] == authoritative_bundle.id
    assert (
        bundle.delegation_payload["context_bundle_policy"]["llm_scope"]
        == "local_only"
    )
    assert bundle.delegation_payload["hub_dispatch_capability"].startswith(
        "ord2."
    )


def test_task_delegation_result_writer_forwards_then_updates_parent_and_returns_stable_model(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
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


def test_task_delegation_result_writer_reports_forwarding_failure_without_parent_update(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
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
    worker_jobs = _WorkerJobService()

    response = TaskDelegationResultWriter(deps).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response["error"] == "delegation_failed"
    assert response["code"] == 502
    assert response["data"]["reason_code"] == "worker_transport_failed"
    assert worker_jobs.failed_dispatches == [
        {
            "worker_job_id": "job-1",
            "reason_code": "worker_transport_failed",
            "rejected": False,
        }
    ]
    assert deps.update_calls == []


def test_result_writer_rejects_gateway_error_dict_without_parent_update(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
    deps = _Dependencies(
        forward_result={
            "status": "error",
            "message": "worker request failed",
        }
    )
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
        delegation_payload={"id": "sub-1"},
    )
    worker_jobs = _WorkerJobService()

    response = TaskDelegationResultWriter(deps).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response == {
        "error": "delegation_failed",
        "code": 502,
        "data": {"reason_code": "worker_transport_failed"},
    }
    assert worker_jobs.failed_dispatches == [
        {
            "worker_job_id": "job-1",
            "reason_code": "worker_transport_failed",
            "rejected": False,
        }
    ]
    assert deps.update_calls == []


def test_category_research_requires_positive_worker_acknowledgement(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
    deps = _Dependencies(
        forward_result={"status": "success", "data": {}}
    )
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=None,
        routing_hint=None,
        effective_task_kind="planning_research",
        effective_required_capabilities=[
            "planning",
            "research",
            "source_analysis",
        ],
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
        delegation_payload={
            "id": "sub-1",
            "hub_dispatch_capability": "ord2.payload.signature",
        },
    )
    worker_jobs = _WorkerJobService()

    response = TaskDelegationResultWriter(deps).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response["error"] == "delegation_failed"
    assert response["code"] == 502
    assert worker_jobs.failed_dispatches[0]["reason_code"] == (
        "worker_transport_failed"
    )
    assert deps.update_calls == []


def test_category_research_acknowledgement_requires_exact_task_id(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
    deps = _Dependencies(
        forward_result={
            "status": "success",
            "data": {"accepted": True},
        }
    )
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=None,
        routing_hint=None,
        effective_task_kind="planning_research",
        effective_required_capabilities=[
            "planning",
            "research",
            "source_analysis",
        ],
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
        delegation_payload={
            "id": "sub-1",
            "hub_dispatch_capability": "ord2.payload.signature",
        },
    )
    worker_jobs = _WorkerJobService()

    response = TaskDelegationResultWriter(deps).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response["error"] == "delegation_failed"
    assert response["code"] == 502
    assert worker_jobs.failed_dispatches[0]["reason_code"] == (
        "worker_transport_failed"
    )
    assert deps.update_calls == []


def test_category_research_redirect_is_not_followed_or_committed(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)

    class RedirectResponse:
        status_code = 307
        headers = {"Location": "https://outside.invalid/capture"}

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class Client:
        def __init__(self):
            self.calls = []
            self.response = RedirectResponse()

        def post(self, url, **kwargs):
            self.calls.append({"url": url, **kwargs})
            return self.response

    gateway = HttpWorkerGateway(timeout=5, retries=1)
    gateway.client = Client()

    class Dependencies(_Dependencies):
        def forward_task_to_worker(
            self,
            agent_url,
            endpoint,
            data,
            token=None,
        ):
            return gateway.forward_task(
                agent_url,
                endpoint,
                data,
                token=token,
            )

    class ResearchPolicy:
        @staticmethod
        def verify_forward(**_kwargs):
            return None

    deps = Dependencies()
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
        delegation_payload={
            "id": "sub-1",
            "hub_dispatch_capability": "ord2.payload.signature",
            "worker_execution_context": {
                "context": {"context_text": "sensitive local context"}
            },
        },
    )
    worker_jobs = _WorkerJobService()

    response = TaskDelegationResultWriter(
        deps,
        research_delegation_policy=ResearchPolicy(),
    ).forward_and_write(
        request=_request(),
        plan=TaskDelegationPlan(
            agent_url="http://planner:5000",
            selected_by_policy=True,
            selection=None,
            policy_decision=SimpleNamespace(id="policy-1"),
            routing_hint=None,
            effective_task_kind="planning_research",
            effective_required_capabilities=[
                "planning",
                "research",
                "source_analysis",
            ],
            preferred_backend="codex",
        ),
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response == {
        "error": "delegation_failed",
        "code": 502,
        "data": {"reason_code": "worker_transport_failed"},
    }
    assert len(gateway.client.calls) == 1
    assert gateway.client.calls[0]["allow_redirects"] is False
    assert gateway.client.response.closed is True
    assert worker_jobs.failed_dispatches == [
        {
            "worker_job_id": "job-1",
            "reason_code": "worker_transport_failed",
            "rejected": False,
        }
    ]
    assert deps.update_calls == []


def test_http_worker_gateway_does_not_project_worker_error_body():
    class ErrorResponse:
        status_code = 401
        text = "worker-secret-token"

        def __init__(self):
            self.closed = False
            self.json_called = False

        def json(self):
            self.json_called = True
            return {"token": "worker-secret-token"}

        def close(self):
            self.closed = True

    class Client:
        def __init__(self):
            self.response = ErrorResponse()

        def post(self, _url, **_kwargs):
            return self.response

    gateway = HttpWorkerGateway(timeout=5, retries=1)
    gateway.client = Client()

    result = gateway.forward_task(
        "http://planner:5000",
        "/tasks",
        {"id": "sub-1"},
        token="worker-service-token",
    )

    assert result == {
        "status": "error",
        "message": "worker_forward_failed",
        "http_status": 401,
    }
    assert gateway.client.response.json_called is False
    assert gateway.client.response.closed is True
    assert "worker-secret-token" not in repr(result)


def test_category_research_result_writer_rechecks_destination_immediately_before_forward(
    monkeypatch,
):
    _isolate_result_writer_repositories(monkeypatch)
    sequence = []

    class Dependencies(_Dependencies):
        def forward_task_to_worker(self, agent_url, endpoint, data, token=None):
            sequence.append("forward")
            return super().forward_task_to_worker(
                agent_url,
                endpoint,
                data,
                token=token,
            )

    class ResearchPolicy:
        def verify_forward(self, **kwargs):
            sequence.append("verify")
            assert kwargs["worker_url"] == "http://planner:5000"
            assert kwargs["expected_context_bundle_id"] == "ctx-1"
            assert kwargs["expected_destination_binding"]["destination_id"] == "dst-current"
            assert kwargs["expected_worker_job_id"] == "job-1"
            assert kwargs["expected_subtask_id"] == "sub-1"

    deps = Dependencies()
    request = _request()
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=SimpleNamespace(id="policy-1"),
        routing_hint=None,
        effective_task_kind="planning_research",
        effective_required_capabilities=["planning", "research"],
        preferred_backend="codex",
    )
    bundle = WorkerExecutionBundle(
        subtask_id="sub-1",
        context_bundle=SimpleNamespace(id="ctx-1"),
        context_policy={
            "research_destination_binding": {
                "destination_id": "dst-current"
            }
        },
        retrieval_hints={},
        task_neighborhood={},
        expected_output_schema={},
        allowed_tools=[],
        routing_decision=RoutingDecision({}),
        worker_job=SimpleNamespace(id="job-1"),
        workspace_scope={},
        worker_execution_context={},
        delegation_payload={
            "id": "sub-1",
            "hub_dispatch_capability": "ord2.payload.signature",
        },
    )

    response = TaskDelegationResultWriter(
        deps,
        research_delegation_policy=ResearchPolicy(),
    ).forward_and_write(request=request, plan=plan, bundle=bundle)

    assert response["data"]["status"] == "delegated"
    assert sequence == ["verify", "forward"]
    assert deps.forward_calls[0]["endpoint"] == (
        "/internal/tasks/organization-planning-research"
    )


def test_result_writer_rechecks_organization_binding_immediately_before_forward(
    monkeypatch,
):
    from agent.services import repository_registry

    authoritative = SimpleNamespace(
        id="parent-1",
        status="todo",
        organization_id="org-1",
        task_kind="planning",
        derivation_reason=None,
        status_reason_details={},
    )
    worker = SimpleNamespace(
        url="http://planner:5000",
        token="worker-token",
    )
    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: authoritative
            ),
            agent_repo=SimpleNamespace(
                get_by_url=lambda _worker_url: worker
            ),
        ),
    )
    deps = _Dependencies()
    worker_jobs = _WorkerJobService()
    request = _request(
        parent_overrides={"organization_id": "org-1"}
    )
    plan = TaskDelegationPlan(
        agent_url="http://planner:5000",
        selected_by_policy=True,
        selection=None,
        policy_decision=SimpleNamespace(id="policy-1"),
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
        delegation_payload={"id": "sub-1"},
    )

    response = TaskDelegationResultWriter(
        deps,
        organization_binding_resolver=SimpleNamespace(
            resolve=lambda _task: None
        ),
    ).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
        worker_job_service=worker_jobs,
    )

    assert response["error"] == (
        "organization_planning_dispatch_binding_required"
    )
    assert deps.forward_calls == []
    assert worker_jobs.failed_dispatches == [
        {
            "worker_job_id": "job-1",
            "reason_code": (
                "organization_planning_dispatch_binding_required"
            ),
            "rejected": True,
        }
    ]


def test_manual_recovery_delegation_fails_closed_without_worker_transport(
    monkeypatch,
):
    from agent.services import recovery_dispatch_gate_service, repository_registry

    deps = _Dependencies()
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
        subtask_id="sub-recovery",
        context_bundle=SimpleNamespace(id="ctx-recovery"),
        context_policy={},
        retrieval_hints={},
        task_neighborhood={},
        expected_output_schema={},
        allowed_tools=[],
        routing_decision=RoutingDecision({}),
        worker_job=SimpleNamespace(id="job-recovery"),
        workspace_scope={},
        worker_execution_context={},
        delegation_payload={"id": "sub-recovery"},
    )
    recovery_task = SimpleNamespace(
        id=request.task_id,
        derivation_reason="goal_task_recovery",
        status="todo",
    )

    class Gate:
        @staticmethod
        def is_recovery_child(_task):
            return True

        @contextlib.contextmanager
        def dispatch_guard(self, _task_id):
            yield SimpleNamespace(
                allowed=True,
                reason_code="recovery_release_gate_valid",
                source_task_id="source-recovery",
                plan_id="plan-recovery",
            )

    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: recovery_task
            ),
            agent_repo=SimpleNamespace(
                get_by_url=lambda _url: (_ for _ in ()).throw(
                    AssertionError(
                        "worker lookup must not run for manual recovery"
                    )
                )
            ),
        ),
    )
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: Gate(),
    )

    response = TaskDelegationResultWriter(
        deps
    ).forward_and_write(
        request=request,
        plan=plan,
        bundle=bundle,
    )

    assert response["error"] == (
        "recovery_child_delegation_not_supported"
    )
    assert response["code"] == 409
    assert response["data"] == {
        "source_task_id": "source-recovery",
        "plan_id": "plan-recovery",
    }
    assert deps.forward_calls == []
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
