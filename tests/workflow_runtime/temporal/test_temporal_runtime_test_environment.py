from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncIterator

import pytest

pytest.importorskip("temporalio")

from temporalio.client import WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from agent.services.temporal_history_projection import (
    InMemoryTemporalProjectionRepository,
    TemporalHistoryProjectionService,
    TemporalSDKHistorySource,
)
from agent.services.workflow_runtime import (
    ExecutionPlan,
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
    SignedWorkflowCommand,
    WorkflowState,
)
from ananta_contracts.hub_task_gateway import RetryBudgetReceipt
from ananta_contracts.temporal_workflow import (
    ActivityClass,
    AnantaWorkflowInput,
    ArtifactReference,
    AuthorizationEnvelopeRef,
    ProbeRequest,
    TemporalWorkflowStep,
    WorkflowCommand,
    WorkflowCommandType,
)
from ananta_contracts.workflow_operation import operation_id_for
from tests.workflow_runtime_contract_fixtures import (
    n_minus_one_runtime_contract_fixture,
)
from worker.temporal.activities import HubActivityGateway, probe_activity
from worker.temporal.authorization import RuntimeAuthorizationVerifier
from worker.temporal.hub_gateway import HubGatewayError, HubTaskReceipt
from worker.temporal.legacy_replay_workflows import LegacyV0AnantaWorkflow
from worker.temporal.workflows import (
    AnantaWorkflow,
    TemporalProbeWorkflow,
    TemporalRecoveryProbeWorkflow,
)

SIGNING_KEY = "temporal-test-signing-key-32-bytes"
KEY_ID = "temporal-test-key"
PLAN_HASH = "a" * 64


@dataclass
class ScriptedHubGateway:
    """Deterministic Hub port fake; it never acts as a worker or router."""

    terminal_status: str = "completed"
    fail_submissions: int = 0
    poll_cycles: int = 0
    operation_override: str = ""
    artifact_refs: tuple[dict[str, Any], ...] = ()
    artifacts_by_step: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    status_by_step: dict[str, str] = field(default_factory=dict)
    submission_delay_seconds: float = 0.0
    submissions: int = 0
    polls: int = 0
    cancellations: int = 0
    submitted_operations: list[str] = field(default_factory=list)
    retry_consumptions: list[tuple[str, str]] = field(default_factory=list)
    operation_steps: dict[str, str] = field(default_factory=dict)
    submitted_artifacts_by_step: dict[str, tuple[str, ...]] = field(default_factory=dict)
    active_submissions: int = 0
    max_active_submissions: int = 0

    async def consume_retry(self, request, *, retry_id: str, category: str) -> RetryBudgetReceipt:
        self.retry_consumptions.append((retry_id, category))
        used = len(self.retry_consumptions)
        return RetryBudgetReceipt(
            retry_id=retry_id,
            category=category,
            used=used,
            maximum=int(request.retry_budget_maximum or 0),
            remaining=max(0, int(request.retry_budget_maximum or 0) - used),
        )

    async def submit_authorized_task(self, request) -> HubTaskReceipt:
        self.submissions += 1
        self.submitted_operations.append(request.operation_id)
        self.operation_steps[request.operation_id] = request.step_id
        self.submitted_artifacts_by_step[request.step_id] = tuple(
            artifact.artifact_id for artifact in request.artifact_refs
        )
        self.active_submissions += 1
        self.max_active_submissions = max(self.max_active_submissions, self.active_submissions)
        try:
            if self.submission_delay_seconds:
                await asyncio.sleep(self.submission_delay_seconds)
            if self.submissions <= self.fail_submissions:
                raise HubGatewayError("scripted_hub_unavailable", retryable=True)
            terminal_status = self.status_by_step.get(request.step_id, self.terminal_status)
            status = "running" if self.poll_cycles != 0 else terminal_status
            return self._receipt(request.operation_id, status)
        finally:
            self.active_submissions -= 1

    async def get_task(self, *, hub_task_id: str, operation_id: str) -> HubTaskReceipt:
        del hub_task_id
        self.polls += 1
        if self.poll_cycles < 0 or self.polls <= self.poll_cycles:
            return self._receipt(operation_id, "running")
        return self._receipt(operation_id, self.terminal_status)

    async def request_cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> None:
        del hub_task_id, operation_id, reason
        self.cancellations += 1

    def _receipt(self, operation_id: str, status: str) -> HubTaskReceipt:
        ledger_state = {
            "completed": "completed",
            "failed": "failed",
            "uncertain": "uncertain",
        }.get(status, "started")
        step_id = self.operation_steps.get(operation_id, "")
        return HubTaskReceipt(
            hub_task_id=f"task-{self.submissions or 1}",
            operation_id=self.operation_override or operation_id,
            status=status,
            authorization_state="valid",
            ledger_state=ledger_state,
            artifact_refs=self.artifacts_by_step.get(step_id, self.artifact_refs),
            canonical_event_refs=(f"event-{self.submissions or 1}",),
            reason_code="" if status == "completed" else f"scripted_{status}",
        )


def _authorization(
    *,
    workflow_id: str,
    run_id: str,
    step_id: str,
    issued_at: float | None = None,
    ttl_seconds: float = 600,
    plan_hash: str = PLAN_HASH,
) -> AuthorizationEnvelopeRef:
    key_ring = HmacKeyRing({KEY_ID: SIGNING_KEY}, active_key_id=KEY_ID)
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-1",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        plan_hash=plan_hash,
        policy_version="policy-v1",
        allowed_tools=("read_file",),
        allowed_artifacts=("artifact-input",),
        budgets={"retries": 5},
        ttl_seconds=ttl_seconds,
        now=issued_at,
        envelope_id=f"env-{workflow_id}-{step_id}",
        nonce=f"nonce-{workflow_id}-{step_id}",
    )
    return AuthorizationEnvelopeRef.from_mapping(envelope.to_dict())


def _workflow_input(
    *,
    workflow_id: str,
    step_count: int = 1,
    gate: bool = False,
    activity_class: ActivityClass = ActivityClass.IDEMPOTENT,
    retry_budget: int = 2,
    mutable_parameters: tuple[str, ...] = (),
    parameters: dict[str, Any] | None = None,
    max_history_events: int = 20_000,
    max_state_bytes: int = 512_000,
    step_id_size: int = 0,
    authorization_time: float | None = None,
    authorization_ttl: float = 600,
    plan_hash: str = PLAN_HASH,
) -> AnantaWorkflowInput:
    run_id = "run-1"
    steps: list[TemporalWorkflowStep] = []
    for index in range(step_count):
        base_step_id = f"step-{index + 1}"
        step_id = (
            f"{base_step_id}-{'x' * max(0, step_id_size - len(base_step_id) - 1)}" if step_id_size else base_step_id
        )
        dependency = steps[-1].step_id if steps else ""
        steps.append(
            TemporalWorkflowStep(
                step_id=step_id,
                title=f"Step {index + 1}",
                operation_id=operation_id_for(
                    tenant_id="tenant-1",
                    run_id=run_id,
                    step_id=step_id,
                    declared_operation="hub_task",
                ),
                authorization_envelope=_authorization(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                    issued_at=authorization_time,
                    ttl_seconds=authorization_ttl,
                    plan_hash=plan_hash,
                ),
                depends_on=(dependency,) if dependency else (),
                gate=gate and index == 0,
                activity_class=activity_class,
            )
        )
    return AnantaWorkflowInput(
        tenant_id="tenant-1",
        workflow_id=workflow_id,
        run_id=run_id,
        correlation_id=f"correlation-{workflow_id}",
        plan_hash=plan_hash,
        policy_version="policy-v1",
        steps=tuple(steps),
        retry_budget_remaining=retry_budget,
        mutable_parameters=mutable_parameters,
        parameters=dict(parameters or {}),
        max_history_events=max_history_events,
        max_state_bytes=max_state_bytes,
    )


@asynccontextmanager
async def _running_worker(
    gateway: ScriptedHubGateway,
    *,
    activity_timeout_seconds: float = 30,
    workflow_class=AnantaWorkflow,
) -> AsyncIterator[tuple[WorkflowEnvironment, str]]:
    environment = await WorkflowEnvironment.start_time_skipping()
    task_queue = f"temporal-test-{uuid.uuid4().hex}"
    activity_gateway = HubActivityGateway(
        gateway=gateway,
        authorization_verifier=RuntimeAuthorizationVerifier(
            keys={KEY_ID: SIGNING_KEY},
            active_key_id=KEY_ID,
        ),
        poll_seconds=0.05,
        activity_timeout_seconds=activity_timeout_seconds,
    )
    try:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[
                TemporalProbeWorkflow,
                TemporalRecoveryProbeWorkflow,
                workflow_class,
            ],
            activities=[probe_activity, activity_gateway.execute],
            graceful_shutdown_timeout=timedelta(seconds=1),
        ):
            yield environment, task_queue
    finally:
        await environment.shutdown()


async def _wait_for_status(handle, expected: str, *, attempts: int = 200) -> dict[str, Any]:
    for _ in range(attempts):
        status = await handle.query("status")
        if status["status"] == expected:
            return dict(status)
        await asyncio.sleep(0.01)
    raise AssertionError(f"workflow did not reach {expected}")


def _command(
    workflow_input: AnantaWorkflowInput,
    *,
    command_id: str,
    command_type: WorkflowCommandType,
    revision: int,
    payload: dict[str, Any] | None = None,
    authorization: AuthorizationEnvelopeRef | None = None,
) -> dict[str, Any]:
    binding = authorization or workflow_input.steps[0].authorization_envelope
    signed = SignedWorkflowCommand.issue(
        key_ring=HmacKeyRing({KEY_ID: SIGNING_KEY}, active_key_id=KEY_ID),
        command_id=command_id,
        command_type=command_type.value,
        tenant_id=binding.tenant_id,
        workflow_id=binding.workflow_id,
        run_id=binding.run_id,
        step_id=binding.step_id,
        checkpoint_id=f"temporal:{workflow_input.workflow_id}:{revision}",
        expected_revision=revision,
        plan_hash=binding.plan_hash,
        policy_version=binding.policy_version,
        actor_id="operator-1",
        actor_roles=("operator",),
        payload=dict(payload or {}),
        # Bind the command window around the authorization clock captured before
        # workflow start.  A small lower margin avoids wall-clock sub-second
        # races, while one day covers bounded time-skipping between ten critical
        # race repetitions without weakening production command validation.
        now=binding.issued_at - 60,
        ttl_seconds=86_400,
    )
    return WorkflowCommand.from_mapping(signed.to_dict()).to_dict()


def test_probe_happy_path_artifact_and_history_replay() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway(artifact_refs=(ArtifactReference("artifact-output", kind="generated").to_dict(),))
        async with _running_worker(gateway) as (environment, task_queue):
            probe_id = f"probe-{uuid.uuid4().hex}"
            probe_result = await environment.client.execute_workflow(
                "AnantaTemporalProbeWorkflow",
                ProbeRequest(request_id=probe_id),
                id=probe_id,
                task_queue=task_queue,
            )
            assert probe_result["status"] == "ok"

            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(workflow_id=workflow_id)
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            assert result["status"] == "completed"
            assert result["completed_step_ids"] == ["step-1"]
            assert await handle.query("artifact_refs") == ["artifact-output"]
            assert gateway.submissions == 1
            assert gateway.submitted_operations == [workflow_input.steps[0].operation_id]

            history = await handle.fetch_history()
            assert len(history.events) > 5
            replay = await Replayer(workflows=[AnantaWorkflow]).replay_workflow(history)
            assert replay.replay_failure is None

            projection = TemporalHistoryProjectionService(
                namespace="default",
                source=TemporalSDKHistorySource(
                    address="test-environment",
                    namespace="default",
                    client_factory=lambda: environment.client,
                ),
                repository=InMemoryTemporalProjectionRepository(),
            )
            projection.bind_run(
                tenant_id="tenant-1",
                workflow_id=workflow_id,
                run_id="run-1",
                temporal_run_id=str(handle.first_execution_run_id),
                correlation_id=workflow_input.correlation_id,
            )
            projected = await projection.synchronize(
                workflow_id,
                expected_tenant_id="tenant-1",
                page_size=5,
                max_pages=100,
            )
            assert projected["consistency_state"] == "current", projected["reason_code"]
            assert projected["projection_cursor"] == history.events[-1].event_id
            assert all("raw_history_ref" in event["payload"] for event in projected["events"])

    asyncio.run(scenario())


def test_parallel_merge_omits_only_explicitly_allowed_failed_branches() -> None:
    async def scenario() -> None:
        workflow_id = f"wf-partial-{uuid.uuid4().hex}"
        run_id = "run-partial"

        def step(
            step_id: str,
            *,
            depends_on: tuple[str, ...] = (),
            node_type: str = "task",
            merge_strategy: str = "",
            partial_failure: str = "fail",
        ) -> TemporalWorkflowStep:
            return TemporalWorkflowStep(
                step_id=step_id,
                title=step_id,
                operation_id=operation_id_for(
                    tenant_id="tenant-1",
                    run_id=run_id,
                    step_id=step_id,
                    declared_operation="hub_task",
                ),
                authorization_envelope=_authorization(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=step_id,
                ),
                depends_on=depends_on,
                node_type=node_type,
                merge_strategy=merge_strategy,
                partial_failure=partial_failure,
            )

        gateway = ScriptedHubGateway(
            status_by_step={"branch-b": "failed"},
            artifacts_by_step={
                "branch-a": ({"artifact_id": "artifact-a", "kind": "generated"},),
                "merge": ({"artifact_id": "artifact-merged", "kind": "generated"},),
            },
        )
        workflow_input = AnantaWorkflowInput(
            tenant_id="tenant-1",
            workflow_id=workflow_id,
            run_id=run_id,
            correlation_id="correlation-partial",
            plan_hash=PLAN_HASH,
            policy_version="policy-v1",
            steps=(
                step("branch-a"),
                step("branch-b"),
                step(
                    "merge",
                    depends_on=("branch-a", "branch-b"),
                    node_type="merge",
                    merge_strategy="ordered_artifact_refs",
                    partial_failure="omit",
                ),
            ),
            retry_budget_remaining=0,
            max_parallel_steps=2,
            tenant_parallel_limit=2,
            worker_parallel_limit=2,
        )
        async with _running_worker(gateway) as (environment, task_queue):
            result = await environment.client.execute_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )

        assert result["status"] == "completed"
        assert result["failed_step_ids"] == ["branch-b"]
        assert result["completed_step_ids"] == ["branch-a", "merge"]
        assert gateway.submitted_artifacts_by_step["merge"] == ("artifact-a",)

    asyncio.run(scenario())


def test_n_minus_one_v0_history_replays_with_current_workflow() -> None:
    n_minus_one_runtime_contracts = n_minus_one_runtime_contract_fixture()
    async def scenario() -> None:
        migrated_plan = ExecutionPlan.from_mapping(
            n_minus_one_runtime_contracts["plan"]
        )
        migrated_state = WorkflowState.from_mapping(
            n_minus_one_runtime_contracts["state"]
        )
        gateway = ScriptedHubGateway(artifact_refs=(ArtifactReference("legacy-artifact", kind="generated").to_dict(),))
        async with _running_worker(gateway, workflow_class=LegacyV0AnantaWorkflow) as (
            environment,
            task_queue,
        ):
            workflow_id = f"legacy-v0-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                _workflow_input(
                    workflow_id=workflow_id,
                    step_count=len(migrated_plan.nodes),
                    mutable_parameters=tuple(migrated_state.business_data),
                    parameters=migrated_state.business_data,
                    plan_hash=migrated_plan.plan_hash,
                ).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            assert result["status"] == "completed"
            history = await handle.fetch_history()

            replay = await Replayer(workflows=[AnantaWorkflow]).replay_workflow(history)

            assert replay.replay_failure is None
            assert gateway.submissions == 1
            assert migrated_plan.schema == "ananta.execution_plan.v1"
            assert migrated_state.schema == "ananta.workflow_state.v1"

    asyncio.run(scenario())


def test_typed_updates_validate_duplicate_stale_unauthorized_and_parameters() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(
                workflow_id=workflow_id,
                gate=True,
                mutable_parameters=("mode",),
                parameters={"mode": "safe"},
            )
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            waiting = await _wait_for_status(handle, "waiting_approval")
            parameter_command = _command(
                workflow_input,
                command_id="parameter-1",
                command_type=WorkflowCommandType.PARAMETER_UPDATE,
                revision=waiting["revision"],
                payload={"parameters": {"mode": "fast"}},
            )
            first = await handle.execute_update("command", parameter_command)
            duplicate = await handle.execute_update("command", parameter_command)
            assert duplicate == first

            stale_approval = _command(
                workflow_input,
                command_id="stale-approval",
                command_type=WorkflowCommandType.APPROVE,
                revision=waiting["revision"],
            )
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update("command", stale_approval)

            current = await handle.query("status")
            foreign_authorization = _authorization(
                workflow_id=f"foreign-{uuid.uuid4().hex}",
                run_id="run-1",
                step_id="step-1",
            )
            unauthorized = _command(
                workflow_input,
                command_id="foreign-approval",
                command_type=WorkflowCommandType.APPROVE,
                revision=current["revision"],
                authorization=foreign_authorization,
            )
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update("command", unauthorized)

            approval = _command(
                workflow_input,
                command_id="approval-1",
                command_type=WorkflowCommandType.APPROVE,
                revision=current["revision"],
            )
            await handle.execute_update("command", approval)
            result = await handle.result()
            assert result["status"] == "completed"
            assert result["parameters"] == {"mode": "fast"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("activity_class", "fail_submissions", "retry_budget", "expected_submissions", "expected_status"),
    [
        (ActivityClass.READ_ONLY, 1, 2, 2, "completed"),
        (ActivityClass.IDEMPOTENT, 10, 1, 2, "failed"),
        (ActivityClass.NON_IDEMPOTENT, 10, 10, 1, "failed"),
    ],
)
def test_retry_budget_and_non_idempotent_retry_suppression(
    activity_class: ActivityClass,
    fail_submissions: int,
    retry_budget: int,
    expected_submissions: int,
    expected_status: str,
) -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway(fail_submissions=fail_submissions)
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(
                workflow_id=workflow_id,
                activity_class=activity_class,
                retry_budget=retry_budget,
            )
            result = await environment.client.execute_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            assert result["status"] == expected_status
            assert gateway.submissions == expected_submissions
            assert len(set(gateway.submitted_operations)) <= 1
            if expected_status == "completed":
                assert result["retry_budget_remaining"] == retry_budget - 1
            assert len(gateway.retry_consumptions) == max(0, expected_submissions - 1)
            assert all(category == "temporal_activity" for _retry_id, category in gateway.retry_consumptions)

    asyncio.run(scenario())


def test_expired_authorization_and_operation_mismatch_fail_closed() -> None:
    async def expired_scenario() -> None:
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(
                workflow_id=workflow_id,
                authorization_time=time.time() - 600,
                authorization_ttl=1,
            )
            result = await environment.client.execute_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            assert result["status"] == "failed"
            assert gateway.submissions == 0

    async def mismatch_scenario() -> None:
        gateway = ScriptedHubGateway(operation_override="op-foreign")
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            result = await environment.client.execute_workflow(
                "AnantaWorkflow",
                _workflow_input(workflow_id=workflow_id).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            assert result["status"] == "failed"
            assert gateway.submissions == 1

    asyncio.run(expired_scenario())
    asyncio.run(mismatch_scenario())


def test_timeout_uncertainty_and_cancel_propagation() -> None:
    async def timeout_scenario() -> None:
        gateway = ScriptedHubGateway(poll_cycles=-1)
        async with _running_worker(gateway, activity_timeout_seconds=0.12) as (
            environment,
            task_queue,
        ):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            result = await environment.client.execute_workflow(
                "AnantaWorkflow",
                _workflow_input(workflow_id=workflow_id).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            assert result["status"] == "failed"
            assert result["reason_code"] == "hub_task_wait_timeout"
            assert gateway.polls >= 1

    async def cancel_scenario() -> None:
        gateway = ScriptedHubGateway(poll_cycles=-1)
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(workflow_id=workflow_id)
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            while gateway.submissions == 0:
                await asyncio.sleep(0.01)
            running = await handle.query("status")
            cancellation = _command(
                workflow_input,
                command_id="cancel-1",
                command_type=WorkflowCommandType.CANCEL,
                revision=running["revision"],
                payload={"reason": "operator_cancelled"},
            )
            await handle.execute_update("command", cancellation)
            result = await handle.result()
            assert result["status"] == "cancelled"
            assert result["reason_code"] == "operator_cancelled"
            assert gateway.cancellations == 1

    asyncio.run(timeout_scenario())
    asyncio.run(cancel_scenario())


def test_direct_signal_race_is_fail_closed_across_ten_repetitions() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            for index in range(10):
                workflow_id = f"wf-race-{index}-{uuid.uuid4().hex}"
                workflow_input = _workflow_input(workflow_id=workflow_id, gate=True)
                handle = await environment.client.start_workflow(
                    "AnantaWorkflow",
                    workflow_input.to_dict(),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                waiting = await _wait_for_status(handle, "waiting_approval")
                before = gateway.submissions
                approval = _command(
                    workflow_input,
                    command_id=f"approve-{index}",
                    command_type=WorkflowCommandType.APPROVE,
                    revision=waiting["revision"],
                )
                cancellation = _command(
                    workflow_input,
                    command_id=f"cancel-{index}",
                    command_type=WorkflowCommandType.CANCEL,
                    revision=waiting["revision"],
                )
                await asyncio.gather(
                    handle.signal("approve", approval),
                    handle.signal("cancel", cancellation),
                )
                rejected = await handle.query("status")
                assert rejected["status"] == "waiting_approval"
                assert rejected["reason_code"] == "direct_signal_forbidden"
                assert gateway.submissions == before

                current = await handle.query("status")
                accepted = _command(
                    workflow_input,
                    command_id=f"hub-update-{index}",
                    command_type=WorkflowCommandType.APPROVE,
                    revision=current["revision"],
                )
                await handle.execute_update("command", accepted)
                result = await handle.result()
                assert result["status"] == "completed"
                assert gateway.submissions - before == 1

    asyncio.run(scenario())


def test_history_limit_stops_before_all_side_effects_are_submitted() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-{uuid.uuid4().hex}"
            workflow_input = _workflow_input(
                workflow_id=workflow_id,
                step_count=20,
                max_history_events=100,
            )
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            limit_evidence = await handle.query("limit_evidence")
            history = await handle.fetch_history()
            assert result["status"] == "failed"
            assert result["reason_code"] == "history_limit_exceeded"
            assert 1 <= gateway.submissions < 20
            assert gateway.submissions == len(result["completed_step_ids"])
            assert len(gateway.submitted_operations) == len(set(gateway.submitted_operations))
            assert len(history.events) > 0
            assert limit_evidence["history_event_estimate"] > 100
            assert limit_evidence["history_limit_events"] == 100
            assert limit_evidence["failed_closed"] is True
            assert limit_evidence["continue_as_new_required"] is True
            assert limit_evidence["reason_code"] == "history_limit_exceeded"

    asyncio.run(scenario())


def test_bounded_parallel_fanout_and_deterministic_merge_repeat_ten_times() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway(submission_delay_seconds=0.03)
        async with _running_worker(gateway) as (environment, task_queue):
            for iteration in range(10):
                workflow_id = f"wf-parallel-{iteration}-{uuid.uuid4().hex}"
                run_id = f"run-{iteration}"

                def step(
                    step_id: str,
                    *,
                    depends_on: tuple[str, ...] = (),
                    node_type: str = "task",
                    merge_strategy: str = "",
                ) -> TemporalWorkflowStep:
                    return TemporalWorkflowStep(
                        step_id=step_id,
                        title=step_id,
                        operation_id=operation_id_for(
                            tenant_id="tenant-1",
                            run_id=run_id,
                            step_id=step_id,
                            declared_operation="hub_task",
                        ),
                        authorization_envelope=_authorization(
                            workflow_id=workflow_id,
                            run_id=run_id,
                            step_id=step_id,
                        ),
                        depends_on=depends_on,
                        node_type=node_type,
                        merge_strategy=merge_strategy,
                    )

                steps = (
                    step("root"),
                    step("branch-a", depends_on=("root",)),
                    step("branch-b", depends_on=("root",)),
                    step(
                        "merge",
                        depends_on=("branch-a", "branch-b"),
                        node_type="merge",
                        merge_strategy="ordered_artifact_refs",
                    ),
                )
                gateway.artifacts_by_step = {
                    "root": ({"artifact_id": "artifact-root", "kind": "generated"},),
                    "branch-a": ({"artifact_id": "artifact-a", "kind": "generated"},),
                    "branch-b": ({"artifact_id": "artifact-b", "kind": "generated"},),
                    "merge": ({"artifact_id": "artifact-merged", "kind": "generated"},),
                }
                workflow_input = AnantaWorkflowInput(
                    tenant_id="tenant-1",
                    workflow_id=workflow_id,
                    run_id=run_id,
                    correlation_id=f"correlation-{iteration}",
                    plan_hash=PLAN_HASH,
                    policy_version="policy-v1",
                    steps=steps,
                    retry_budget_remaining=2,
                    max_parallel_steps=4,
                    tenant_parallel_limit=2,
                    worker_parallel_limit=3,
                )

                result = await environment.client.execute_workflow(
                    "AnantaWorkflow",
                    workflow_input.to_dict(),
                    id=workflow_id,
                    task_queue=task_queue,
                )

                assert result["status"] == "completed"
                assert result["completed_step_ids"] == [
                    "root",
                    "branch-a",
                    "branch-b",
                    "merge",
                ]
                assert result["failed_step_ids"] == []
                assert gateway.submitted_artifacts_by_step["merge"] == (
                    "artifact-a",
                    "artifact-b",
                )
            assert gateway.max_active_submissions == 2
            assert gateway.submissions == 40
            assert len(gateway.submitted_operations) == 40

    asyncio.run(scenario())


def test_cancel_propagates_to_every_open_parallel_branch() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway(poll_cycles=-1)
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"wf-parallel-cancel-{uuid.uuid4().hex}"
            run_id = "run-parallel-cancel"

            def branch(step_id: str) -> TemporalWorkflowStep:
                return TemporalWorkflowStep(
                    step_id=step_id,
                    title=step_id,
                    operation_id=operation_id_for(
                        tenant_id="tenant-1",
                        run_id=run_id,
                        step_id=step_id,
                        declared_operation="hub_task",
                    ),
                    authorization_envelope=_authorization(
                        workflow_id=workflow_id,
                        run_id=run_id,
                        step_id=step_id,
                    ),
                )

            workflow_input = AnantaWorkflowInput(
                tenant_id="tenant-1",
                workflow_id=workflow_id,
                run_id=run_id,
                correlation_id="correlation-parallel-cancel",
                plan_hash=PLAN_HASH,
                policy_version="policy-v1",
                steps=(branch("branch-a"), branch("branch-b")),
                retry_budget_remaining=0,
                max_parallel_steps=2,
                tenant_parallel_limit=2,
                worker_parallel_limit=2,
            )
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                workflow_input.to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            for _ in range(200):
                if gateway.submissions == 2:
                    break
                await asyncio.sleep(0.01)
            assert gateway.submissions == 2
            running = await handle.query("status")
            assert running["active_step_ids"] == ["branch-a", "branch-b"]
            cancellation = _command(
                workflow_input,
                command_id="cancel-parallel",
                command_type=WorkflowCommandType.CANCEL,
                revision=running["revision"],
                payload={"reason": "operator_cancelled_parallel"},
            )
            await handle.execute_update("command", cancellation)
            result = await handle.result()

            assert result["status"] == "cancelled"
            assert result["reason_code"] == "operator_cancelled_parallel"
            assert gateway.cancellations == 2

    asyncio.run(scenario())
