from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncIterator

import pytest

pytest.importorskip("temporalio")

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from ananta_contracts.hub_task_gateway import RetryBudgetReceipt
from ananta_contracts.temporal_workflow import ActivityClass, ProbeRequest
from worker.temporal.activities import HubActivityGateway, probe_activity
from worker.temporal.authorization import RuntimeAuthorizationVerifier
from worker.temporal.hub_gateway import HubGatewayError, HubTaskReceipt
from worker.temporal.workflows import (
    AnantaWorkflow,
    TemporalProbeWorkflow,
    TemporalRecoveryProbeWorkflow,
)

from .test_temporal_runtime_test_environment import (
    KEY_ID,
    SIGNING_KEY,
    ScriptedHubGateway,
    _running_worker,
    _workflow_input,
)


@dataclass
class CrashAfterAcceptanceGateway:
    """Hub boundary that loses one poll response after accepting the task."""

    poll_failures_remaining: int = 1
    submissions: int = 0
    polls: int = 0
    accepted_effects: int = 0
    accepted_operations: set[str] = field(default_factory=set)
    retry_consumptions: list[tuple[str, str]] = field(default_factory=list)

    async def consume_retry(
        self,
        request: Any,
        *,
        retry_id: str,
        category: str,
    ) -> RetryBudgetReceipt:
        self.retry_consumptions.append((retry_id, category))
        used = len(self.retry_consumptions)
        return RetryBudgetReceipt(
            retry_id=retry_id,
            category=category,
            used=used,
            maximum=int(request.retry_budget_maximum or 0),
            remaining=max(0, int(request.retry_budget_maximum or 0) - used),
        )

    async def submit_authorized_task(self, request: Any) -> HubTaskReceipt:
        self.submissions += 1
        if request.operation_id not in self.accepted_operations:
            self.accepted_operations.add(request.operation_id)
            self.accepted_effects += 1
        return self._receipt(request.operation_id, "running")

    async def get_task(
        self,
        *,
        hub_task_id: str,
        operation_id: str,
    ) -> HubTaskReceipt:
        del hub_task_id
        self.polls += 1
        if self.poll_failures_remaining:
            self.poll_failures_remaining -= 1
            raise HubGatewayError("hub_crashed_after_task_acceptance", retryable=True)
        return self._receipt(operation_id, "completed")

    async def request_cancel(
        self,
        *,
        hub_task_id: str,
        operation_id: str,
        reason: str,
    ) -> None:
        del hub_task_id, operation_id, reason

    @staticmethod
    def _receipt(operation_id: str, status: str) -> HubTaskReceipt:
        return HubTaskReceipt(
            hub_task_id=f"task-{operation_id[-16:]}",
            operation_id=operation_id,
            status=status,
            authorization_state="valid",
            ledger_state="completed" if status == "completed" else "started",
            canonical_event_refs=(f"event-{operation_id[-16:]}",),
        )


@dataclass
class LostNonIdempotentAcknowledgementGateway:
    """Records an external effect, then loses the only acknowledgement."""

    submissions: int = 0
    unconfirmed_effects: int = 0
    accepted_operations: set[str] = field(default_factory=set)
    retry_consumptions: list[tuple[str, str]] = field(default_factory=list)

    async def consume_retry(
        self,
        request: Any,
        *,
        retry_id: str,
        category: str,
    ) -> RetryBudgetReceipt:
        del request
        self.retry_consumptions.append((retry_id, category))
        return RetryBudgetReceipt(
            retry_id=retry_id,
            category=category,
            used=len(self.retry_consumptions),
            maximum=1,
            remaining=0,
        )

    async def submit_authorized_task(self, request: Any) -> HubTaskReceipt:
        self.submissions += 1
        if request.operation_id not in self.accepted_operations:
            self.accepted_operations.add(request.operation_id)
            self.unconfirmed_effects += 1
        raise HubGatewayError(
            "non_idempotent_acknowledgement_lost",
            retryable=True,
        )

    async def get_task(self, **_values: Any) -> HubTaskReceipt:
        raise AssertionError("a lost submission acknowledgement must not be polled")

    async def request_cancel(self, **_values: Any) -> None:
        return None


@activity.defn(name="ananta.failure-lab.heartbeat-loss.v1")
async def _heartbeat_loss_activity() -> dict[str, int]:
    attempt = int(activity.info().attempt)
    if attempt == 1:
        activity.heartbeat({"schema": "ananta.failure-lab-heartbeat.v1"})
        await asyncio.sleep(1.25)
    return {"attempt": attempt}


@workflow.defn(name="AnantaHeartbeatLossRecoveryTestWorkflow", sandboxed=False)
class _HeartbeatLossRecoveryWorkflow:
    @workflow.run
    async def run(self) -> dict[str, int]:
        return await workflow.execute_activity(
            "ananta.failure-lab.heartbeat-loss.v1",
            start_to_close_timeout=timedelta(seconds=5),
            heartbeat_timeout=timedelta(milliseconds=250),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100),
                maximum_interval=timedelta(milliseconds=100),
                backoff_coefficient=1.0,
                maximum_attempts=2,
            ),
            result_type=dict,
        )


@activity.defn(name="ananta.failure-lab.start-to-close-timeout.v1")
async def _start_to_close_timeout_activity() -> None:
    await asyncio.sleep(1.0)


@workflow.defn(name="AnantaStartToCloseTimeoutTestWorkflow", sandboxed=False)
class _StartToCloseTimeoutWorkflow:
    @workflow.run
    async def run(self) -> dict[str, str]:
        try:
            await workflow.execute_activity(
                "ananta.failure-lab.start-to-close-timeout.v1",
                start_to_close_timeout=timedelta(milliseconds=200),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            return {"status": "failed_closed", "reason_code": "activity_timeout"}
        return {"status": "unexpected_success", "reason_code": ""}


def _gateway_worker(
    environment: WorkflowEnvironment,
    task_queue: str,
    gateway: Any,
) -> Worker:
    activity_gateway = HubActivityGateway(
        gateway=gateway,
        authorization_verifier=RuntimeAuthorizationVerifier(
            keys={KEY_ID: SIGNING_KEY},
            active_key_id=KEY_ID,
        ),
        poll_seconds=0.05,
        activity_timeout_seconds=30,
    )
    return Worker(
        environment.client,
        task_queue=task_queue,
        workflows=[
            TemporalProbeWorkflow,
            TemporalRecoveryProbeWorkflow,
            AnantaWorkflow,
        ],
        activities=[probe_activity, activity_gateway.execute],
        graceful_shutdown_timeout=timedelta(seconds=1),
        # A process replacement cannot retain an in-memory sticky cache.  Turning
        # it off here makes every task prove reconstruction from server history.
        max_cached_workflows=0,
    )


@asynccontextmanager
async def _test_activity_worker(
    *,
    workflow_type: type,
    activity_fn: Any,
) -> AsyncIterator[tuple[WorkflowEnvironment, str]]:
    environment = await WorkflowEnvironment.start_time_skipping()
    task_queue = f"temporal-failure-lab-{uuid.uuid4().hex}"
    try:
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[workflow_type],
            activities=[activity_fn],
        ):
            yield environment, task_queue
    finally:
        await environment.shutdown()


async def _wait_recovery_state(
    handle: Any,
    expected: str,
    *,
    attempts: int = 200,
) -> dict[str, Any]:
    for _ in range(attempts):
        try:
            state = await handle.query("recovery_state")
        except Exception:  # noqa: BLE001 - Worker replacement is the test subject
            await asyncio.sleep(0.01)
            continue
        if isinstance(state, dict) and state.get("status") == expected:
            return dict(state)
        await asyncio.sleep(0.01)
    raise AssertionError(f"recovery probe did not reach {expected}")


def test_worker_crash_replacement_replays_waiting_workflow_without_state_loss() -> None:
    async def scenario() -> None:
        environment = await asyncio.wait_for(
            WorkflowEnvironment.start_time_skipping(),
            timeout=10,
        )
        task_queue = f"temporal-worker-crash-{uuid.uuid4().hex}"
        gateway = ScriptedHubGateway()
        first_worker = _gateway_worker(environment, task_queue, gateway)
        first_worker_task = asyncio.create_task(first_worker.run())
        try:
            workflow_id = f"worker-crash-{uuid.uuid4().hex}"
            request = ProbeRequest(request_id=f"{workflow_id}-request", value="durable")
            handle = await asyncio.wait_for(
                environment.client.start_workflow(
                    "AnantaTemporalRecoveryProbeWorkflow",
                    request,
                    id=workflow_id,
                    task_queue=task_queue,
                ),
                timeout=10,
            )
            before_crash = await asyncio.wait_for(
                _wait_recovery_state(handle, "waiting"),
                timeout=10,
            )

            # Stop the in-process SDK worker at the same durable boundary used by
            # the Compose SIGKILL drill.  The actual ungraceful process loss is
            # exercised by the real-container gate; the Test Environment keeps
            # its child server alive so the same run can be replayed here.
            await asyncio.wait_for(first_worker.shutdown(), timeout=5)
            await asyncio.wait_for(first_worker_task, timeout=5)
            assert not first_worker.is_running

            replacement = _gateway_worker(environment, task_queue, gateway)
            async with replacement:
                after_replay = await asyncio.wait_for(
                    _wait_recovery_state(handle, "waiting"),
                    timeout=10,
                )
                assert after_replay == before_crash
                await handle.signal("release", request.request_id)
                result = await handle.result()
                assert result["status"] == "completed"
                assert result["request_id"] == request.request_id
                assert result["run_id"] == before_crash["run_id"]
                history = await handle.fetch_history()
                replay = await Replayer(workflows=[TemporalRecoveryProbeWorkflow]).replay_workflow(history)
                assert replay.replay_failure is None
        finally:
            if first_worker.is_running:
                await first_worker.shutdown()
            if not first_worker_task.done():
                first_worker_task.cancel()
                await asyncio.gather(first_worker_task, return_exceptions=True)
            await asyncio.wait_for(environment.shutdown(), timeout=10)

    asyncio.run(scenario())


def test_hub_crash_after_task_acceptance_recovers_without_duplicate_effect() -> None:
    async def scenario() -> None:
        gateway = CrashAfterAcceptanceGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"hub-crash-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                _workflow_input(
                    workflow_id=workflow_id,
                    activity_class=ActivityClass.IDEMPOTENT,
                    retry_budget=2,
                ).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()

            assert result["status"] == "completed"
            assert result["completed_step_ids"] == ["step-1"]
            assert gateway.submissions == 2
            assert gateway.accepted_effects == 1
            assert len(gateway.accepted_operations) == 1
            assert len(gateway.retry_consumptions) == 1

    asyncio.run(scenario())


def test_heartbeat_loss_retries_idempotent_activity_in_real_test_environment() -> None:
    async def scenario() -> None:
        async with _test_activity_worker(
            workflow_type=_HeartbeatLossRecoveryWorkflow,
            activity_fn=_heartbeat_loss_activity,
        ) as (environment, task_queue):
            workflow_id = f"heartbeat-loss-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaHeartbeatLossRecoveryTestWorkflow",
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()

            assert result == {"attempt": 2}
            recorded_attempts = [
                event.activity_task_started_event_attributes.attempt
                for event in history.events
                if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
            ]
            # Temporal compacts retryable intermediate timeouts from workflow
            # history, but the final started event preserves the actual attempt.
            assert recorded_attempts == [2]

    asyncio.run(scenario())


def test_start_to_close_timeout_fails_closed_without_confirming_state() -> None:
    async def scenario() -> None:
        async with _test_activity_worker(
            workflow_type=_StartToCloseTimeoutWorkflow,
            activity_fn=_start_to_close_timeout_activity,
        ) as (environment, task_queue):
            workflow_id = f"activity-timeout-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaStartToCloseTimeoutTestWorkflow",
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()

            assert result == {
                "status": "failed_closed",
                "reason_code": "activity_timeout",
            }
            assert any(event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT for event in history.events)

    asyncio.run(scenario())


def test_non_idempotent_ack_loss_is_not_retried_or_reported_completed() -> None:
    async def scenario() -> None:
        gateway = LostNonIdempotentAcknowledgementGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"uncertain-side-effect-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                _workflow_input(
                    workflow_id=workflow_id,
                    activity_class=ActivityClass.NON_IDEMPOTENT,
                    retry_budget=10,
                ).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            history = await handle.fetch_history()

            assert result["status"] == "failed"
            assert result["reason_code"] == "activity_retry_exhausted"
            assert result["completed_step_ids"] == []
            assert gateway.submissions == 1
            assert gateway.unconfirmed_effects == 1
            assert gateway.retry_consumptions == []
            replay = await Replayer(workflows=[AnantaWorkflow]).replay_workflow(history)
            assert replay.replay_failure is None

    asyncio.run(scenario())


def test_state_limit_fails_closed_before_next_side_effect_submission() -> None:
    async def scenario() -> None:
        gateway = ScriptedHubGateway()
        async with _running_worker(gateway) as (environment, task_queue):
            workflow_id = f"state-limit-{uuid.uuid4().hex}"
            handle = await environment.client.start_workflow(
                "AnantaWorkflow",
                _workflow_input(
                    workflow_id=workflow_id,
                    step_count=100,
                    step_id_size=180,
                    max_state_bytes=16_384,
                ).to_dict(),
                id=workflow_id,
                task_queue=task_queue,
            )
            result = await handle.result()
            limit_evidence = await handle.query("limit_evidence")

            assert result["status"] == "failed"
            assert result["reason_code"] == "state_limit_exceeded"
            assert limit_evidence == {
                "schema": "ananta.temporal-limit-evidence.v1",
                "history_event_estimate": limit_evidence["history_event_estimate"],
                "history_limit_events": 20_000,
                "state_bytes_estimate": limit_evidence["state_bytes_estimate"],
                "state_limit_bytes": 16_384,
                "failed_closed": True,
                "continue_as_new_required": True,
                "reason_code": "state_limit_exceeded",
            }
            assert limit_evidence["state_bytes_estimate"] > 16_384
            assert 0 < gateway.submissions < 100
            assert gateway.submissions == len(result["completed_step_ids"])
            assert len(gateway.submitted_operations) == len(set(gateway.submitted_operations))

    asyncio.run(scenario())
