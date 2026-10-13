"""Native result admission in the caller-owned Task transaction."""

import copy
import time
from dataclasses import asdict

from agent.common.native_task_result_binding import NATIVE_RESULT_RECEIPT, is_native_task
from agent.common.pi_task_result_binding import is_pi_task
from agent.ports.native_result_authority import NativeResultAuthorityPort
from agent.services.native_artifact_result_admission import require_registered_native_artifacts
from agent.services.native_result_task_projection import native_result_digest, native_task_command, native_task_result
from ananta_contracts.native_context_bundle import native_context_digest

_TERMINAL = {"completed", "failed", "cancelled"}
_ACTIVE = {"todo", "created", "assigned", "queued", "running", "in_progress"}


class NativeResultCompletionPolicy:
    def __init__(self, *, authority: NativeResultAuthorityPort, clock=time.time):
        self._authority, self._clock = authority, clock

    def apply(self, *, authoritative_task, candidate_task, session):
        old, task = authoritative_task, candidate_task
        if not (is_native_task(old) or is_native_task(task)) or is_pi_task(old) or is_pi_task(task):
            return task  # Pi retains its stricter provider-specific admission policy.
        command = native_task_command(task)
        command_digest = native_context_digest(command.to_dict())
        if old is not None and (
            not is_native_task(old) or command_digest != native_context_digest(native_task_command(old).to_dict())
            or any(getattr(old, field) != getattr(task, field) for field in ("id", "tenant_id", "project_id"))
            or old.assigned_agent_url and old.assigned_agent_url != task.assigned_agent_url
        ):
            raise ValueError("native_result_task_binding_immutable")
        verification = dict(task.verification_status or {})
        previous = (old.verification_status or {}).get(NATIVE_RESULT_RECEIPT) if old is not None else None
        if NATIVE_RESULT_RECEIPT in verification and (
            previous is None
            or native_context_digest(verification[NATIVE_RESULT_RECEIPT]) != native_context_digest(previous)
        ):
            raise ValueError("native_result_receipt_not_worker_owned")
        if previous is not None:
            native_task_result(task, command)
            if (
                not isinstance(previous, dict) or previous.get("command_digest") != command_digest
                or previous.get("result_digest") != native_result_digest(task)
                or native_result_digest(task) != native_result_digest(old)
            ):
                raise ValueError("native_result_receipt_immutable")
            verification[NATIVE_RESULT_RECEIPT] = copy.deepcopy(previous)
            task.verification_status = verification
            return task
        has_result = "native_node_result" in verification or "workflow_adapter_task_result" in verification
        if old is not None and old.status in _TERMINAL and (task.status != old.status or has_result):
            raise ValueError("native_result_terminal_task_immutable")
        if task.status not in _TERMINAL:
            if has_result:
                raise ValueError("native_result_terminal_required")
            return task
        if task.status != "completed" and not has_result:
            return task  # Hub-originated bounded dispatch failure/cancellation.
        if old is None:
            raise ValueError("native_result_active_task_required")
        result = native_task_result(task, command)
        authority = self._authority.require_current(session=session, command=command, task=old, now=self._clock())
        if old.status not in _ACTIVE:
            from agent.services.native_dispatch_recovery_admission import require_native_transport_recovery

            require_native_transport_recovery(session=session, task=old, authority=authority)
        artifacts = require_registered_native_artifacts(
            session=session, task=task, command=command, result=result, authority=authority,
        )
        verification[NATIVE_RESULT_RECEIPT] = {
            "schema": "ananta.native-result-receipt.v1", "classification": "technical_observation",
            "hub_task_id": task.id, "tenant_id": task.tenant_id, "project_id": task.project_id,
            "command_digest": command_digest, "result_digest": native_result_digest(task),
            "authority": asdict(authority),
            **({"artifacts": artifacts} if artifacts else {}),
        }
        task.verification_status = verification
        return task
