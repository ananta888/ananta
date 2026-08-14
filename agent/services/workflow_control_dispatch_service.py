"""Hub-owned dispatcher for persisted workflow start and command intents."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.services.workflow_backend import WORKFLOW_STATUS_SCHEMA
from agent.services.workflow_backend_durable_run_adapter import (
    DURABLE_RUN_SIGNAL_SCHEMA,
)
from agent.services.workflow_configured_bridge_reconciler import (
    authoritative_runtime_status,
)
from agent.services.workflow_control_bindings import (
    WorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_command_receipts import (
    WorkflowControlCommandRejectedError,
)
from agent.services.workflow_control_command_verification import (
    HubVerifiedDurableCommandPort,
)
from agent.services.workflow_control_dispatch_intents import (
    DISPATCH_KIND_COMMAND,
    DISPATCH_KIND_START,
    DISPATCH_STATE_OBSERVATION_PENDING,
    DISPATCH_STATE_REJECTED,
    WorkflowControlDispatchIntent,
    WorkflowControlDispatchIntentStore,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.ports import DurableRunInfrastructurePort
from ananta_contracts.temporal_workflow import (
    COMMAND_RESULT_SCHEMA as _COMMAND_RESULT_SCHEMA,
)
from ananta_contracts.temporal_workflow import STATUS_SCHEMA as _TEMPORAL_STATUS_SCHEMA

COMMAND_OBSERVATION_PENDING = "workflow_control_command_observation_pending"
START_OBSERVATION_PENDING = "workflow_control_start_observation_pending"

_COMMAND_RESULT_KEYS = frozenset({"schema", "command_id", "accepted", "revision", "status", "reason_code"})
_COMMAND_STATUSES = frozenset({"created", "running", "paused", "waiting_approval", "completed", "failed", "cancelled"})


class WorkflowControlDispatchService:
    """Dispatch immutable Hub intents and materialize authoritative status."""

    def __init__(
        self,
        *,
        runtime_id: str,
        bindings: WorkflowControlBindingStore,
        intents: WorkflowControlDispatchIntentStore,
        durable_runs: DurableRunInfrastructurePort,
        commands: HubVerifiedDurableCommandPort,
        project: Callable[[WorkflowControlRunBinding, dict[str, Any]], None],
        clock: Any = time.time,
        owner_id: str = "",
        lease_seconds: float = 30.0,
        retry_seconds: float = 1.0,
    ) -> None:
        self._runtime_id = str(runtime_id)
        self._bindings = bindings
        self._intents = intents
        self._durable_runs = durable_runs
        self._commands = commands
        self._project = project
        self._clock = clock
        self._owner_id = str(owner_id or f"workflow-dispatch-{uuid.uuid4().hex}")
        self._lease_seconds = max(1.0, min(float(lease_seconds), 300.0))
        self._retry_seconds = max(0.1, min(float(retry_seconds), 60.0))

    def stage_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
    ) -> dict[str, Any]:
        # Signature and expiry are checked before staging.  The production
        # store consumes the nonce atomically with the outbox/binding claim;
        # leased replays use signature-only verification plus Temporal's ID.
        self._commands.verify_for_staging(
            tenant_id=binding.tenant_id,
            run_id=binding.workflow_id,
            command={
                "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                "command": command.to_dict(),
            },
        )
        intent = self._intents.stage_command(binding=binding, command=command)
        status = self._dispatch(intent.intent_id)
        if status is None:
            raise RuntimeError(COMMAND_OBSERVATION_PENDING)
        return status

    def stage_start(
        self,
        *,
        binding: WorkflowControlRunBinding,
        start_command: dict[str, Any],
        request_id: str,
        pending_status: dict[str, Any],
    ) -> dict[str, Any]:
        intent = self._intents.stage_start(
            binding=binding,
            start_command=start_command,
            request_id=request_id,
            pending_status=pending_status,
        )
        if intent.state == "completed":
            status = self._bindings.last_status(binding.workflow_id)
            if status is not None:
                return status
        status = self._dispatch(intent.intent_id)
        if status is None:
            raise RuntimeError(START_OBSERVATION_PENDING)
        return status

    def reconcile_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        intent = self._intents.get_active(workflow_id)
        if intent is None:
            return self._bindings.last_status(workflow_id)
        return self._dispatch(intent.intent_id)

    def retry_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command_id: str,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve an explicit client idempotency key without reissuing it."""

        intent = self._intents.get(command_id)
        if intent is None:
            return None
        if (
            intent.kind != DISPATCH_KIND_COMMAND
            or intent.tenant_id != binding.tenant_id
            or intent.workflow_id != binding.workflow_id
            or intent.run_id != binding.run_id
        ):
            raise RuntimeError("workflow_control_dispatch_stage_conflict")
        command = intent.command
        if (
            command.command_type != str(command_type)
            or command.payload != dict(payload)
            or command.actor_id != binding.subject_id
        ):
            raise RuntimeError("workflow_control_dispatch_stage_conflict")
        if intent.state == DISPATCH_STATE_REJECTED:
            raise WorkflowControlCommandRejectedError(intent.last_error)
        if intent.state != "completed":
            self.reconcile_workflow(binding.workflow_id)
            intent = self._intents.get(command_id)
        if intent is None or intent.state != "completed":
            raise RuntimeError(COMMAND_OBSERVATION_PENDING)
        status = self._bindings.last_status(binding.workflow_id)
        if status is None:
            raise RuntimeError(COMMAND_OBSERVATION_PENDING)
        return status

    def drain(self, *, limit: int = 100) -> dict[str, Any]:
        claimed = self._intents.claim_due(
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
            limit=max(1, min(int(limit), 1000)),
        )
        processed = 0
        failures: list[dict[str, str]] = []
        for intent in claimed:
            try:
                status = self._process_claimed(intent)
            except WorkflowControlCommandRejectedError:
                processed += 1
                continue
            if status is None:
                failures.append(
                    {
                        "workflow_id": intent.workflow_id,
                        "intent_id": intent.intent_id,
                        "reason_code": (
                            COMMAND_OBSERVATION_PENDING
                            if intent.kind == DISPATCH_KIND_COMMAND
                            else START_OBSERVATION_PENDING
                        ),
                    }
                )
            else:
                processed += 1
        return {
            "runtime_id": self._runtime_id,
            "processed": processed,
            "failed": failures,
        }

    def _dispatch(self, intent_id: str) -> dict[str, Any] | None:
        claimed = self._intents.claim(
            intent_id,
            owner_id=self._owner_id,
            lease_seconds=self._lease_seconds,
        )
        if claimed is None:
            return None
        return self._process_claimed(claimed)

    def _process_claimed(
        self,
        intent: WorkflowControlDispatchIntent,
    ) -> dict[str, Any] | None:
        try:
            binding = self._binding(intent)
            if intent.kind == DISPATCH_KIND_COMMAND:
                current = self._dispatch_command(intent, binding=binding)
            elif intent.kind == DISPATCH_KIND_START:
                current = self._dispatch_start(intent)
            else:  # domain DTO already rejects this; retain a defensive fence.
                raise ValueError("workflow_control_dispatch_kind_invalid")
            observed = self._mapping(
                self._durable_runs.describe(
                    tenant_id=intent.tenant_id,
                    run_id=intent.workflow_id,
                )
            )
            status = authoritative_runtime_status(
                observed,
                binding=binding,
                previous=self._bindings.last_status(binding.workflow_id),
                runtime_id=self._runtime_id,
            )
            if current.kind == DISPATCH_KIND_COMMAND:
                _assert_acknowledged_observation(status, intent=current)
            # Read-model evidence is part of durable completion.  A failed
            # projection keeps the intent retryable; Describe can replay it
            # without reapplying an observation-pending command.
            self._project(binding, status)
            self._intents.complete(
                current.intent_id,
                owner_id=self._owner_id,
                status=status,
            )
            return status
        except WorkflowControlCommandRejectedError:
            raise
        except Exception as exc:  # ambiguity is durable and retried by the Hub
            self._release_after_failure(intent, exc)
            return None

    def _dispatch_command(
        self,
        intent: WorkflowControlDispatchIntent,
        *,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowControlDispatchIntent:
        if intent.phase == DISPATCH_STATE_OBSERVATION_PENDING:
            return intent
        command = intent.command
        if (
            command.tenant_id != binding.tenant_id
            or command.workflow_id != binding.workflow_id
            or command.run_id != binding.run_id
            or command.plan_hash != binding.plan_hash
            or command.policy_version != binding.policy_version
        ):
            raise PermissionError("workflow_control_dispatch_binding_mismatch")
        response = self._mapping(
            self._durable_runs.signal_persisted(
                tenant_id=intent.tenant_id,
                run_id=intent.workflow_id,
                command={
                    "schema": DURABLE_RUN_SIGNAL_SCHEMA,
                    "command": command.to_dict(),
                },
            )
        )
        if response.get("accepted") is False:
            reason_code, rejected_revision, rejected_status = _validate_rejected_command_ack(
                response,
                command=command,
            )
            observed = self._mapping(
                self._durable_runs.describe(
                    tenant_id=intent.tenant_id,
                    run_id=intent.workflow_id,
                )
            )
            status = authoritative_runtime_status(
                observed,
                binding=binding,
                previous=self._bindings.last_status(binding.workflow_id),
                runtime_id=self._runtime_id,
            )
            _assert_rejected_observation(
                status,
                acknowledgement_revision=rejected_revision,
                acknowledgement_status=rejected_status,
            )
            self._project(binding, status)
            self._intents.reject(
                intent.intent_id,
                owner_id=self._owner_id,
                reason_code=reason_code,
                status=status,
            )
            log_audit(
                "workflow_control_command_rejected",
                {
                    "tenant_id": intent.tenant_id,
                    "workflow_id": intent.workflow_id,
                    "run_id": intent.run_id,
                    "command_id": intent.intent_id,
                    "runtime": self._runtime_id,
                    "reason_code": reason_code,
                },
            )
            raise WorkflowControlCommandRejectedError(reason_code)
        # The persisted command ID is also the Temporal Update ID, therefore a
        # missing or malformed ACK remains safely replayable.  Only a strictly
        # bound positive ACK advances the outbox to observation-only recovery.
        try:
            revision, status = _validate_command_ack(response, command=command)
        except (TypeError, ValueError):
            boundary_revision = _command_result_boundary_revision(
                response,
                command=command,
            )
            if boundary_revision is not None:
                self._intents.acknowledge(
                    intent.intent_id,
                    owner_id=self._owner_id,
                    acknowledgement_revision=boundary_revision,
                )
            raise
        return self._intents.acknowledge(
            intent.intent_id,
            owner_id=self._owner_id,
            acknowledgement_revision=revision,
            acknowledgement_status=status,
        )

    def _dispatch_start(
        self,
        intent: WorkflowControlDispatchIntent,
    ) -> WorkflowControlDispatchIntent:
        if intent.phase == DISPATCH_STATE_OBSERVATION_PENDING:
            return intent
        # The workflow ID inside the validated start command is Temporal's
        # idempotency anchor.  ACK contents are not treated as runtime status.
        response = self._mapping(self._durable_runs.start(intent.start_command))
        if (
            response.get("schema") != WORKFLOW_STATUS_SCHEMA
            or response.get("backend") != self._runtime_id
            or response.get("workflow_id") != intent.workflow_id
            or response.get("status") != "running"
        ):
            raise RuntimeError("workflow_control_start_ack_invalid")
        # Do not convert a generic/degraded start response into an observation
        # phase.  Completion is driven only by the subsequent authoritative
        # Describe.  If Describe fails or says not-found, retrying Start with
        # the stable workflow ID is idempotent and can recover a pre-send loss.
        return intent

    def _binding(
        self,
        intent: WorkflowControlDispatchIntent,
    ) -> WorkflowControlRunBinding:
        binding = self._bindings.get(intent.workflow_id)
        if binding is None or any(
            (
                binding.tenant_id != intent.tenant_id,
                binding.workflow_id != intent.workflow_id,
                binding.run_id != intent.run_id,
                binding.runtime_id != self._runtime_id,
            )
        ):
            raise PermissionError("workflow_control_dispatch_binding_mismatch")
        return binding

    def _release_after_failure(
        self,
        intent: WorkflowControlDispatchIntent,
        cause: Exception,
    ) -> None:
        reason = COMMAND_OBSERVATION_PENDING if intent.kind == DISPATCH_KIND_COMMAND else START_OBSERVATION_PENDING
        try:
            self._intents.release(
                intent.intent_id,
                owner_id=self._owner_id,
                reason_code=reason,
                retry_at=float(self._clock()) + self._retry_seconds,
            )
        except Exception as persistence_exc:
            log_audit(
                reason,
                {
                    "tenant_id": intent.tenant_id,
                    "workflow_id": intent.workflow_id,
                    "run_id": intent.run_id,
                    "intent_id": intent.intent_id,
                    "stage": "retry_persistence",
                    "error_type": type(persistence_exc).__name__,
                },
            )
            return
        log_audit(
            reason,
            {
                "tenant_id": intent.tenant_id,
                "workflow_id": intent.workflow_id,
                "run_id": intent.run_id,
                "intent_id": intent.intent_id,
                "stage": intent.phase,
                "error_type": type(cause).__name__,
            },
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("workflow_control_dispatch_response_invalid")
        return dict(value)


def _validate_command_ack(
    raw: dict[str, Any],
    *,
    command: SignedWorkflowCommand,
) -> tuple[int, str]:
    if frozenset(raw) != _COMMAND_RESULT_KEYS:
        raise ValueError("workflow_control_command_ack_shape_invalid")
    if raw.get("schema") != _COMMAND_RESULT_SCHEMA:
        raise ValueError("workflow_control_command_ack_schema_invalid")
    if raw.get("command_id") != command.command_id:
        raise ValueError("workflow_control_command_ack_identity_mismatch")
    if raw.get("accepted") is not True:
        raise ValueError("workflow_control_command_ack_rejected")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= command.expected_revision:
        raise ValueError("workflow_control_command_ack_revision_invalid")
    status = _ack_text(raw.get("status"), field_name="status", maximum=64).lower()
    if status not in _COMMAND_STATUSES:
        raise ValueError("workflow_control_command_ack_status_invalid")
    _ack_text(
        raw.get("reason_code"),
        field_name="reason_code",
        maximum=512,
        allow_empty=True,
    )
    return revision, status


def _command_result_boundary_revision(
    raw: dict[str, Any],
    *,
    command: SignedWorkflowCommand,
) -> int | None:
    """Return only mutation-boundary evidence, never an accepted ACK status."""

    revision = raw.get("revision")
    if (
        raw.get("schema") != _COMMAND_RESULT_SCHEMA
        or raw.get("command_id") != command.command_id
        or raw.get("accepted") is not True
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision <= command.expected_revision
    ):
        return None
    return revision


def _validate_rejected_command_ack(
    raw: dict[str, Any],
    *,
    command: SignedWorkflowCommand,
) -> tuple[str, int, str]:
    if frozenset(raw) != _COMMAND_RESULT_KEYS:
        raise ValueError("workflow_control_command_ack_shape_invalid")
    if raw.get("schema") != _COMMAND_RESULT_SCHEMA:
        raise ValueError("workflow_control_command_ack_schema_invalid")
    if raw.get("command_id") != command.command_id:
        raise ValueError("workflow_control_command_ack_identity_mismatch")
    if raw.get("accepted") is not False:
        raise ValueError("workflow_control_command_ack_rejection_invalid")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < command.expected_revision:
        raise ValueError("workflow_control_command_ack_revision_invalid")
    status = _ack_text(raw.get("status"), field_name="status", maximum=64).lower()
    if status not in _COMMAND_STATUSES:
        raise ValueError("workflow_control_command_ack_status_invalid")
    reason = _ack_text(
        raw.get("reason_code"),
        field_name="reason_code",
        maximum=64,
    )
    if not reason[0].isalpha() or any(not character.isalnum() and character != "_" for character in reason):
        raise ValueError("workflow_control_command_ack_reason_code_invalid")
    return reason, revision, status


def _assert_rejected_observation(
    status: dict[str, Any],
    *,
    acknowledgement_revision: int,
    acknowledgement_status: str,
) -> None:
    source = status.get("source_observation")
    if not isinstance(source, dict) or source.get("schema") != _TEMPORAL_STATUS_SCHEMA:
        raise ValueError("workflow_control_command_rejection_observation_schema_invalid")
    revision = source.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < acknowledgement_revision
        or (revision == acknowledgement_revision and source.get("status") != acknowledgement_status)
    ):
        raise ValueError("workflow_control_command_rejection_observation_conflict")


def _assert_acknowledged_observation(
    status: dict[str, Any],
    *,
    intent: WorkflowControlDispatchIntent,
) -> None:
    source = status.get("source_observation")
    if not isinstance(source, dict) or source.get("schema") != _TEMPORAL_STATUS_SCHEMA:
        raise ValueError("workflow_control_command_observation_schema_invalid")
    revision = source.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < intent.acknowledgement_revision:
        raise ValueError("workflow_control_command_observation_revision_stale")
    if (
        revision == intent.acknowledgement_revision
        and intent.acknowledgement_status
        and source.get("status") != intent.acknowledgement_status
    ):
        raise ValueError("workflow_control_command_observation_status_conflict")


def _ack_text(
    raw: Any,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if (
        not isinstance(raw, str)
        or raw != raw.strip()
        or len(raw) > maximum
        or (not raw and not allow_empty)
        or any(not character.isprintable() or character in {"\x00", "\x7f"} for character in raw)
    ):
        raise ValueError(f"workflow_control_command_ack_{field_name}_invalid")
    return raw


__all__ = [
    "COMMAND_OBSERVATION_PENDING",
    "START_OBSERVATION_PENDING",
    "WorkflowControlDispatchService",
]
