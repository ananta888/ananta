"""Deterministic Temporal workflow definitions for Ananta.

The workflow owns durable technical state only.  It never imports the hub DB,
policy engines, providers, tools or worker routing.  Executable work crosses a
single Activity boundary and is materialized by the hub as a normal delegated
task.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from ananta_contracts.temporal_workflow import (
    COMMAND_AUTHORITY_ACTIVITY,
    AnantaWorkflowInput,
    ArtifactReference,
    ProbeRequest,
    StepActivityInput,
    StepActivityResult,
    TemporalContractError,
    TemporalWorkflowStep,
    WorkflowCommand,
    WorkflowCommandAuthorityResult,
    WorkflowCommandResult,
    WorkflowCommandType,
    WorkflowPhase,
    WorkflowStatus,
)
from worker.temporal.retry_profiles import retry_profile_for

ANANTA_WORKFLOW_TYPE = "AnantaWorkflow"
PROBE_WORKFLOW_TYPE = "AnantaTemporalProbeWorkflow"
RECOVERY_PROBE_WORKFLOW_TYPE = "AnantaTemporalRecoveryProbeWorkflow"
N_MINUS_ONE_PATCH_ID = "ananta-workflow-state-machine-v1"
HUB_SIGNED_COMMAND_PATCH_ID = "ananta-hub-signed-workflow-command-v2"
BOUNDED_PARALLEL_PATCH_ID = "ananta-workflow-bounded-parallel-v1"
COMMAND_AUTHORITY_PATCH_ID = "ananta-workflow-command-authority-v3"


@workflow.defn(name=PROBE_WORKFLOW_TYPE, sandboxed=True)
class TemporalProbeWorkflow:
    @workflow.run
    async def run(self, request: ProbeRequest) -> dict[str, str]:
        result = await workflow.execute_activity(
            "ananta.temporal.probe-activity.v1",
            request,
            start_to_close_timeout=timedelta(seconds=10),
            result_type=dict,
        )
        return {str(key): str(value) for key, value in dict(result).items()}


@workflow.defn(name=RECOVERY_PROBE_WORKFLOW_TYPE, sandboxed=True)
class TemporalRecoveryProbeWorkflow:
    """Side-effect-free durable probe used by the real Compose restart gate."""

    def __init__(self) -> None:
        self._request_id = ""
        self._value = ""
        self._run_id = ""
        self._released = False
        self._rejected_release_count = 0

    @workflow.run
    async def run(self, request: ProbeRequest) -> dict[str, str | int]:
        self._request_id = request.request_id
        self._value = request.value
        self._run_id = workflow.info().run_id
        await workflow.wait_condition(lambda: self._released)
        return {
            "schema": "ananta.temporal-recovery-probe.v1",
            "status": "completed",
            "request_id": self._request_id,
            "value": self._value,
            "run_id": self._run_id,
            "rejected_release_count": self._rejected_release_count,
        }

    @workflow.query(name="recovery_state")
    def recovery_state(self) -> dict[str, str | int]:
        return {
            "schema": "ananta.temporal-recovery-probe.v1",
            "status": "released" if self._released else "waiting",
            "request_id": self._request_id,
            "value": self._value,
            "run_id": self._run_id,
            "rejected_release_count": self._rejected_release_count,
        }

    @workflow.signal(name="release")
    def release(self, request_id: str) -> None:
        if str(request_id) != self._request_id:
            self._rejected_release_count += 1
            return
        self._released = True


@workflow.defn(name=ANANTA_WORKFLOW_TYPE, sandboxed=True)
class AnantaWorkflow:
    """Versioned DAG state machine over Hub-delegated Activities.

    Temporal owns durable technical scheduling only.  The Hub remains the
    authority for task creation, policy, authorization, retry budgets and side
    effects; every executable node still crosses the single Hub Activity port.
    """

    _record_compatibility_patch = True

    def __init__(self) -> None:
        self._input: AnantaWorkflowInput | None = None
        self._phase = WorkflowPhase.CREATED
        self._phase_before_pause = WorkflowPhase.RUNNING
        self._revision = 0
        self._current_step_id = ""
        self._completed_step_ids: list[str] = []
        self._open_gates: list[str] = []
        self._approved_gates: set[str] = set()
        self._processed_command_ids: dict[str, WorkflowCommandResult] = {}
        self._processed_command_payload_digests: dict[str, str] = {}
        self._command_authority_enabled = False
        self._parameters: dict[str, Any] = {}
        self._retry_budget_remaining = 0
        self._retry_budget_maximum = 0
        self._checkpoint_ref = ""
        self._effective_plan_hash = ""
        self._plan_revision = 1
        self._plan_ref = ""
        self._reason_code = ""
        self._history_event_estimate = 0
        self._state_bytes_estimate = 0
        self._artifact_refs: list[str] = []
        self._artifacts_by_step: dict[str, tuple[ArtifactReference, ...]] = {}
        self._failed_step_ids: dict[str, str] = {}
        self._active_activity = None
        self._active_activities: dict[str, Any] = {}

    @workflow.run
    async def run(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        try:
            self._input = AnantaWorkflowInput.from_mapping(raw_input)
        except TemporalContractError as exc:
            self._phase = WorkflowPhase.FAILED
            self._reason_code = exc.reason_code
            return self._status().to_dict()
        if self._input.workflow_id != workflow.info().workflow_id:
            self._phase = WorkflowPhase.FAILED
            self._reason_code = "temporal_workflow_id_mismatch"
            return self._status().to_dict()

        self._command_authority_enabled = (
            workflow.patched(COMMAND_AUTHORITY_PATCH_ID) if self._record_compatibility_patch else False
        )

        self._parameters = dict(self._input.parameters)
        self._effective_plan_hash = self._input.plan_hash
        self._retry_budget_remaining = self._input.retry_budget_remaining
        self._retry_budget_maximum = int(self._input.retry_budget_maximum or 0)
        self._transition(WorkflowPhase.RUNNING)

        # The marker is retained for N-1 histories.  Future changes deprecate
        # this marker only after the stored replay corpus no longer contains it.
        if self._record_compatibility_patch:
            workflow.patched(N_MINUS_ONE_PATCH_ID)

        if workflow.patched(BOUNDED_PARALLEL_PATCH_ID):
            await self._run_bounded_dag()
        else:
            # Stored N-1 histories contain the original sequential command
            # order.  Keeping that path byte-for-byte equivalent avoids replay
            # drift while every new run records the bounded-DAG patch marker.
            await self._run_legacy_sequential()

        self._current_step_id = ""
        self._open_gates = []
        if self._phase not in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
            self._transition(WorkflowPhase.COMPLETED)
        return self._status().to_dict()

    async def _run_legacy_sequential(self) -> None:
        assert self._input is not None
        for step in self._input.steps:
            if self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
                break
            await workflow.wait_condition(
                lambda: self._phase not in {WorkflowPhase.PAUSED},
            )
            if self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
                break
            if any(dependency not in self._completed_step_ids for dependency in step.depends_on):
                self._fail("dependency_not_completed")
                break

            self._current_step_id = step.step_id
            self._revision += 1
            self._refresh_checkpoint_ref()
            self._history_event_estimate += 1
            if not self._within_limits():
                break

            if step.gate and step.step_id not in self._approved_gates:
                self._open_gates = [step.step_id]
                self._transition(WorkflowPhase.WAITING_APPROVAL)
                await workflow.wait_condition(
                    lambda: (
                        step.step_id in self._approved_gates
                        or self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}
                    ),
                )
                self._open_gates = []
                if self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
                    break
                self._transition(WorkflowPhase.RUNNING)

            request = StepActivityInput(
                tenant_id=self._input.tenant_id,
                workflow_id=self._input.workflow_id,
                run_id=self._input.run_id,
                correlation_id=self._input.correlation_id,
                step_id=step.step_id,
                operation_id=step.operation_id,
                plan_hash=self._input.plan_hash,
                task_kind=step.task_kind,
                authorization_envelope=step.authorization_envelope,
                artifact_refs=step.artifact_refs,
                required_capabilities=step.required_capabilities,
                activity_class=step.activity_class,
                retry_budget_remaining=self._retry_budget_remaining,
                retry_budget_maximum=self._retry_budget_maximum,
                parameters=dict(self._parameters),
            )
            options = retry_profile_for(step.activity_class).temporal_options(
                retry_budget_remaining=self._retry_budget_remaining
            )
            try:
                self._active_activity = workflow.start_activity(
                    "ananta.hub-task.execute.v1",
                    request.to_dict(),
                    result_type=dict,
                    activity_id=f"ananta:{step.step_id}:{step.operation_id}",
                    **options,
                )
                result = await self._active_activity
            except ActivityError:
                # Temporal's retry profile is already exhausted.  The common
                # hub budget must be reconciled before any manual retry, so this
                # run fails closed instead of starting another Activity.
                self._retry_budget_remaining = 0
                if self._phase is not WorkflowPhase.CANCELLED:
                    self._fail("activity_retry_exhausted")
                break
            finally:
                self._active_activity = None
            try:
                result = StepActivityResult.from_mapping(result)
            except TemporalContractError as exc:
                self._fail(exc.reason_code)
                break

            self._history_event_estimate += 6 + max(0, int(result.attempt) - 1) * 3
            self._retry_budget_remaining = max(
                0,
                self._retry_budget_remaining - max(0, int(result.attempt) - 1),
            )
            if result.operation_id != step.operation_id:
                self._fail("activity_operation_binding_mismatch")
                break
            self._artifact_refs.extend(
                artifact.artifact_id
                for artifact in result.artifact_refs
                if artifact.artifact_id not in self._artifact_refs
            )
            if result.status != "completed":
                self._fail(result.reason_code or f"activity_{result.status}")
                break
            self._completed_step_ids.append(step.step_id)
            self._revision += 1
            self._refresh_checkpoint_ref()
            if not self._within_limits():
                break

    async def _run_bounded_dag(self) -> None:
        assert self._input is not None
        remaining = {step.step_id for step in self._input.steps}
        while remaining and self._phase not in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
            await workflow.wait_condition(lambda: self._phase is not WorkflowPhase.PAUSED)
            if self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
                break
            ready = [
                step for step in self._input.steps if step.step_id in remaining and self._dependencies_resolved(step)
            ]
            if not ready:
                self._fail("dependency_not_completed")
                break

            gated = [step for step in ready if step.gate and step.step_id not in self._approved_gates]
            if gated:
                await self._wait_for_gate(gated[0])
                continue

            first_group = ready[0].parallel_group
            limit = min(
                self._input.max_parallel_steps,
                self._input.tenant_parallel_limit,
                self._input.worker_parallel_limit,
            )
            batch = tuple(step for step in ready if step.parallel_group == first_group)[:limit]
            await self._execute_batch(batch)
            remaining.difference_update(self._completed_step_ids)
            remaining.difference_update(self._failed_step_ids)

    async def _wait_for_gate(self, step: TemporalWorkflowStep) -> None:
        self._current_step_id = step.step_id
        self._open_gates = [step.step_id]
        self._transition(WorkflowPhase.WAITING_APPROVAL)
        await workflow.wait_condition(
            lambda: (
                step.step_id in self._approved_gates or self._phase in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}
            )
        )
        self._open_gates = []
        if self._phase not in {WorkflowPhase.CANCELLED, WorkflowPhase.FAILED}:
            self._transition(WorkflowPhase.RUNNING)

    async def _execute_batch(self, batch: tuple[TemporalWorkflowStep, ...]) -> None:
        assert self._input is not None
        if not batch:
            self._fail("parallel_batch_empty")
            return
        self._current_step_id = batch[0].step_id
        started: list[tuple[TemporalWorkflowStep, Any]] = []
        for step in batch:
            self._revision += 1
            self._refresh_checkpoint_ref()
            self._history_event_estimate += 1
            if not self._within_limits():
                break
            request = self._activity_request(step)
            options = retry_profile_for(step.activity_class).temporal_options(
                retry_budget_remaining=self._retry_budget_remaining
            )
            handle = workflow.start_activity(
                "ananta.hub-task.execute.v1",
                request.to_dict(),
                result_type=dict,
                activity_id=f"ananta:{step.step_id}:{step.operation_id}",
                **options,
            )
            self._active_activities[step.step_id] = handle
            started.append((step, handle))

        outcomes: list[tuple[TemporalWorkflowStep, StepActivityResult | None, str]] = []
        for step, handle in started:
            try:
                raw_result = await handle
                result = StepActivityResult.from_mapping(raw_result)
                outcomes.append((step, result, ""))
            except ActivityError:
                outcomes.append((step, None, "activity_retry_exhausted"))
            except asyncio.CancelledError:
                outcomes.append((step, None, "activity_cancelled"))
            except TemporalContractError as exc:
                outcomes.append((step, None, exc.reason_code))
            finally:
                self._active_activities.pop(step.step_id, None)

        fatal_reason = ""
        for step, result, error in outcomes:
            if error:
                self._retry_budget_remaining = 0
                if self._failure_is_omittable(step.step_id):
                    self._record_omitted_failure(step, error)
                elif not fatal_reason:
                    fatal_reason = error
                continue
            assert result is not None
            failure = self._record_activity_result(step, result)
            if failure:
                if self._failure_is_omittable(step.step_id):
                    self._record_omitted_failure(step, failure)
                elif not fatal_reason:
                    fatal_reason = failure
        if fatal_reason and self._phase is not WorkflowPhase.CANCELLED:
            self._fail(fatal_reason)
        self._current_step_id = ""

    def _activity_request(self, step: TemporalWorkflowStep) -> StepActivityInput:
        assert self._input is not None
        return StepActivityInput(
            tenant_id=self._input.tenant_id,
            workflow_id=self._input.workflow_id,
            run_id=self._input.run_id,
            correlation_id=self._input.correlation_id,
            step_id=step.step_id,
            operation_id=step.operation_id,
            plan_hash=self._input.plan_hash,
            task_kind=step.task_kind,
            authorization_envelope=step.authorization_envelope,
            artifact_refs=self._activity_artifacts(step),
            required_capabilities=step.required_capabilities,
            activity_class=step.activity_class,
            retry_budget_remaining=self._retry_budget_remaining,
            retry_budget_maximum=self._retry_budget_maximum,
            parameters=dict(self._parameters),
            node_type=step.node_type,
            parallel_group=step.parallel_group,
            merge_strategy=step.merge_strategy,
            partial_failure=step.partial_failure,
        )

    def _activity_artifacts(
        self,
        step: TemporalWorkflowStep,
    ) -> tuple[ArtifactReference, ...]:
        values: list[ArtifactReference] = list(step.artifact_refs)
        for dependency in step.depends_on:
            values.extend(
                sorted(
                    self._artifacts_by_step.get(dependency, ()),
                    key=lambda artifact: (artifact.artifact_id, artifact.kind, artifact.digest),
                )
            )
        by_id: dict[str, ArtifactReference] = {}
        for artifact in values:
            by_id.setdefault(artifact.artifact_id, artifact)
        return tuple(by_id[artifact_id] for artifact_id in sorted(by_id))

    def _record_activity_result(
        self,
        step: TemporalWorkflowStep,
        result: StepActivityResult,
    ) -> str:
        self._history_event_estimate += 6 + max(0, int(result.attempt) - 1) * 3
        self._retry_budget_remaining = max(
            0,
            self._retry_budget_remaining - max(0, int(result.attempt) - 1),
        )
        if result.operation_id != step.operation_id:
            return "activity_operation_binding_mismatch"
        if result.status != "completed":
            return result.reason_code or f"activity_{result.status}"
        artifacts = tuple(
            sorted(
                result.artifact_refs,
                key=lambda artifact: (artifact.artifact_id, artifact.kind, artifact.digest),
            )
        )
        self._artifacts_by_step[step.step_id] = artifacts
        for artifact in artifacts:
            if artifact.artifact_id not in self._artifact_refs:
                self._artifact_refs.append(artifact.artifact_id)
        self._completed_step_ids.append(step.step_id)
        self._revision += 1
        self._refresh_checkpoint_ref()
        self._within_limits()
        return ""

    def _record_omitted_failure(self, step: TemporalWorkflowStep, reason_code: str) -> None:
        self._failed_step_ids[step.step_id] = str(reason_code)[:256]
        self._revision += 1
        self._refresh_checkpoint_ref()
        self._history_event_estimate += 1
        self._within_limits()

    def _dependencies_resolved(self, step: TemporalWorkflowStep) -> bool:
        completed = set(self._completed_step_ids)
        failed = set(self._failed_step_ids)
        if step.node_type == "merge" and step.partial_failure == "omit":
            return all(dependency in completed or dependency in failed for dependency in step.depends_on)
        return all(dependency in completed for dependency in step.depends_on)

    def _failure_is_omittable(self, step_id: str) -> bool:
        assert self._input is not None
        consumers = [step for step in self._input.steps if step_id in step.depends_on]
        return bool(consumers) and all(
            step.node_type == "merge" and step.partial_failure == "omit" for step in consumers
        )

    @workflow.query(name="status")
    def query_status(self) -> dict[str, Any]:
        return self._status().to_dict()

    @workflow.query(name="artifact_refs")
    def query_artifact_refs(self) -> list[str]:
        return list(self._artifact_refs)

    @workflow.query(name="limit_evidence")
    def query_limit_evidence(self) -> dict[str, Any]:
        """Expose deterministic threshold measurements without payload data."""

        history_limit = self._input.max_history_events if self._input is not None else 0
        state_limit = self._input.max_state_bytes if self._input is not None else 0
        limit_reason = self._reason_code in {
            "history_limit_exceeded",
            "state_limit_exceeded",
        }
        return {
            "schema": "ananta.temporal-limit-evidence.v1",
            "history_event_estimate": self._history_event_estimate,
            "history_limit_events": history_limit,
            "state_bytes_estimate": self._state_bytes_estimate,
            "state_limit_bytes": state_limit,
            "failed_closed": limit_reason and self._phase is WorkflowPhase.FAILED,
            "continue_as_new_required": limit_reason,
            "reason_code": self._reason_code if limit_reason else "",
        }

    @workflow.update(name="command")
    async def command(self, raw_command: dict[str, Any]) -> dict[str, Any]:
        try:
            command = WorkflowCommand.from_mapping(raw_command)
            if not self._command_authority_enabled:
                if not workflow.unsafe.is_replaying():
                    raise TemporalContractError(
                        "temporal_command_authority_migration_required",
                        "legacy workflow requires a Hub-authorized replacement run",
                    )
                return self._apply_legacy_replay_command(command).to_dict()
            authority = await self._verify_command_authority(command)
            duplicate = self._processed_command_ids.get(command.command_id)
            if duplicate is not None:
                expected_digest = self._processed_command_payload_digests.get(command.command_id)
                if not expected_digest or expected_digest != authority.payload_digest:
                    raise TemporalContractError(
                        "command_duplicate_payload_mismatch",
                        "duplicate workflow command payload differs",
                    )
                return duplicate.to_dict()
            self._validate_command(command)
            result = self._apply_command(command)
            self._processed_command_payload_digests[command.command_id] = authority.payload_digest
            return result.to_dict()
        except TemporalContractError as exc:
            return self._rejected_command_result(raw_command, exc.reason_code).to_dict()

    @command.validator
    def validate_command(self, raw_command: dict[str, Any]) -> None:
        # Update validators cannot run Activities.  Deterministic contract,
        # crypto and domain denials must reach the handler so callers receive a
        # bound ``accepted=false`` result instead of an exception/retry loop.
        if not isinstance(raw_command, dict):
            raise ApplicationError(
                "workflow command rejected",
                type="invalid_command",
                non_retryable=True,
            )

    async def _verify_command_authority(
        self,
        command: WorkflowCommand,
    ) -> WorkflowCommandAuthorityResult:
        raw_result = await workflow.execute_local_activity(
            COMMAND_AUTHORITY_ACTIVITY,
            command.to_dict(),
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
            result_type=dict,
        )
        try:
            result = WorkflowCommandAuthorityResult.from_mapping(raw_result)
        except TemporalContractError as exc:
            raise ApplicationError(
                "workflow command authority returned an invalid result",
                type=f"temporal-command-authority-result-invalid:{exc.reason_code}",
                non_retryable=False,
            ) from exc
        expected_algorithm = command.signature_algorithm or "ed25519"
        if (
            result.command_id != command.command_id
            or result.payload_digest != command.computed_payload_digest()
            or result.signature_algorithm != expected_algorithm
            or result.key_id != command.key_id
        ):
            raise ApplicationError(
                "workflow command authority result is not bound to the command",
                type="temporal-command-authority-result-binding-mismatch",
                non_retryable=False,
            )
        if not result.accepted:
            raise TemporalContractError(
                result.reason_code,
                "workflow command authority rejected the command",
            )
        return result

    def _rejected_command_result(
        self,
        raw_command: dict[str, Any],
        reason_code: str,
    ) -> WorkflowCommandResult:
        command_id = str(raw_command.get("command_id") or "")[:256]
        return WorkflowCommandResult(
            command_id=command_id,
            accepted=False,
            revision=self._revision,
            status=self._phase.value,
            reason_code=str(reason_code or "workflow_command_rejected"),
        )

    def _apply_legacy_replay_command(self, command: WorkflowCommand) -> WorkflowCommandResult:
        duplicate = self._processed_command_ids.get(command.command_id)
        if duplicate is not None:
            return duplicate
        self._validate_command(command)
        result = self._apply_command(command)
        self._processed_command_payload_digests[command.command_id] = command.computed_payload_digest()
        return result

    @workflow.signal(name="approve")
    def approve(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.APPROVE)

    @workflow.signal(name="reject")
    def reject(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.REJECT)

    @workflow.signal(name="pause")
    def pause(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.PAUSE)

    @workflow.signal(name="resume")
    def resume(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.RESUME)

    @workflow.signal(name="cancel")
    def cancel(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.CANCEL)

    @workflow.signal(name="parameter_update")
    def parameter_update(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.PARAMETER_UPDATE)

    @workflow.signal(name="edit")
    def edit(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.EDIT)

    @workflow.signal(name="request_changes")
    def request_changes(self, raw_command: dict[str, Any]) -> None:
        self._apply_signal(raw_command, WorkflowCommandType.REQUEST_CHANGES)

    def _apply_signal(self, raw_command: dict[str, Any], expected: WorkflowCommandType) -> None:
        if workflow.patched(HUB_SIGNED_COMMAND_PATCH_ID):
            del raw_command, expected
            self._reason_code = "direct_signal_forbidden"
            return
        try:
            command = WorkflowCommand.from_mapping(raw_command)
            if command.command_type is not expected:
                raise TemporalContractError("command_type_mismatch", "signal and command type differ")
            if command.command_id in self._processed_command_ids:
                return
            self._validate_command(command)
            self._apply_command(command)
        except TemporalContractError as exc:
            # Signals cannot synchronously report validation failures.  They
            # remain a no-op and expose a redacted stable reason in the query.
            self._reason_code = f"signal_rejected:{exc.reason_code}"

    def _validate_command(self, command: WorkflowCommand) -> None:
        if self._input is None:
            raise TemporalContractError("workflow_not_started", "workflow has not started")
        if len(self._processed_command_ids) >= 1_000:
            raise TemporalContractError("command_limit_exceeded", "workflow command limit was reached")
        if command.expected_revision != self._revision:
            raise TemporalContractError("stale_workflow_revision", "workflow revision is stale")
        if command.command_type is WorkflowCommandType.RETRY:
            raise TemporalContractError(
                "temporal_retry_unsupported",
                "Temporal retry requires a new Hub-owned execution",
            )
        if command.command_type in {
            WorkflowCommandType.EDIT,
            WorkflowCommandType.REQUEST_CHANGES,
        }:
            raise TemporalContractError(
                "temporal_plan_edit_unsupported",
                "Temporal plan edits require a new Hub-owned execution",
            )
        if self._phase in {WorkflowPhase.COMPLETED, WorkflowPhase.FAILED, WorkflowPhase.CANCELLED}:
            raise TemporalContractError("workflow_terminal", "workflow is terminal")
        current_step = self._current_step_id or self._input.steps[0].step_id
        expected_bindings = {
            "tenant_id": self._input.tenant_id,
            "workflow_id": self._input.workflow_id,
            "run_id": self._input.run_id,
            "step_id": current_step,
            "checkpoint_id": self._checkpoint_ref,
            "plan_hash": self._effective_plan_hash,
            "policy_version": self._input.policy_version,
        }
        for field_name, expected_value in expected_bindings.items():
            if str(getattr(command, field_name)) != str(expected_value):
                raise TemporalContractError(
                    f"command_{field_name}_mismatch",
                    "workflow command binding is stale",
                )
        now = workflow.now().timestamp()
        if now < command.issued_at or now >= command.expires_at:
            raise TemporalContractError("command_expired", "workflow command is outside its validity window")

        command_type = command.command_type
        if command_type in {WorkflowCommandType.APPROVE, WorkflowCommandType.REJECT}:
            if self._phase is not WorkflowPhase.WAITING_APPROVAL or current_step not in self._open_gates:
                raise TemporalContractError("approval_gate_not_open", "approval gate is not open")
        elif command_type is WorkflowCommandType.PAUSE:
            if self._phase not in {WorkflowPhase.RUNNING, WorkflowPhase.WAITING_APPROVAL}:
                raise TemporalContractError("workflow_not_pausable", "workflow cannot be paused")
        elif command_type is WorkflowCommandType.RESUME:
            if self._phase is not WorkflowPhase.PAUSED:
                raise TemporalContractError("workflow_not_paused", "workflow is not paused")
            if self._reason_code == "plan_reauthorization_required":
                raise TemporalContractError(
                    "plan_reauthorization_required",
                    "an edited plan requires a new Hub-authorized execution",
                )
        elif command_type is WorkflowCommandType.PARAMETER_UPDATE:
            updates = command.payload.get("parameters")
            if not isinstance(updates, dict) or not updates:
                raise TemporalContractError("parameter_update_required", "parameter update is empty")
            if set(updates) - set(self._input.mutable_parameters):
                raise TemporalContractError("immutable_parameter", "parameter is not mutable")
            merged = {**self._parameters, **updates}
            if len(json.dumps(merged, sort_keys=True, separators=(",", ":")).encode("utf-8")) > 32_768:
                raise TemporalContractError("parameter_update_too_large", "parameter update exceeds its limit")
        elif command_type in {
            WorkflowCommandType.EDIT,
            WorkflowCommandType.REQUEST_CHANGES,
        }:
            if self._active_activity is not None or self._active_activities:
                raise TemporalContractError("plan_edit_activity_running", "an active activity cannot be edited")

    def _apply_command(self, command: WorkflowCommand) -> WorkflowCommandResult:
        command_type = command.command_type
        self._reason_code = ""
        if command_type is WorkflowCommandType.APPROVE:
            self._approved_gates.add(self._current_step_id)
        elif command_type is WorkflowCommandType.REJECT:
            self._fail("approval_rejected")
        elif command_type is WorkflowCommandType.PAUSE:
            self._phase_before_pause = self._phase
            self._transition(WorkflowPhase.PAUSED)
        elif command_type is WorkflowCommandType.RESUME:
            target = self._phase_before_pause
            if target not in {WorkflowPhase.RUNNING, WorkflowPhase.WAITING_APPROVAL}:
                target = WorkflowPhase.RUNNING
            self._transition(target)
        elif command_type is WorkflowCommandType.CANCEL:
            self._transition(WorkflowPhase.CANCELLED)
            self._reason_code = str(command.payload.get("reason") or "cancel_requested")[:256]
            if self._active_activity is not None:
                self._active_activity.cancel()
            for handle in self._active_activities.values():
                handle.cancel()
        elif command_type is WorkflowCommandType.PARAMETER_UPDATE:
            self._parameters.update(dict(command.payload.get("parameters") or {}))
        elif command_type in {
            WorkflowCommandType.EDIT,
            WorkflowCommandType.REQUEST_CHANGES,
        }:
            self._effective_plan_hash = str(command.payload["replacement_plan_hash"])
            self._plan_ref = str(command.payload.get("plan_ref") or "inline-plan")[:256]
            self._plan_revision += 1
            self._phase_before_pause = self._phase
            self._transition(WorkflowPhase.PAUSED)
            self._reason_code = "plan_reauthorization_required"

        self._revision += 1
        self._refresh_checkpoint_ref()
        self._history_event_estimate += 2
        result = WorkflowCommandResult(
            command_id=command.command_id,
            accepted=True,
            revision=self._revision,
            status=self._phase.value,
        )
        self._processed_command_ids[command.command_id] = result
        self._within_limits()
        return result

    def _transition(self, phase: WorkflowPhase) -> None:
        if phase is self._phase:
            return
        self._phase = phase
        self._revision += 1
        self._refresh_checkpoint_ref()
        self._history_event_estimate += 1

    def _refresh_checkpoint_ref(self) -> None:
        workflow_id = self._input.workflow_id if self._input is not None else "uninitialized"
        self._checkpoint_ref = f"temporal:{workflow_id}:{self._revision}"

    def _fail(self, reason_code: str) -> None:
        self._reason_code = str(reason_code or "workflow_failed")[:256]
        self._transition(WorkflowPhase.FAILED)

    def _within_limits(self) -> bool:
        if self._input is None:
            return True
        if self._history_event_estimate > self._input.max_history_events:
            self._fail("history_limit_exceeded")
            return False
        self._state_bytes_estimate = len(
            json.dumps(
                self._status().to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if self._state_bytes_estimate > self._input.max_state_bytes:
            self._fail("state_limit_exceeded")
            return False
        return True

    def _status(self) -> WorkflowStatus:
        workflow_id = self._input.workflow_id if self._input else "uninitialized"
        run_id = self._input.run_id if self._input else "uninitialized"
        return WorkflowStatus(
            workflow_id=workflow_id,
            run_id=run_id,
            status=self._phase.value,
            revision=self._revision,
            current_step_id=self._current_step_id,
            completed_step_ids=tuple(self._completed_step_ids),
            retry_budget_remaining=self._retry_budget_remaining,
            checkpoint_ref=self._checkpoint_ref,
            open_gates=tuple(self._open_gates),
            reason_code=self._reason_code,
            parameters=dict(self._parameters),
            plan_hash=self._effective_plan_hash,
            plan_revision=self._plan_revision,
            plan_ref=self._plan_ref,
            active_step_ids=tuple(sorted(self._active_activities)),
            failed_step_ids=tuple(sorted(self._failed_step_ids)),
        )


__all__ = [
    "ANANTA_WORKFLOW_TYPE",
    "AnantaWorkflow",
    "BOUNDED_PARALLEL_PATCH_ID",
    "N_MINUS_ONE_PATCH_ID",
    "PROBE_WORKFLOW_TYPE",
    "RECOVERY_PROBE_WORKFLOW_TYPE",
    "TemporalProbeWorkflow",
    "TemporalRecoveryProbeWorkflow",
]
