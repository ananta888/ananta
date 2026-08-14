from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from inspect import Parameter, signature
from typing import Any

import pytest

from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    FinalizationObserved,
    FinalizationQuarantine,
    FinalizationRetry,
    RetryAt,
    RetryExhausted,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectExecutionError,
    WorkflowTransitionEffectExecutorRegistry,
    WorkflowTransitionEffectHandler,
    WorkflowTransitionEffectObservation,
    WorkflowTransitionEffectRegistration,
    WorkflowTransitionFinalizationAttempt,
    WorkflowTransitionFinalizationObserverRegistry,
    WorkflowTransitionFinalizationRegistration,
    workflow_transition_effect_result_envelope,
    workflow_transition_effect_stage_attempt_count,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_CHECKPOINT_SAVE,
    EFFECT_QUEUE_RESERVE,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    WorkflowTransition,
    WorkflowTransitionEffect,
    WorkflowTransitionSnapshot,
    workflow_transition_effect_result_digest,
    workflow_transition_id,
)

_CREATED_AT = 1_000.0


class _Heartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def heartbeat(self) -> None:
        self.calls += 1


class _Observation:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[WorkflowTransitionEffectObservation] = []

    def observe_or_adopt(
        self,
        observation: WorkflowTransitionEffectObservation,
        *,
        heartbeat: _Heartbeat,
    ) -> Any:
        self.calls.append(observation)
        heartbeat.heartbeat()
        return self.result


class _Execution:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[WorkflowTransitionEffectAttempt, EffectExecutable]] = []

    def execute(
        self,
        attempt: WorkflowTransitionEffectAttempt,
        *,
        executable: EffectExecutable,
        heartbeat: _Heartbeat,
    ) -> Any:
        self.calls.append((attempt, executable))
        heartbeat.heartbeat()
        return self.result


class _FinalizationObservation:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[WorkflowTransitionFinalizationAttempt] = []

    def observe(
        self,
        attempt: WorkflowTransitionFinalizationAttempt,
        *,
        heartbeat: _Heartbeat,
    ) -> Any:
        self.calls.append(attempt)
        heartbeat.heartbeat()
        return self.result


def _plan() -> tuple[WorkflowTransition, tuple[WorkflowTransitionEffect, ...]]:
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        identity_key="command-a",
    )
    effects = (
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=1,
            kind=EFFECT_QUEUE_RESERVE,
            idempotency_key="queue:command-a",
            payload={"task_id": "task-a"},
            created_at=_CREATED_AT,
        ),
        WorkflowTransitionEffect.build(
            transition_id=transition_id,
            ordinal=2,
            kind=EFFECT_BINDING_FINALIZE,
            idempotency_key="binding:workflow-a",
            payload={"workflow_id": "workflow-a"},
            created_at=_CREATED_AT,
        ),
    )
    transition = WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        kind=TRANSITION_KIND_COMMAND,
        command_id="command-a",
        receipt_id="command-a",
        admitted_command={"command_id": "command-a", "kind": "advance"},
        request_payload={"command": "advance"},
        effects=effects,
        expected_revision=7,
        expected_checkpoint_ref="checkpoint-7",
        created_at=_CREATED_AT,
    )
    return transition, effects


def _applying_transition(transition: WorkflowTransition, *, generation: int = 2) -> WorkflowTransition:
    return replace(
        transition,
        state=TRANSITION_STATE_APPLYING,
        claim_owner="runner-a",
        claim_generation=generation,
        claim_expires_at=1_100.0,
        last_heartbeat_at=1_001.0,
        attempt_count=generation,
        revision=transition.revision + 1,
        updated_at=1_001.0,
    )


def _effect_attempt() -> WorkflowTransitionEffectAttempt:
    transition, effects = _plan()
    applying_effect = replace(
        effects[0],
        state=EFFECT_STATE_APPLYING,
        applied_generation=2,
        revision=effects[0].revision + 1,
        updated_at=1_001.0,
    )
    return WorkflowTransitionEffectAttempt(
        transition=_applying_transition(transition),
        effect=applying_effect,
        claim_generation=2,
    )


def _effect_observation(*, applying_generation: int = 0) -> WorkflowTransitionEffectObservation:
    transition, effects = _plan()
    effect = effects[0]
    if applying_generation:
        effect = replace(
            effect,
            state=EFFECT_STATE_APPLYING,
            applied_generation=applying_generation,
            revision=effect.revision + 1,
            updated_at=1_001.0,
        )
    return WorkflowTransitionEffectObservation(
        transition=_applying_transition(transition),
        effect=effect,
        claim_generation=2,
    )


def _finalization_attempt() -> WorkflowTransitionFinalizationAttempt:
    transition, effects = _plan()
    result = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload={"queue_state": "reserved", "task_id": "task-a"},
        proof_payload={"downstream_revision": 1},
        stage_attempt_count=1,
    )
    applied_effect = replace(
        effects[0],
        state=EFFECT_STATE_APPLIED,
        applied_generation=2,
        result_payload=result,
        result_digest=workflow_transition_effect_result_digest(result),
        revision=effects[0].revision + 1,
        updated_at=1_001.0,
    )
    snapshot = WorkflowTransitionSnapshot(
        _applying_transition(transition),
        (applied_effect, effects[1]),
    )
    return WorkflowTransitionFinalizationAttempt(snapshot=snapshot, claim_generation=2)


def _handler(*, observation_result: Any | None = None) -> WorkflowTransitionEffectHandler:
    return WorkflowTransitionEffectHandler(
        observation=_Observation(observation_result or EffectExecutable({"absence_revision": 3})),
        execution=_Execution(
            EffectApplied(
                {"task_id": "task-a"},
                {"downstream_revision": 4},
            )
        ),
    )


def test_effect_results_are_closed_bounded_and_deeply_immutable() -> None:
    result = {"items": [{"id": "task-a"}]}
    proof = {"ledger_revision": 4, "observations": ["absent"]}
    already_applied = EffectAlreadyApplied(result, proof)
    executable = EffectExecutable(proof)
    applied = EffectApplied(result, {"downstream_revision": 4})

    result["items"][0]["id"] = "mutated"  # type: ignore[index]
    proof["observations"].append("mutated")  # type: ignore[union-attr]

    assert already_applied.result_payload["items"][0]["id"] == "task-a"
    assert already_applied.proof_payload["observations"] == ("absent",)
    assert executable.proof_payload["observations"] == ("absent",)
    assert applied.result_payload["items"][0]["id"] == "task-a"
    with pytest.raises(TypeError):
        applied.result_payload["task_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        applied.result_payload = {"task_id": "other"}  # type: ignore[misc]

    for candidate in (
        lambda: EffectAlreadyApplied({}, {"ledger_revision": 1}),
        lambda: EffectAlreadyApplied({"task_id": "task-a"}, None),
        lambda: EffectExecutable({}),
        lambda: EffectApplied({"value": float("nan")}, {"proof": "exact"}),
        lambda: EffectApplied({"task_id": "task-a"}, {}),
        lambda: EffectExecutable({"value": "x" * 524_289}),
        lambda: EffectRetry("INVALID-REASON"),
        lambda: EffectQuarantine(""),
    ):
        with pytest.raises(WorkflowTransitionEffectExecutionError):
            candidate()

    assert {field.name for field in fields(EffectApplied)} == {
        "proof_payload",
        "result_payload",
    }
    assert {field.name for field in fields(EffectAlreadyApplied)} == {
        "proof_payload",
        "result_payload",
    }
    assert all("digest" not in field.name and "outcome" not in field.name for field in fields(EffectApplied))


def test_effect_result_envelope_is_versioned_combined_bounded_and_exact() -> None:
    envelope = workflow_transition_effect_result_envelope(
        mode="execute",
        result_payload={"task_id": "task-a"},
        proof_payload={"downstream_revision": 4},
        stage_attempt_count=2,
    )

    assert envelope == {
        "schema": "ananta.workflow-transition-effect-result.v1",
        "mode": "execute",
        "effect_result": {"task_id": "task-a"},
        "effect_proof": {"downstream_revision": 4},
        "stage_attempt_count": 2,
    }
    assert workflow_transition_effect_stage_attempt_count(envelope) == 2
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="envelope_invalid"):
        workflow_transition_effect_stage_attempt_count({**envelope, "schema": "unsupported"})
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="stage_attempt_invalid"):
        workflow_transition_effect_result_envelope(
            mode="execute",
            result_payload={"task_id": "task-a"},
            proof_payload={"downstream_revision": 4},
            stage_attempt_count=0,
        )
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="envelope_too_large"):
        EffectApplied(
            {"result": "x" * 300_000},
            {"proof": "y" * 300_000},
        )


def test_effect_attempt_requires_a_begun_current_generation_nonfinal_effect() -> None:
    attempt = _effect_attempt()
    assert attempt.effect.applied_generation == attempt.claim_generation == 2

    transition, effects = _plan()
    applying_transition = _applying_transition(transition)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="effect_attempt_invalid"):
        WorkflowTransitionEffectAttempt(applying_transition, effects[0], 2)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="effect_attempt_invalid"):
        WorkflowTransitionEffectAttempt(attempt.transition, attempt.effect, 1)

    applying_final = replace(
        effects[-1],
        state=EFFECT_STATE_APPLYING,
        applied_generation=2,
        revision=2,
        updated_at=1_001.0,
    )
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="effect_attempt_invalid"):
        WorkflowTransitionEffectAttempt(applying_transition, applying_final, 2)


def test_effect_observation_requires_pre_begin_or_an_older_generation() -> None:
    planned = _effect_observation()
    adopted = _effect_observation(applying_generation=1)

    assert planned.effect.state != EFFECT_STATE_APPLYING
    assert planned.effect.applied_generation == 0
    assert adopted.effect.state == EFFECT_STATE_APPLYING
    assert adopted.effect.applied_generation == 1 < adopted.claim_generation

    attempt = _effect_attempt()
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="effect_observation_invalid"):
        WorkflowTransitionEffectObservation(
            attempt.transition,
            attempt.effect,
            attempt.claim_generation,
        )
    transition, effects = _plan()
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="effect_observation_invalid"):
        WorkflowTransitionEffectObservation(
            _applying_transition(transition),
            effects[-1],
            2,
        )


def test_observe_or_adopt_is_mandatory_before_the_separate_execution_port() -> None:
    observation_context = _effect_observation()
    attempt = _effect_attempt()
    executable = EffectExecutable({"authoritative_absence_revision": 8})
    observation = _Observation(executable)
    execution = _Execution(
        EffectApplied(
            {"queue_state": "reserved"},
            {"downstream_revision": 4},
        )
    )
    handler = WorkflowTransitionEffectHandler(observation, execution)
    heartbeat = _Heartbeat()

    observed = handler.observation.observe_or_adopt(observation_context, heartbeat=heartbeat)
    assert observed is executable
    assert isinstance(observed, EffectExecutable)
    executed = handler.execution.execute(
        attempt,
        executable=observed,
        heartbeat=heartbeat,
    )

    assert isinstance(executed, EffectApplied)
    assert observation.calls == [observation_context]
    assert execution.calls == [(attempt, executable)]
    assert heartbeat.calls == 2
    executable_parameter = signature(handler.execution.execute).parameters["executable"]
    assert executable_parameter.default is Parameter.empty


def test_effect_executor_registry_is_immutable_exact_and_fail_closed() -> None:
    handler = _handler()
    registration = WorkflowTransitionEffectRegistration(
        TRANSITION_RUNTIME_NATIVE,
        EFFECT_QUEUE_RESERVE,
        handler,
    )
    registry = WorkflowTransitionEffectExecutorRegistry((registration,))

    assert (
        registry.resolve(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            effect_kind=EFFECT_QUEUE_RESERVE,
        )
        is handler
    )
    for runtime_id, effect_kind, reason in (
        (TRANSITION_RUNTIME_LANGGRAPH, EFFECT_QUEUE_RESERVE, "executor_missing"),
        (TRANSITION_RUNTIME_NATIVE, EFFECT_CHECKPOINT_SAVE, "executor_missing"),
        (TRANSITION_RUNTIME_NATIVE, EFFECT_BINDING_FINALIZE, "finalize_forbidden"),
    ):
        with pytest.raises(WorkflowTransitionEffectExecutionError, match=reason):
            registry.resolve(runtime_id=runtime_id, effect_kind=effect_kind)

    with pytest.raises(WorkflowTransitionEffectExecutionError, match="registration_duplicate"):
        WorkflowTransitionEffectExecutorRegistry((registration, registration))
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="finalize_forbidden"):
        WorkflowTransitionEffectRegistration(
            TRANSITION_RUNTIME_NATIVE,
            EFFECT_BINDING_FINALIZE,
            handler,
        )
    with pytest.raises(FrozenInstanceError):
        registry._handlers = {}  # type: ignore[misc]  # noqa: SLF001
    with pytest.raises(TypeError):
        registry._handlers[(TRANSITION_RUNTIME_NATIVE, EFFECT_CHECKPOINT_SAVE)] = handler  # type: ignore[index]  # noqa: SLF001


def test_finalization_observation_is_raw_read_only_and_exactly_registered() -> None:
    attempt = _finalization_attempt()
    raw_status = {
        "checkpoint_ref": "checkpoint-8",
        "revision": 8,
        "status": "running",
    }
    proof = {"observation_revision": 8, "sources": ["runtime"]}
    observed = FinalizationObserved(raw_status, "checkpoint-8", proof)
    observation = _FinalizationObservation(observed)
    registration = WorkflowTransitionFinalizationRegistration(
        TRANSITION_RUNTIME_NATIVE,
        TRANSITION_KIND_COMMAND,
        observation,
    )
    registry = WorkflowTransitionFinalizationObserverRegistry((registration,))
    heartbeat = _Heartbeat()

    resolved = registry.resolve(
        runtime_id=TRANSITION_RUNTIME_NATIVE,
        transition_kind=TRANSITION_KIND_COMMAND,
    )
    assert resolved.observe(attempt, heartbeat=heartbeat) == observed
    assert heartbeat.calls == 1
    raw_status["status"] = "mutated"
    proof["sources"].append("mutated")
    assert observed.binding_status["status"] == "running"
    assert observed.proof_payload["sources"] == ("runtime",)
    assert {field.name for field in fields(FinalizationObserved)} == {
        "binding_status",
        "checkpoint_ref",
        "proof_payload",
    }

    with pytest.raises(WorkflowTransitionEffectExecutionError, match="evidence_invalid"):
        FinalizationObserved(
            {"checkpoint_ref": "checkpoint-9", "revision": 8},
            "checkpoint-8",
            {"observation_revision": 8},
        )
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="evidence_invalid"):
        FinalizationObserved(
            {"checkpoint_ref": "checkpoint-8", "revision": True},
            "checkpoint-8",
            {"observation_revision": 8},
        )
    for invalid_proof in ({}, {"proof": "x" * 524_289}, {"proof": float("nan")}):
        with pytest.raises(
            WorkflowTransitionEffectExecutionError,
            match="finalization_proof",
        ):
            FinalizationObserved(raw_status, "checkpoint-8", invalid_proof)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="observer_missing"):
        registry.resolve(
            runtime_id=TRANSITION_RUNTIME_LANGGRAPH,
            transition_kind=TRANSITION_KIND_COMMAND,
        )
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="observer_missing"):
        registry.resolve(
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            transition_kind=TRANSITION_KIND_ADVANCE,
        )
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="registration_duplicate"):
        WorkflowTransitionFinalizationObserverRegistry((registration, registration))

    assert FinalizationRetry("runtime_observation_pending").reason_code == "runtime_observation_pending"
    assert FinalizationQuarantine("runtime_evidence_conflict").reason_code == "runtime_evidence_conflict"


def test_finalization_attempt_requires_all_nonfinal_effects_applied_under_the_lease() -> None:
    valid = _finalization_attempt()
    assert valid.snapshot.effects[-1].kind == EFFECT_BINDING_FINALIZE

    transition, effects = _plan()
    incomplete = WorkflowTransitionSnapshot(_applying_transition(transition), effects)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="finalization_attempt_invalid"):
        WorkflowTransitionFinalizationAttempt(incomplete, 2)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="finalization_attempt_invalid"):
        WorkflowTransitionFinalizationAttempt(valid.snapshot, 1)
    applying_final = replace(
        valid.snapshot.effects[-1],
        state=EFFECT_STATE_APPLYING,
        applied_generation=2,
        revision=valid.snapshot.effects[-1].revision + 1,
    )
    corrupt = WorkflowTransitionSnapshot(
        valid.snapshot.transition,
        (*valid.snapshot.effects[:-1], applying_final),
    )
    with pytest.raises(
        WorkflowTransitionEffectExecutionError,
        match="finalization_attempt_invalid",
    ):
        WorkflowTransitionFinalizationAttempt(corrupt, 2)


def test_retry_policy_is_pure_bounded_and_returns_retry_at_or_exhausted() -> None:
    policy = BoundedWorkflowTransitionRetryPolicy(
        maximum_attempts=3,
        initial_delay_seconds=2.0,
        multiplier=2.0,
        maximum_delay_seconds=5.0,
    )

    first = policy.next_retry(attempt_count=1, decision_at=100.0)
    second = policy.next_retry(attempt_count=2, decision_at=100.0)
    exhausted = policy.next_retry(attempt_count=3, decision_at=100.0)

    assert first == RetryAt(102.0)
    assert second == RetryAt(104.0)
    assert exhausted == RetryExhausted()
    assert policy.authorize_attempt(attempt_count=1) is True
    assert policy.authorize_attempt(attempt_count=3) is True
    assert policy.authorize_attempt(attempt_count=4) is False
    assert policy.next_retry(attempt_count=2, decision_at=100.0) == second

    capped = BoundedWorkflowTransitionRetryPolicy(5, 2.0, 10.0, 5.0)
    assert capped.next_retry(attempt_count=2, decision_at=100.0) == RetryAt(105.0)

    invalid_policies = (
        (0, 1.0, 2.0, 3.0),
        (True, 1.0, 2.0, 3.0),
        (1_001, 1.0, 2.0, 3.0),
        (3, 0.0, 2.0, 3.0),
        (3, 1.0, 0.5, 3.0),
        (3, 2.0, 2.0, 1.0),
        (3, 1.0, 2.0, float("inf")),
    )
    for values in invalid_policies:
        with pytest.raises(WorkflowTransitionEffectExecutionError, match="retry_policy_invalid"):
            BoundedWorkflowTransitionRetryPolicy(*values)

    with pytest.raises(WorkflowTransitionEffectExecutionError, match="retry_attempt_invalid"):
        policy.next_retry(attempt_count=0, decision_at=100.0)
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="retry_decision_at_invalid"):
        policy.next_retry(attempt_count=1, decision_at=float("nan"))
    with pytest.raises(WorkflowTransitionEffectExecutionError, match="retry_at_invalid"):
        RetryAt(-1.0)


def test_public_contract_configuration_has_no_implicit_defaults() -> None:
    required_parameters = (
        (EffectAlreadyApplied, ("result_payload", "proof_payload")),
        (WorkflowTransitionEffectExecutorRegistry, ("registrations",)),
        (WorkflowTransitionFinalizationObserverRegistry, ("registrations",)),
        (
            BoundedWorkflowTransitionRetryPolicy,
            (
                "maximum_attempts",
                "initial_delay_seconds",
                "multiplier",
                "maximum_delay_seconds",
            ),
        ),
    )
    for target, names in required_parameters:
        parameters = signature(target).parameters
        assert tuple(parameters) == names
        assert all(parameters[name].default is Parameter.empty for name in names)
