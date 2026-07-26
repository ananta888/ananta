from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.native_graph_orchestration_service import (
    NativeGraphOrchestrator,
    NativeGraphRequest,
)
from agent.services.workflow_provider_selection_service import (
    HubConfiguredWorkflowProviderDecisionService,
    WorkflowProviderDecision,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    SignedCheckpoint,
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)
from tests.workflow_runtime_contract_fixtures import (
    n_minus_one_runtime_contract_fixture,
)
from worker.runtime.native_graph import (
    HubTaskReceipt,
    NativeDelegatedNodeRuntime,
    NativeNodeCommand,
    NativeNodeResult,
)


class AllowPolicy:
    def authorize_command(self, command, *, plan, state):
        return True, "allow"

    def authorize_delegation(self, *, plan, node, state):
        return True, "allow"

    def allow_node(self, command):
        return True, "allow"


class HubRevalidator:
    def revalidate(self, envelope):
        return True


class StaticProviderDecisions:
    def __init__(self, decision: WorkflowProviderDecision):
        self.decision = decision
        self.requirements = []

    def decide(self, requirement):
        self.requirements.append(requirement)
        return self.decision


class DeterministicHandler:
    def __init__(self, *, fail_once: set[str] | None = None):
        self.calls: list[str] = []
        self._fail_once = set(fail_once or ())

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        self.calls.append(command.node.node_id)
        should_fail = command.node.node_id in self._fail_once
        self._fail_once.discard(command.node.node_id)
        return NativeNodeResult(
            result_id=f"result-{command.node.node_id}-{len(self.calls)}",
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=command.node.node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status="failed" if should_fail else "completed",
            output_data={"value": command.node.node_id},
            artifact_refs=(
                {}
                if should_fail
                else {
                    artifact_id: f"artifact://test/{command.run_id}/{artifact_id}"
                    for artifact_id in command.node.output_artifacts
                }
            ),
            budget_usage={"tokens": 1, "cost_micros": 1},
            reason_code="scripted_failure" if should_fail else "",
        )


class ImmediateHubQueue:
    """Test adapter only: records Hub delegation before invoking a worker."""

    def __init__(self, runtime: NativeDelegatedNodeRuntime):
        self.runtime = runtime
        self.submissions: list[NativeNodeCommand] = []
        self.results: dict[str, NativeNodeResult] = {}
        self.cancelled: list[str] = []

    def submit(self, command: NativeNodeCommand) -> HubTaskReceipt:
        self.submissions.append(command)
        task_id = f"hub-task-{len(self.submissions)}"
        self.results[task_id] = self.runtime.execute(command, hub_task_id=task_id)
        return HubTaskReceipt(task_id, command.command_id, True)

    def poll(self, *, tenant_id: str, run_id: str, hub_task_ids: tuple[str, ...]):
        values = []
        for task_id in hub_task_ids:
            result = self.results.pop(task_id, None)
            if result is not None:
                values.append(result)
        return tuple(values)

    def cancel(self, *, tenant_id: str, run_id: str, hub_task_ids: tuple[str, ...], reason: str):
        self.cancelled.extend(hub_task_ids)


def plan(*, approval: bool = False, parallel: bool = False, retry: bool = False) -> ExecutionPlan:
    if parallel:
        return ExecutionPlan.from_mapping(
            {
                "tenant_id": "tenant-a",
                "plan_id": "parallel-v1",
                "workflow_id": "parallel",
                "policy_version": "policy-v1",
                "capabilities": ["bounded_parallel", "deterministic_merge"],
                "nodes": [
                    {
                        "id": "b",
                        "required_capabilities": ["bounded_parallel"],
                        "output_artifacts": ["b-out"],
                        "metadata": {"parallel_group": "g", "parallel_limit": 2},
                    },
                    {
                        "id": "a",
                        "required_capabilities": ["bounded_parallel"],
                        "output_artifacts": ["a-out"],
                        "metadata": {"parallel_group": "g", "parallel_limit": 2},
                    },
                    {
                        "id": "merge",
                        "node_type": "merge",
                        "required_capabilities": ["deterministic_merge"],
                        "input_artifacts": ["a-out", "b-out"],
                        "output_artifacts": ["merged"],
                        "metadata": {
                            "merge_strategy": "ordered-by-node-id",
                            "partial_failure": "fail",
                        },
                    },
                ],
                "edges": [{"from": "a", "to": "merge"}, {"from": "b", "to": "merge"}],
                "artifacts": [{"id": "a-out"}, {"id": "b-out"}, {"id": "merged"}],
                "budget": {"max_attempts": 1, "max_tokens": 20, "max_cost_micros": 20},
            }
        )
    second = {
        "id": "publish" if approval else "analyze",
        "input_artifacts": ["draft"],
        "output_artifacts": ["published" if approval else "report"],
        "gate_id": "publish-gate" if approval else "",
        "side_effect_class": "idempotent_write" if approval else "none",
        "allowed_tools": ["artifact.publish"] if approval else [],
        "metadata": {"operation_name": "publish-artifact"} if approval else {},
    }
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-a",
            "plan_id": "native-v1",
            "workflow_id": "native-test",
            "policy_version": "policy-v1",
            "capabilities": ["approval", "tool_calling"] if approval else [],
            "nodes": [
                {
                    "id": "draft",
                    "output_artifacts": ["draft"],
                    "budget": {"max_attempts": 2 if retry else 1},
                },
                second,
            ],
            "edges": [{"from": "draft", "to": second["id"]}],
            "gates": ([{"id": "publish-gate", "required_roles": ["operator"]}] if approval else []),
            "artifacts": [
                {"id": "draft"},
                {"id": "published" if approval else "report"},
            ],
            "budget": {
                "max_attempts": 2 if retry else 1,
                "max_tokens": 20,
                "max_cost_micros": 20,
            },
        }
    )


def runtime(*, fail_once: set[str] | None = None, provider_decisions=None):
    keys = HmacKeyRing({"native-key": "k" * 32}, active_key_id="native-key")
    ledger = InMemorySideEffectLedger()
    handler = DeterministicHandler(fail_once=fail_once)
    worker = NativeDelegatedNodeRuntime(
        handler=handler,
        authorization_verifier=AuthorizationVerifier(keys, InMemoryReplayNonceStore(clock=lambda: 100.0)),
        policy=AllowPolicy(),
        capabilities=frozenset(
            {
                "approval",
                "bounded_parallel",
                "deterministic_merge",
                "retrieval",
                "structured_output",
                "tool_calling",
            }
        ),
        ledger=ledger,
        hub_revalidator=HubRevalidator(),
        clock=lambda: 100.0,
    )
    queue = ImmediateHubQueue(worker)
    stores = {
        "checkpoints": InMemoryCheckpointStore(),
        "events": InMemoryEventStore(),
        "ownership": InMemoryExecutionOwnershipStore(),
    }
    orchestrator = NativeGraphOrchestrator(
        queue=queue,
        checkpoints=stores["checkpoints"],
        events=stores["events"],
        ownership=stores["ownership"],
        ledger=ledger,
        key_ring=keys,
        command_verifier=WorkflowCommandVerifier(keys, InMemoryReplayNonceStore(clock=lambda: 100.0)),
        policy=AllowPolicy(),
        provider_decisions=provider_decisions,
        clock=lambda: 100.0,
    )
    return orchestrator, queue, handler, keys, ledger, stores


def request(value: ExecutionPlan, *, run_id: str = "run-1") -> NativeGraphRequest:
    return NativeGraphRequest(value, run_id, f"control-{run_id}", tenant_parallel_limit=2)


def provider_plan() -> ExecutionPlan:
    value = plan().to_dict()
    value.pop("plan_hash", None)
    value["nodes"][0]["metadata"] = {"provider_transport": "required"}
    return ExecutionPlan.from_mapping(value)


def test_native_provider_node_fails_closed_without_hub_decision() -> None:
    decisions = StaticProviderDecisions(
        WorkflowProviderDecision("denied", "provider_binding_required")
    )
    orchestrator, queue, _, _, _, _ = runtime(provider_decisions=decisions)

    result = orchestrator.start(request(provider_plan()))

    assert result.status == "failed"
    assert result.reason_code == (
        "native_provider_selection_unavailable:provider_binding_required"
    )
    assert queue.submissions == []
    assert decisions.requirements[0].runtime_kind == "ananta-native"


def test_native_provider_node_receives_immutable_hub_binding() -> None:
    binding = ProviderExecutionBinding(
        provider_id="ollama",
        model_id="qwen2.5-coder",
        source="hub_profile.workflow_runtime.provider_selection",
        reason_code="hub_provider_policy_selected",
    )
    decisions = StaticProviderDecisions(
        WorkflowProviderDecision("selected", "hub_provider_policy_selected", binding)
    )
    orchestrator, queue, _, _, _, _ = runtime(provider_decisions=decisions)

    result = orchestrator.start(request(provider_plan()))

    assert result.status == "running"
    assert queue.submissions[0].provider_binding == binding


def test_native_provider_node_uses_compiled_gemma_route_and_separate_budget() -> None:
    value = plan().to_dict()
    value.pop("plan_hash", None)
    value["nodes"][0]["metadata"] = {
        "provider_transport": "required",
        "model_routing": {
            "model_role": "reasoning",
            "preferred_profile_id": (
                "local_ollama_gemma4_e4b_reasoning"
            ),
            "fallback_group_id": "local_phi_to_gemma_reasoning",
        },
    }
    routed_plan = ExecutionPlan.from_mapping(value)
    decisions = HubConfiguredWorkflowProviderDecisionService(
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
    orchestrator, queue, _, _, _, _ = runtime(
        provider_decisions=decisions
    )

    result = orchestrator.start(request(routed_plan))

    assert result.status == "running"
    command = queue.submissions[0]
    assert command.primary_profile_id == (
        "local_ollama_gemma4_e4b_reasoning"
    )
    assert command.provider_binding.model_id == (
        "ananta-gemma4-reasoning-8k"
    )
    assert [item.profile_id for item in command.provider_profile_bindings] == [
        "local_ollama_gemma4_e4b_reasoning",
        "local_ollama_phi4_mini",
    ]
    assert command.provider_maximum_attempts == 5
    assert command.authorization.budgets["attempts"] == 1
    assert command.authorization.budgets["provider_attempts"] == 5
    assert command.provider_context["max_attempts"] == 5
    assert command.provider_context["require_hub_retry_budget"] is False


def test_native_provider_nodes_keep_node_caps_and_share_signed_run_ceiling() -> None:
    provider_bindings = tuple(
        ProviderProfileExecutionBinding(
            profile_id=profile_id,
            binding=ProviderExecutionBinding(
                provider_id="ollama",
                model_id=model_id,
                source="hub_model_profile_routing",
                reason_code="hub_provider_profile_selected",
            ),
        )
        for profile_id, model_id in (
            ("phi-primary", "phi4-mini:latest"),
            ("gemma-fallback", "gemma4:e4b-it-qat"),
        )
    )
    attempt_plan = tuple(
        ProviderProfileAttemptPlanEntry.from_profile_binding(
            binding,
            maximum_attempts=maximum,
        )
        for binding, maximum in zip(
            provider_bindings,
            (3, 2),
            strict=True,
        )
    )
    decision = WorkflowProviderDecision(
        status="selected",
        reason_code="hub_provider_profile_selected",
        binding=provider_bindings[0].binding,
        primary_profile_id=provider_bindings[0].profile_id,
        profile_bindings=provider_bindings,
        profile_attempt_plan=attempt_plan,
        maximum_provider_attempts=5,
    )
    two_node_plan = ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-a",
            "plan_id": "provider-budget-v1",
            "workflow_id": "provider-budget",
            "policy_version": "policy-v1",
            "capabilities": ["bounded_parallel"],
            "nodes": [
                {
                    "id": "small",
                    "required_capabilities": ["bounded_parallel"],
                    "budget": {
                        "max_attempts": 1,
                        "max_tokens": 30,
                        "max_cost_micros": 300,
                    },
                    "metadata": {
                        "parallel_group": "provider",
                        "parallel_limit": 2,
                        "provider_transport": "required",
                    },
                },
                {
                    "id": "large",
                    "required_capabilities": ["bounded_parallel"],
                    "budget": {
                        "max_attempts": 1,
                        "max_tokens": 50,
                        "max_cost_micros": 500,
                    },
                    "metadata": {
                        "parallel_group": "provider",
                        "parallel_limit": 2,
                        "provider_transport": "required",
                    },
                },
            ],
            "budget": {
                "max_attempts": 1,
                "max_tokens": 100,
                "max_cost_micros": 1_000,
            },
        }
    )
    orchestrator, queue, _handler, keys, _ledger, _stores = runtime(
        provider_decisions=StaticProviderDecisions(decision)
    )

    started = orchestrator.start(request(two_node_plan))

    assert started.status == "running"
    commands = {
        command.node.node_id: command for command in queue.submissions
    }
    assert set(commands) == {"small", "large"}
    for node_id, node_tokens, node_cost in (
        ("small", 30, 300),
        ("large", 50, 500),
    ):
        command = commands[node_id]
        assert command.authorization.budgets["tokens"] == node_tokens
        assert command.authorization.budgets["cost_micros"] == node_cost
        assert command.authorization.budgets["provider_run_tokens"] == 100
        assert (
            command.authorization.budgets["provider_run_cost_micros"]
            == 1_000
        )
        assert command.provider_context["max_total_tokens"] == node_tokens
        assert command.provider_context["max_cost_micros"] == node_cost

    original = commands["small"]
    assert original.node.budget is not None
    tampered = replace(
        original,
        node=replace(
            original.node,
            budget=replace(
                original.node.budget,
                max_tokens=80,
            ),
        ),
    )
    tamper_handler = DeterministicHandler()
    tamper_runtime = NativeDelegatedNodeRuntime(
        handler=tamper_handler,
        authorization_verifier=AuthorizationVerifier(
            keys,
            InMemoryReplayNonceStore(clock=lambda: 100.0),
        ),
        policy=AllowPolicy(),
        capabilities=frozenset({"bounded_parallel"}),
        hub_revalidator=HubRevalidator(),
        clock=lambda: 100.0,
    )

    denied = tamper_runtime.execute(
        tampered,
        hub_task_id="hub-task-tampered",
    )

    assert denied.status == "failed"
    assert denied.reason_code == "authorization_budget_exceeded"
    assert tamper_handler.calls == []


def test_native_command_plan_controls_propose_strategy_despite_worker_drift(
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
    from agent.services.propose_strategies.flexible_llm_normalization_strategy import (
        FlexibleLLMNormalizationStrategy,
    )
    from worker.core.propose_orchestrator import ProposeContext
    from worker.runtime.native_graph.composition import (
        NativeTaskScopedNodeHandler,
    )

    decisions = HubConfiguredWorkflowProviderDecisionService(
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
    orchestrator, queue, _handler, _keys, _ledger, _stores = runtime(
        provider_decisions=decisions
    )
    started = orchestrator.start(request(provider_plan()))
    assert started.status == "running"
    command = queue.submissions[0]

    native_handler = NativeTaskScopedNodeHandler(
        agent_config={},
        task_snapshots=SimpleNamespace(),
        executor=SimpleNamespace(),
    )
    effective_config = native_handler._hub_bound_agent_config(command)
    assert effective_config["provider_attempt_plan"] == [
        entry.to_dict() for entry in command.provider_attempt_plan
    ]

    # The Worker has the opposite order and deliberately incorrect retry
    # budgets. Only the Hub-signed plan transported by the Native command may
    # control the strategy's ModelInvocation calls.
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
        del cls, messages
        attempt = values["attempt"]
        provider_context = values["provider_context"]
        calls.append(
            (
                attempt["profile"].profile_id,
                provider_context.provider_profile_id,
            )
        )
        if len(calls) < 5:
            raise LLMUnavailableError(
                "simulated timeout",
                terminal_reason="provider_timeout",
            )
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"command":"echo native-plan-ok"}'
                    }
                }
            ],
            "metadata": {},
            "model": attempt["model"],
            "usage": {},
        }

    monkeypatch.setattr(
        ModelInvocationService,
        "_make_single_chat_call",
        classmethod(invoke_once),
    )
    context = ProposeContext(
        goal_id="goal-native",
        task_id="task-native",
        task={"id": "task-native", "goal_id": "goal-native"},
        base_prompt="return one command",
        effective_config=effective_config,
    )

    result = FlexibleLLMNormalizationStrategy().run(context)

    expected_profiles = (
        ["local_ollama_phi4_mini"] * 3
        + ["local_ollama_gemma4_e4b_reasoning"] * 2
    )
    assert result.proposal is not None
    assert result.proposal.command == "echo native-plan-ok"
    assert [profile for profile, _context_profile in calls] == (
        expected_profiles
    )
    assert [
        context_profile for _profile, context_profile in calls
    ] == expected_profiles


def signed_control(
    *,
    keys: HmacKeyRing,
    checkpoint: SignedCheckpoint,
    command_type: str,
    step_id: str,
    command_id: str,
    payload: dict | None = None,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=keys,
        command_type=command_type,
        tenant_id=checkpoint.tenant_id,
        workflow_id=checkpoint.workflow_id,
        run_id=checkpoint.run_id,
        step_id=step_id,
        checkpoint_id=checkpoint.checkpoint_id,
        expected_revision=checkpoint.revision,
        plan_hash=checkpoint.plan_hash,
        policy_version=checkpoint.policy_version,
        actor_id="operator-1",
        actor_roles=("operator",),
        payload=payload,
        now=100,
        command_id=command_id,
        nonce=f"nonce-{command_id}",
    )


def test_native_graph_delegates_each_task_and_completes_from_signed_checkpoints() -> None:
    orchestrator, queue, handler, keys, _, _ = runtime()
    req = request(plan())

    first = orchestrator.start(req)
    second = orchestrator.advance(req)
    completed = orchestrator.advance(req)

    assert first.status == second.status == "running"
    assert completed.status == "completed"
    assert handler.calls == ["draft", "analyze"]
    assert [item.node.node_id for item in queue.submissions] == ["draft", "analyze"]
    assert completed.checkpoint.revision == 3
    completed.checkpoint.verify(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="native-test",
        run_id="run-1",
        task_id="control-run-1",
        plan_hash=req.plan.plan_hash,
        policy_version="policy-v1",
    )
    assert {event.event_type for event in orchestrator.stream(req)} >= {
        "workflow.run.started",
        "workflow.step.delegated",
        "workflow.step.completed",
        "workflow.run.completed",
        "workflow.checkpoint.created",
    }


def test_approval_is_signed_bound_replay_safe_and_write_uses_hub_ledger() -> None:
    orchestrator, _, _, keys, ledger, _ = runtime()
    value = plan(approval=True)
    req = request(value)
    orchestrator.start(req)
    waiting = orchestrator.advance(req)
    assert waiting.status == "waiting_for_approval"
    assert waiting.open_gates == ("publish-gate",)

    command = SignedWorkflowCommand.issue(
        key_ring=keys,
        command_type="approve",
        tenant_id="tenant-a",
        workflow_id="native-test",
        run_id="run-1",
        step_id="publish",
        checkpoint_id=waiting.checkpoint.checkpoint_id,
        expected_revision=waiting.checkpoint.revision,
        plan_hash=value.plan_hash,
        policy_version="policy-v1",
        actor_id="operator-1",
        actor_roles=("operator",),
        now=100,
        nonce="approval-once",
    )
    resumed = orchestrator.resume(req, command=command)
    completed = orchestrator.advance(req)

    assert resumed.status == "running"
    assert completed.status == "completed"
    operation_id = next(iter(ledger._records))  # noqa: SLF001 - reference ledger assertion
    assert ledger.get(tenant_id="tenant-a", operation_id=operation_id).status == "completed"
    assert {event.event_type for event in orchestrator.stream(req)} >= {
        "workflow.approval.requested",
        "workflow.approval.granted",
        "workflow.side_effect.completed",
    }
    with pytest.raises(Exception, match="replay"):
        orchestrator.resume(req, command=command, checkpoint=waiting.checkpoint)


def test_tampered_or_cross_task_command_and_checkpoint_fail_closed() -> None:
    orchestrator, _, _, keys, _, _ = runtime()
    value = plan(approval=True)
    req = request(value)
    orchestrator.start(req)
    waiting = orchestrator.advance(req)
    command = SignedWorkflowCommand.issue(
        key_ring=keys,
        command_type="approve",
        tenant_id="tenant-a",
        workflow_id="native-test",
        run_id="run-1",
        step_id="publish",
        checkpoint_id=waiting.checkpoint.checkpoint_id,
        expected_revision=waiting.checkpoint.revision,
        plan_hash=value.plan_hash,
        policy_version="policy-v1",
        actor_id="operator-1",
        actor_roles=("operator",),
        now=100,
    )

    with pytest.raises(Exception, match="signature_invalid"):
        orchestrator.resume(req, command=replace(command, actor_roles=("admin",)))
    with pytest.raises(Exception, match="run_id_mismatch"):
        orchestrator.resume(request(value, run_id="run-2"), command=command, checkpoint=waiting.checkpoint)
    cross_runtime = SignedCheckpoint.issue(
        key_ring=keys,
        tenant_id=waiting.checkpoint.tenant_id,
        workflow_id=waiting.checkpoint.workflow_id,
        run_id=waiting.checkpoint.run_id,
        task_id=waiting.checkpoint.task_id,
        plan_hash=waiting.checkpoint.plan_hash,
        policy_version=waiting.checkpoint.policy_version,
        runtime_id="langgraph",
        runtime_version=waiting.checkpoint.runtime_version,
        state=waiting.checkpoint.state,
        revision=waiting.checkpoint.revision,
        fencing_token=waiting.checkpoint.fencing_token,
        now=100,
    )
    with pytest.raises(ValueError, match="cross_runtime"):
        orchestrator.resume(req, command=command, checkpoint=cross_runtime)


def test_reject_pause_and_resume_use_the_same_signed_revision_bound_path() -> None:
    reject_orchestrator, _, _, reject_keys, _, _ = runtime()
    approval_plan = plan(approval=True)
    approval_request = request(approval_plan)
    reject_orchestrator.start(approval_request)
    waiting = reject_orchestrator.advance(approval_request)
    rejected = reject_orchestrator.resume(
        approval_request,
        command=signed_control(
            keys=reject_keys,
            checkpoint=waiting.checkpoint,
            command_type="reject",
            step_id="publish",
            command_id="reject-1",
        ),
    )
    assert rejected.status == "failed"
    assert "workflow.approval.rejected" in {event.event_type for event in reject_orchestrator.stream(approval_request)}

    orchestrator, _, _, keys, _, _ = runtime()
    req = request(plan())
    running = orchestrator.start(req)
    paused = orchestrator.resume(
        req,
        command=signed_control(
            keys=keys,
            checkpoint=running.checkpoint,
            command_type="pause",
            step_id="__workflow__",
            command_id="pause-1",
        ),
    )
    assert paused.status == "paused"
    resumed = orchestrator.resume(
        req,
        command=signed_control(
            keys=keys,
            checkpoint=paused.checkpoint,
            command_type="resume",
            step_id="__workflow__",
            command_id="resume-1",
        ),
    )
    assert resumed.status in {"running", "completed"}


def test_native_edit_and_request_changes_persist_the_effective_plan() -> None:
    orchestrator, _, _, keys, _, _ = runtime()
    original = plan(approval=True)
    req = request(original)
    orchestrator.start(req)
    waiting = orchestrator.advance(req)
    first_replacement = replace(
        original,
        nodes=(
            original.nodes[0],
            replace(
                original.nodes[1],
                metadata={**original.nodes[1].metadata, "label": "Reviewed publish"},
            ),
        ),
    )
    edited = orchestrator.resume(
        req,
        command=signed_control(
            keys=keys,
            checkpoint=waiting.checkpoint,
            command_type="edit",
            step_id="publish",
            command_id="edit-1",
            payload={
                "replacement_plan": first_replacement.to_dict(),
                "replacement_plan_hash": first_replacement.plan_hash,
            },
        ),
    )
    assert edited.effective_plan is not None
    assert edited.effective_plan.plan_hash == first_replacement.plan_hash
    assert orchestrator.checkpoint(req).plan_hash == first_replacement.plan_hash

    second_replacement = replace(
        first_replacement,
        nodes=(
            first_replacement.nodes[0],
            replace(
                first_replacement.nodes[1],
                metadata={
                    **first_replacement.nodes[1].metadata,
                    "label": "Changes requested",
                },
            ),
        ),
    )
    changes_requested = orchestrator.resume(
        req,
        command=signed_control(
            keys=keys,
            checkpoint=edited.checkpoint,
            command_type="request_changes",
            step_id="publish",
            command_id="request-changes-1",
            payload={
                "replacement_plan": second_replacement.to_dict(),
                "replacement_plan_hash": second_replacement.plan_hash,
            },
        ),
    )
    assert changes_requested.status == "paused"
    assert changes_requested.effective_plan is not None
    assert changes_requested.effective_plan.plan_hash == second_replacement.plan_hash
    assert orchestrator.checkpoint(req).plan_hash == second_replacement.plan_hash


def test_bounded_parallel_fanout_and_merge_are_hub_deterministic() -> None:
    orchestrator, queue, _, _, _, _ = runtime()
    req = request(plan(parallel=True))

    first = orchestrator.start(req)
    completed = orchestrator.advance(req)

    assert first.status == "running"
    assert [item.node.node_id for item in queue.submissions] == ["a", "b"]
    assert completed.status == "completed"
    assert completed.completed_node_ids == ("a", "b", "merge")
    assert completed.artifact_refs["merged"].startswith("artifact://native/")


def test_retry_is_bounded_and_new_attempt_gets_higher_fencing_token() -> None:
    orchestrator, queue, handler, _, _, _ = runtime(fail_once={"draft"})
    req = request(plan(retry=True))

    orchestrator.start(req)
    orchestrator.advance(req)
    orchestrator.advance(req)
    completed = orchestrator.advance(req)

    draft_commands = [item for item in queue.submissions if item.node.node_id == "draft"]
    assert handler.calls[:2] == ["draft", "draft"]
    assert [item.fencing_token for item in draft_commands] == [1, 2]
    assert completed.status == "completed"


def test_hub_restart_resumes_from_persisted_state_without_worker_orchestration() -> None:
    orchestrator, queue, _, keys, ledger, stores = runtime()
    req = request(plan())
    orchestrator.start(req)
    restarted = NativeGraphOrchestrator(
        queue=queue,
        checkpoints=stores["checkpoints"],
        events=stores["events"],
        ownership=stores["ownership"],
        ledger=ledger,
        key_ring=keys,
        command_verifier=WorkflowCommandVerifier(keys, InMemoryReplayNonceStore(clock=lambda: 100.0)),
        policy=AllowPolicy(),
        clock=lambda: 100.0,
    )

    restarted.advance(req)
    completed = restarted.advance(req)

    assert completed.status == "completed"
    assert len(queue.submissions) == 2


def test_native_resume_consumes_shared_n_minus_one_contract_fixture() -> None:
    n_minus_one_runtime_contracts = n_minus_one_runtime_contract_fixture()
    legacy_plan = ExecutionPlan.from_mapping(n_minus_one_runtime_contracts["plan"])
    orchestrator, queue, _, keys, _, _ = runtime()
    req = request(legacy_plan, run_id="shared-n-minus-one-run")

    waiting = orchestrator.start(req)
    assert waiting.status == "waiting_for_approval"
    resumed = orchestrator.resume(
        req,
        command=signed_control(
            keys=keys,
            checkpoint=waiting.checkpoint,
            command_type="approve",
            step_id="step-1",
            command_id="approve-shared-n-minus-one",
        ),
    )
    completed = orchestrator.advance(req)

    assert legacy_plan.schema == "ananta.execution_plan.v1"
    assert resumed.status == "running"
    assert completed.status == "completed"
    assert [submission.node.node_id for submission in queue.submissions] == ["step-1"]


def test_worker_replacement_resumes_from_hub_checkpoint_independently_of_hub_restart() -> None:
    orchestrator, queue, first_handler, keys, ledger, _ = runtime()
    req = request(plan(), run_id="worker-replacement-run")
    first = orchestrator.start(req)
    assert first.status == "running"
    assert first_handler.calls == ["draft"]

    replacement_handler = DeterministicHandler()
    queue.runtime = NativeDelegatedNodeRuntime(
        handler=replacement_handler,
        authorization_verifier=AuthorizationVerifier(
            keys,
            InMemoryReplayNonceStore(clock=lambda: 100.0),
        ),
        policy=AllowPolicy(),
        capabilities=frozenset(
            {
                "approval",
                "bounded_parallel",
                "deterministic_merge",
                "retrieval",
                "structured_output",
                "tool_calling",
            }
        ),
        ledger=ledger,
        hub_revalidator=HubRevalidator(),
        clock=lambda: 100.0,
    )

    orchestrator.advance(req)
    completed = orchestrator.advance(req)

    assert completed.status == "completed"
    assert replacement_handler.calls == ["analyze"]
    assert completed.checkpoint.revision == 3
