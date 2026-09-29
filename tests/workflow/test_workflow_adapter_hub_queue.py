from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.model_routing_contract import ModelRoutingConfig
from agent.services.workflow_adapter_control_facade import (
    WORKFLOW_ADAPTER_COMMAND_SCHEMA,
    WorkflowAdapterControlFacade,
)
from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterQueueError,
    WorkflowAdapterTaskQueueService,
    WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_provider_selection_service import (
    HubConfiguredWorkflowProviderDecisionService,
    WorkflowProviderDecision,
    WorkflowProviderRequirement,
)
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    RuntimeAuthorizationEnvelope,
)
from ananta_contracts.provider_execution import ProviderExecutionBinding
from ananta_contracts.model_selection import (
    ModelAssignment,
    ModelFallbackCandidate,
    ModelFallbackGroup,
    ModelRoutingConfiguration,
)
from ananta_contracts.workflow_adapter_task import (
    WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA,
    WorkflowAdapterTask,
)


class _Repository:
    def __init__(self) -> None:
        self.values = {}

    def get_by_id(self, task_id):
        return self.values.get(task_id)


class _Queue:
    def __init__(self, repository: _Repository, *, fail: bool = False) -> None:
        self.repository = repository
        self.fail = fail
        self.calls = []

    def ingest_task(self, **values) -> None:
        self.calls.append(values)
        if self.fail:
            raise RuntimeError("queue unavailable")
        extra = dict(values["extra_fields"])
        self.repository.values[values["task_id"]] = SimpleNamespace(
            id=values["task_id"],
            status=values["status"],
            status_reason_code="",
            verification_status={},
            history=[
                {
                    "event_type": values["event_type"],
                    "timestamp": 1_001.0,
                    "details": dict(values["event_details"]),
                }
            ],
            **extra,
        )


class _Runtime:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository

    def update_local_task_status(self, task_id, status, **values) -> None:
        task = self.repository.values[task_id]
        task.status = status
        task.status_reason_code = str(values.get("status_reason_code") or "")
        task.history.append(
            {
                "event_type": values.get("event_type"),
                "timestamp": 1_002.0,
                "details": dict(values.get("event_details") or {}),
            }
        )


def _submission(**changes) -> WorkflowAdapterTaskSubmission:
    values = {
        "tenant_id": "tenant-a",
        "subject_id": "user-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "plan_hash": "a" * 64,
        "policy_version": "policy-v1",
        "adapter_kind": "langgraph",
        "command": "execute",
        "task_type": "agent_workflow",
        "payload": {
            "graph_id": "graph-a",
            "tenant_id": "attacker-tenant",
            "provider_context": {"max_attempts": 999},
        },
        "allowed_tools": ("read_file",),
        "allowed_artifacts": ("artifact-a",),
        "correlation_id": "correlation-a",
        "idempotency_key": "request-a",
        "maximum_retries": 2,
        "max_total_tokens": 2_000,
        "max_cost_micros": 10_000,
        "authorization_ttl_seconds": 600.0,
        "provider_binding": ProviderExecutionBinding(
            provider_id="lmstudio",
            model_id="model-a",
            source="hub_config.defaults",
            reason_code="hub_provider_policy_selected",
        ),
        "provider_decision_reason": "hub_provider_policy_selected",
    }
    values.update(changes)
    return WorkflowAdapterTaskSubmission(**values)


def _service(*, fail_queue: bool = False, events=None):
    repository = _Repository()
    queue = _Queue(repository, fail=fail_queue)
    ownership = InMemoryExecutionOwnershipStore()
    keys = HmacKeyRing({"key-1": b"x" * 32}, active_key_id="key-1")
    service = WorkflowAdapterTaskQueueService(
        task_queue=queue,
        task_repository=repository,
        task_runtime=_Runtime(repository),
        ownership=ownership,
        authorization_keys=keys,
        events=events or InMemoryEventStore(),
        clock=lambda: 1_000.0,
    )
    return service, repository, queue, ownership, keys


def test_hub_queue_persists_one_signed_fenced_and_routable_contract() -> None:
    service, repository, queue, ownership, keys = _service()

    receipt = service.submit(_submission())
    duplicate = service.submit(_submission())
    task = repository.values[receipt.hub_task_id]
    contract = WorkflowAdapterTask.from_mapping(task.worker_execution_context)
    envelope = RuntimeAuthorizationEnvelope.from_mapping(
        contract.authorization_envelope.to_dict()
    )
    AuthorizationVerifier(keys, InMemoryReplayNonceStore()).authorize(
        envelope,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        requested_budget={"attempts": 3, "tokens": 2_000},
        consume_nonce=False,
        now=1_001.0,
    )

    assert receipt.accepted is True
    assert duplicate.duplicate is True
    assert len(queue.calls) == 1
    assert task.required_capabilities == ["workflow.adapter.langgraph"]
    assert contract.payload["provider_context"] == {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "plan_hash": "a" * 64,
        "policy_version": "policy-v1",
        "prompt_version": "workflow-adapter-prompt-v1",
        "correlation_id": "correlation-a",
        "external_egress_allowed": False,
        "max_attempts": 3,
        "max_total_tokens": 2_000,
        "max_completion_tokens_per_call": 1_000,
        "max_cost_micros": 10_000,
        "require_hub_retry_budget": True,
        "require_hub_provider_budget": True,
        "provider_transport_mode": "hub_bound",
        "provider_decision_reason": "hub_provider_policy_selected",
        "provider_binding_id": contract.provider_binding.binding_id,
        "selected_provider_id": "lmstudio",
        "selected_model_id": "model-a",
        "combined_retry_maximum": 2,
        "authorization_envelope": envelope.to_dict(),
        "attempt_id": contract.attempt_id,
        "fencing_token": contract.fencing_token,
    }
    assert "tenant_id" not in contract.payload
    assert contract.provider_binding is not None
    assert contract.provider_binding.provider_id == "lmstudio"
    assert receipt.to_dict()["provider_binding"]["model_id"] == "model-a"
    assert ownership.get(
        tenant_id="tenant-a", run_id="run-a", step_id="step-a"
    ).status == "active"


def test_hub_builds_and_transports_phi_gemma_profile_contexts_with_route_budget() -> None:
    decision = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "model_profiles_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
            ),
            "model_routing_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_routing.json"
            ),
        }
    ).decide(
        WorkflowProviderRequirement(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            step_id="step-a",
            task_type="agent_workflow",
            runtime_kind="langgraph",
            requires_provider=True,
        )
    )
    service, repository, _queue, _ownership, _keys = _service()

    receipt = service.submit(
        _submission(
            maximum_retries=0,
            provider_binding=decision.binding,
            provider_decision_reason=decision.reason_code,
            primary_profile_id=decision.primary_profile_id,
            provider_profile_bindings=decision.profile_bindings,
            provider_attempt_plan=decision.profile_attempt_plan,
            provider_maximum_attempts=decision.maximum_provider_attempts,
            payload={
                "graph_id": "graph-a",
                "provider_contexts_by_profile_id": {
                    "attacker": {"selected_model_id": "unbound"}
                },
            },
        )
    )
    task = repository.values[receipt.hub_task_id]
    contract = WorkflowAdapterTask.from_mapping(task.worker_execution_context)
    contexts = contract.payload["provider_contexts_by_profile_id"]
    primary = contract.payload["provider_context"]
    envelope = RuntimeAuthorizationEnvelope.from_mapping(
        contract.authorization_envelope.to_dict()
    )

    assert decision.maximum_provider_attempts == 5
    assert contract.provider_maximum_attempts == 5
    assert primary["max_attempts"] == 5
    assert primary["combined_retry_maximum"] == 0
    assert primary["require_hub_retry_budget"] is False
    assert primary["require_hub_provider_attempt_budget"] is True
    assert primary["provider_endpoint_identity"] == (
        "http://ollama:11434/v1/chat/completions"
    )
    assert envelope.budgets["attempts"] == 1
    assert envelope.budgets["retries"] == 0
    assert envelope.budgets["provider_attempts"] == 5
    assert [
        (item.profile_id, item.maximum_attempts)
        for item in envelope.provider_attempt_plan
    ] == [
        ("local_ollama_phi4_mini", 3),
        ("local_ollama_gemma4_e4b_reasoning", 2),
    ]
    assert {
        (
            item.binding_id,
            item.provider_id,
            item.model_id,
            item.endpoint_identity,
        )
        for item in envelope.allowed_provider_bindings
    } == {
        (
            item.binding.binding_id,
            item.binding.provider_id,
            item.binding.model_id,
            "http://ollama:11434/v1/chat/completions",
        )
        for item in decision.profile_bindings
    }
    assert set(contexts) == {
        "local_ollama_phi4_mini",
        "local_ollama_gemma4_e4b_reasoning",
    }
    assert contexts["local_ollama_phi4_mini"] == primary
    assert contexts["local_ollama_gemma4_e4b_reasoning"][
        "selected_model_id"
    ] == "ananta-gemma4-reasoning-8k"
    assert contexts["local_ollama_gemma4_e4b_reasoning"][
        "provider_endpoint_identity"
    ] == "http://ollama:11434/v1/chat/completions"
    assert contract.worker_payload()[
        "provider_contexts_by_profile_id"
    ] == contexts
    assert "attacker" not in contexts

    tampered = dict(task.worker_execution_context)
    tampered["payload"] = dict(tampered["payload"])
    tampered_contexts = {
        key: dict(value)
        for key, value in tampered["payload"][
            "provider_contexts_by_profile_id"
        ].items()
    }
    tampered_contexts["local_ollama_gemma4_e4b_reasoning"][
        "selected_model_id"
    ] = "attacker-model"
    tampered["payload"]["provider_contexts_by_profile_id"] = (
        tampered_contexts
    )
    with pytest.raises(
        ValueError,
        match="provider_profile_binding_mismatch",
    ):
        WorkflowAdapterTask.from_mapping(tampered)


def test_hub_signed_attempt_plan_controls_worker_despite_local_retry_drift(
    monkeypatch,
) -> None:
    from agent.services.model_invocation_service import (
        LLMUnavailableError,
        ModelInvocationService,
    )
    from agent.services.model_profile_loader import ModelProfile
    from agent.services.model_profile_resolver import (
        ModelProfileResolver,
        RoutingRules,
    )
    from worker.runtime.provider_text_generation import (
        HubProfileRoutedWorkerTextGeneration,
    )

    decision = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "model_profiles_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
            ),
            "model_routing_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_routing.json"
            ),
        }
    ).decide(
        WorkflowProviderRequirement(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            step_id="step-a",
            task_type="agent_workflow",
            runtime_kind="langgraph",
            requires_provider=True,
        )
    )
    service, repository, _queue, _ownership, _keys = _service()
    receipt = service.submit(
        _submission(
            maximum_retries=0,
            provider_binding=decision.binding,
            provider_decision_reason=decision.reason_code,
            primary_profile_id=decision.primary_profile_id,
            provider_profile_bindings=decision.profile_bindings,
            provider_attempt_plan=decision.profile_attempt_plan,
            provider_maximum_attempts=decision.maximum_provider_attempts,
        )
    )
    contract = WorkflowAdapterTask.from_mapping(
        repository.values[receipt.hub_task_id].worker_execution_context
    )
    payload = contract.worker_payload()

    # Deliberately reverse local routing and invert retry budgets. These are
    # technical profile records only; the signed Hub plan remains authoritative.
    phi = ModelProfile(
        profile_id="local_ollama_phi4_mini",
        provider_id="ollama",
        model="ananta-phi4-mini-32k",
        local=True,
        retry_budget=0,
        fallback_group="drifted",
        fallback_rank=20,
        base_url="http://ollama:11434/v1",
    )
    gemma = ModelProfile(
        profile_id="local_ollama_gemma4_e4b_reasoning",
        provider_id="ollama",
        model="ananta-gemma4-reasoning-8k",
        local=True,
        retry_budget=12,
        fallback_group="drifted",
        fallback_rank=10,
        base_url="http://ollama:11434/v1",
    )
    resolver = ModelProfileResolver(
        [gemma, phi],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "drifted": {
                        "ordered_profiles": [
                            gemma.profile_id,
                            phi.profile_id,
                        ],
                        "max_total_retries": 12,
                    }
                }
            }
        ),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_resolver",
        classmethod(lambda cls: resolver),
    )
    monkeypatch.setattr(
        ModelInvocationService,
        "_get_settings",
        classmethod(
            lambda cls: SimpleNamespace(
                default_provider="ollama",
                default_model="auto",
                lmstudio_url="",
                ollama_url="http://ollama:11434/v1",
                openai_url="",
                openai_api_key=None,
                mock_url="",
                llm_invoke_timeout_seconds=120,
            )
        ),
    )
    calls: list[tuple[str, str]] = []

    def invoke_once(cls, messages, **values):  # noqa: ANN001
        attempt = values["attempt"]
        context = values["provider_context"]
        calls.append(
            (
                attempt["profile"].profile_id,
                context.provider_profile_id,
            )
        )
        if len(calls) < 5:
            raise LLMUnavailableError(
                "simulated timeout",
                terminal_reason="provider_timeout",
            )
        return {
            "choices": [{"message": {"content": "gemma ok"}}],
            "metadata": {},
            "model": attempt["model"],
            "usage": {},
        }

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(invoke_once),
    )
    worker = HubProfileRoutedWorkerTextGeneration(
        direct=SimpleNamespace(
            generate_text=lambda **values: pytest.fail(
                "signed profile route must not use direct provider path"
            )
        ),
        model_routing=ModelInvocationService,
    )
    primary = payload["provider_context"]
    result = worker.generate_text(
        **payload,
        prompt="execute signed route",
        provider=primary["selected_provider_id"],
        model=primary["selected_model_id"],
    )

    expected_profiles = (
        ["local_ollama_phi4_mini"] * 3
        + ["local_ollama_gemma4_e4b_reasoning"] * 2
    )
    assert result["text"] == "gemma ok"
    assert [profile for profile, _context_profile in calls] == (
        expected_profiles
    )
    assert [
        context_profile for _profile, context_profile in calls
    ] == expected_profiles


def test_queue_enforces_idempotency_and_principal_tenant_binding() -> None:
    service, _repository, _queue, _ownership, _keys = _service()
    receipt = service.submit(_submission())

    with pytest.raises(WorkflowAdapterQueueError, match="idempotency_conflict"):
        service.submit(_submission(payload={"graph_id": "other"}))
    for tenant_id, subject_id in (("tenant-b", "user-a"), ("tenant-a", "user-b")):
        with pytest.raises(WorkflowAdapterQueueError) as exc:
            service.status(
                tenant_id=tenant_id,
                subject_id=subject_id,
                hub_task_id=receipt.hub_task_id,
            )
        assert exc.value.status_code == 404


def test_terminal_results_are_validated_acknowledged_and_cancellable() -> None:
    service, repository, _queue, ownership, _keys = _service()
    receipt = service.submit(_submission())
    task = repository.values[receipt.hub_task_id]
    task.status = "completed"
    task.verification_status = {
        "workflow_adapter_task_result": {
            "schema": WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA,
            "hub_task_id": receipt.hub_task_id,
            "adapter_kind": "langgraph",
            "status": "success",
            "summary": "done",
            "artifacts": [],
            "sources": [],
            "adapter_result": {},
        }
    }

    status = service.status(
        tenant_id="tenant-a",
        subject_id="user-a",
        hub_task_id=receipt.hub_task_id,
    )
    assert status["result"]["status"] == "success"
    assert ownership.get(tenant_id="tenant-a", run_id="run-a", step_id="step-a").status == "completed"

    second = _submission(
        workflow_id="workflow-b",
        run_id="run-b",
        step_id="step-b",
        idempotency_key="request-b",
    )
    second_receipt = service.submit(second)
    cancelled = service.cancel(
        tenant_id="tenant-a",
        subject_id="user-a",
        hub_task_id=second_receipt.hub_task_id,
        reason="operator cancelled",
    )
    assert cancelled["status"] == "cancelled"
    assert ownership.get(tenant_id="tenant-a", run_id="run-b", step_id="step-b").status == "failed"


def test_langgraph_execution_trace_is_losslessly_projected_as_canonical_events() -> None:
    events = InMemoryEventStore()
    service, repository, _queue, _ownership, _keys = _service(events=events)
    receipt = service.submit(_submission())
    task = repository.values[receipt.hub_task_id]
    trace = [
        {"event": "workflow.run.started", "plan_hash": "a" * 64},
        {
            "event": "workflow.batch.scheduled",
            "batch": 0,
            "node_ids": ["step-a"],
        },
        {
            "event": "workflow.step.completed",
            "node_id": "step-a",
            "reason_code": "",
            "failed_branches": [],
        },
        {"event": "workflow.run.completed", "reason_code": ""},
    ]
    task.status = "completed"
    task.verification_status = {
        "workflow_adapter_task_result": {
            "schema": WORKFLOW_ADAPTER_TASK_RESULT_SCHEMA,
            "hub_task_id": receipt.hub_task_id,
            "adapter_kind": "langgraph",
            "status": "success",
            "summary": "done",
            "artifacts": [],
            "sources": [],
            "adapter_result": {
                "schema": "workflow_artifact_result.v1",
                "artifacts": [
                    {
                        "schema": "ananta.langgraph_execution_plan_result.v1",
                        "records": [
                            {
                                "node_id": "step-a",
                                "status": "completed",
                                "value": {"answer": 42},
                                "artifacts": {"report": "artifact://report-a"},
                                "reason_code": "",
                                "failed_branches": [],
                                "tokens": 7,
                                "cost_micros": 11,
                            }
                        ],
                        "trace": trace,
                    }
                ],
            },
        }
    }

    service.status(
        tenant_id="tenant-a",
        subject_id="user-a",
        hub_task_id=receipt.hub_task_id,
    )
    # Polling is idempotent and cannot append a second copy.
    service.status(
        tenant_id="tenant-a",
        subject_id="user-a",
        hub_task_id=receipt.hub_task_id,
    )
    history = service.history(
        tenant_id="tenant-a",
        subject_id="user-a",
        hub_task_id=receipt.hub_task_id,
    )

    projected = [
        event for event in history if event["causation_id"] == f"hub-task:{receipt.hub_task_id}"
    ]
    assert [event["event_type"] for event in projected] == [
        item["event"] for item in trace
    ]
    step = next(
        event for event in projected if event["event_type"] == "workflow.step.completed"
    )
    assert step["tenant_id"] == "tenant-a"
    assert step["workflow_id"] == "workflow-a"
    assert step["run_id"] == "run-a"
    assert step["step_id"] == "step-a"
    assert step["attempt"] == 1
    assert step["payload"]["node_result"]["value"] == {"answer": 42}
    assert step["payload"]["node_result"]["artifacts"] == {
        "report": "artifact://report-a"
    }


def test_invalid_or_unpersisted_submission_never_leaves_active_ownership() -> None:
    service, _repository, _queue, ownership, _keys = _service()
    with pytest.raises(WorkflowAdapterQueueError, match="embedded_secret_denied"):
        service.submit(_submission(payload={"api_key": "secret"}))
    with pytest.raises(WorkflowAdapterQueueError, match="plan_hash_invalid"):
        service.submit(_submission(plan_hash="not-a-plan-hash"))
    assert ownership.get(tenant_id="tenant-a", run_id="run-a", step_id="step-a") is None

    failing, _repository, _queue, failed_ownership, _keys = _service(fail_queue=True)
    with pytest.raises(WorkflowAdapterQueueError, match="queue_persistence_failed"):
        failing.submit(_submission())
    assert failed_ownership.get(
        tenant_id="tenant-a", run_id="run-a", step_id="step-a"
    ).status == "failed"


class _FacadeQueue:
    def __init__(self) -> None:
        self.submission = None

    def submit(self, submission):
        self.submission = submission
        return SimpleNamespace(
            to_dict=lambda: {
                "hub_task_id": "wfa-test",
                "workflow_id": submission.workflow_id,
                "duplicate": False,
            },
            hub_task_id="wfa-test",
            workflow_id=submission.workflow_id,
        )


class _ProviderDecisions:
    def __init__(self, decision: WorkflowProviderDecision) -> None:
        self.decision = decision
        self.requirement = None

    def decide(self, requirement):
        self.requirement = requirement
        return self.decision


def test_control_facade_derives_plan_budget_and_principal_binding() -> None:
    queue = _FacadeQueue()
    binding = ProviderExecutionBinding(
        provider_id="lmstudio",
        model_id="model-a",
        source="hub_config.defaults",
        reason_code="hub_provider_policy_selected",
    )
    decisions = _ProviderDecisions(
        WorkflowProviderDecision(
            status="selected",
            reason_code="hub_provider_policy_selected",
            binding=binding,
        )
    )
    control = WorkflowAdapterControlFacade(
        queue,
        provider_decisions=decisions,
    ).bind(
        WorkflowRoutePrincipal(tenant_id="tenant-a", subject="user-a")
    )

    response = control.submit(
        kind="langgraph",
        command="execute",
        idempotency_key="header-idempotency",
        body={
            "schema": WORKFLOW_ADAPTER_COMMAND_SCHEMA,
            "task_type": "agent_workflow",
            "allowed_tools": ["read_file"],
            "maximum_retries": 1,
            "max_total_tokens": 1_000,
            "max_cost_micros": 2_000,
            "model_routing": {
                "preferred_profile_id": "trusted-profile"
            },
            "payload": {"graph_id": "graph-a"},
        },
    )

    assert response["control_path"] == "hub_task_queue"
    assert response["poll_url"].endswith("/wfa-test")
    assert queue.submission.tenant_id == "tenant-a"
    assert queue.submission.subject_id == "user-a"
    assert queue.submission.idempotency_key == "header-idempotency"
    assert queue.submission.plan_hash.startswith("sha256:") or len(queue.submission.plan_hash) == 64
    assert queue.submission.max_total_tokens == 1_000
    assert queue.submission.max_cost_micros == 2_000
    assert queue.submission.provider_binding == binding
    assert decisions.requirement.runtime_kind == "langgraph"
    assert decisions.requirement.model_routing.preferred_profile_id == (
        "trusted-profile"
    )
    assert queue.submission.model_routing == {
        "preferred_profile_id": "trusted-profile"
    }


def test_control_facade_rejects_invalid_routing_and_ignores_payload_routing() -> None:
    queue = _FacadeQueue()
    decisions = _ProviderDecisions(
        WorkflowProviderDecision(
            status="not_required",
            reason_code="provider_transport_not_required",
        )
    )
    control = WorkflowAdapterControlFacade(
        queue,
        provider_decisions=decisions,
    ).bind(
        WorkflowRoutePrincipal(tenant_id="tenant-a", subject="user-a")
    )

    with pytest.raises(
        WorkflowAdapterQueueError,
        match="model_routing_invalid",
    ):
        control.submit(
            kind="langgraph",
            command="dry_run",
            body={
                "task_type": "agent_workflow",
                "model_routing": {"worker_smuggled_field": True},
            },
        )

    control.submit(
        kind="langgraph",
        command="dry_run",
        body={
            "task_type": "agent_workflow",
            "payload": {
                "model_routing": {
                    "preferred_profile_id": "payload-smuggling"
                }
            },
        },
    )
    assert decisions.requirement.model_routing is None
    assert queue.submission.model_routing == {}


def test_execute_fails_closed_without_hub_provider_decision_and_dry_run_is_provider_free() -> None:
    queue = _FacadeQueue()
    missing = _ProviderDecisions(
        WorkflowProviderDecision(
            status="denied",
            reason_code="provider_model_not_resolved",
        )
    )
    bound = WorkflowAdapterControlFacade(
        queue,
        provider_decisions=missing,
    ).bind(WorkflowRoutePrincipal(tenant_id="tenant-a", subject="user-a"))

    with pytest.raises(WorkflowAdapterQueueError, match="provider_selection_unavailable"):
        bound.submit(
            kind="langgraph",
            command="execute",
            body={"task_type": "agent_workflow"},
        )

    no_transport = _ProviderDecisions(
        WorkflowProviderDecision(
            status="not_required",
            reason_code="provider_transport_not_required",
        )
    )
    dry_run = WorkflowAdapterControlFacade(
        queue,
        provider_decisions=no_transport,
    ).bind(WorkflowRoutePrincipal(tenant_id="tenant-a", subject="user-a"))
    dry_run.submit(
        kind="langgraph",
        command="dry_run",
        body={"task_type": "agent_workflow"},
    )

    assert queue.submission.provider_binding is None
    assert queue.submission.provider_decision_reason == "provider_transport_not_required"


def test_configured_decision_is_identical_for_native_langchain_and_langgraph() -> None:
    subject = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "default_provider": "lmstudio",
            "default_model": "model-a",
        }
    )
    decisions = []
    for runtime_kind in ("native", "langchain", "langgraph"):
        requirement = WorkflowProviderRequirement(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            step_id="step-a",
            task_type="agent_workflow",
            runtime_kind=runtime_kind,
            requires_provider=True,
        )
        decisions.append(subject.decide(requirement).binding)

    assert decisions[0] == decisions[1] == decisions[2]


def test_profile_decision_honors_trusted_gemma_primary_and_fallback_group() -> None:
    subject = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "model_profiles_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
            ),
            "model_routing_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_routing.json"
            ),
        }
    )
    decision = subject.decide(
        WorkflowProviderRequirement(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            step_id="step-a",
            task_type="reasoning",
            runtime_kind="langgraph",
            requires_provider=True,
            model_routing=ModelRoutingConfig(
                model_role="reasoning",
                preferred_profile_id=(
                    "local_ollama_gemma4_e4b_reasoning"
                ),
                fallback_group_id="local_phi_to_gemma_reasoning",
            ),
        )
    )

    assert decision.status == "selected"
    assert decision.primary_profile_id == (
        "local_ollama_gemma4_e4b_reasoning"
    )
    assert decision.binding is not None
    assert decision.binding.model_id == "ananta-gemma4-reasoning-8k"
    assert [item.profile_id for item in decision.profile_bindings] == [
        "local_ollama_gemma4_e4b_reasoning",
        "local_ollama_phi4_mini",
    ]
    assert decision.maximum_provider_attempts == 5

    denied = subject.decide(
        WorkflowProviderRequirement(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            step_id="step-a",
            task_type="reasoning",
            runtime_kind="langgraph",
            requires_provider=True,
            model_routing=ModelRoutingConfig(
                fallback_group_id="unconfigured-group"
            ),
        )
    )
    assert denied.status == "denied"
    assert denied.reason_code == "provider_fallback_group_not_found"


def test_central_routing_is_compiled_into_hub_signed_attempt_plan() -> None:
    configuration = ModelRoutingConfiguration(
        revision=7,
        assignments=(ModelAssignment(
            consumer_id="task.coding",
            scope="global",
            mode="profile",
            profile_id="local_ollama_gemma4_e4b_reasoning",
            fallback_group_id="central-code",
        ),),
        fallback_groups=(ModelFallbackGroup(
            group_id="central-code",
            max_total_retries=1,
            candidates=(
                ModelFallbackCandidate(
                    profile_id="local_ollama_gemma4_e4b_reasoning",
                    retry_budget=1,
                    triggers=("timeout",),
                ),
                ModelFallbackCandidate(
                    profile_id="local_ollama_phi4_mini",
                    retry_budget=2,
                ),
            ),
        ),),
    )
    subject = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "model_profiles_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
            ),
            "model_routing_path": (
                "config/models/"
                "local-ollama-phi-gemma-rtx3080.model_routing.json"
            ),
        },
        lambda: configuration,
    )

    decision = subject.decide(WorkflowProviderRequirement(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        step_id="step-a",
        task_type="coding",
        runtime_kind="native",
        requires_provider=True,
    ))

    assert decision.status == "selected"
    assert decision.primary_profile_id == "local_ollama_gemma4_e4b_reasoning"
    assert [item.profile_id for item in decision.profile_attempt_plan] == [
        "local_ollama_gemma4_e4b_reasoning",
        "local_ollama_phi4_mini",
    ]
    assert [item.maximum_attempts for item in decision.profile_attempt_plan] == [2, 1]
    assert decision.profile_attempt_plan[0].allowed_error_types == ("timeout",)
    assert decision.maximum_provider_attempts == 3
