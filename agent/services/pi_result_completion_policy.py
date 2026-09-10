"""Hub-owned Pi terminal admission; no evidence issuance or Worker scheduling."""

from __future__ import annotations

import copy
import time
from dataclasses import asdict

from agent.common.pi_task_result_binding import PI_RESULT_RECEIPT, is_pi_task
from agent.ports.pi_result_authority import PiResultAuthorityPort
from agent.services.pi_result_receipt import require_pi_result_receipt
from agent.services.pi_result_task_projection import pi_task_command, pi_task_result_candidate
from ananta_contracts.native_context_bundle import native_context_digest

_TERMINAL = {"completed", "failed", "cancelled"}
_ACTIVE = {"todo", "created", "assigned", "queued", "running", "in_progress"}


class PiResultCompletionPolicy:
    def __init__(self, *, authority: PiResultAuthorityPort, clock=time.time):
        self._authority, self._clock = authority, clock

    def apply(self, *, authoritative_task, candidate_task, session):
        old, task = authoritative_task, candidate_task
        if not is_pi_task(old) and not is_pi_task(task):
            return task
        command = pi_task_command(task)
        command_digest = native_context_digest(command.to_dict())
        if old is not None:
            if (
                not is_pi_task(old) or command_digest != native_context_digest(pi_task_command(old).to_dict())
                or any(getattr(old, field) != getattr(task, field) for field in ("id", "tenant_id", "project_id"))
                or (old.assigned_agent_url and old.assigned_agent_url != task.assigned_agent_url)
            ):
                raise ValueError("pi_native_result_task_binding_immutable")
        verification = dict(task.verification_status or {})
        old_receipt = (old.verification_status or {}).get(PI_RESULT_RECEIPT) if old is not None else None
        if PI_RESULT_RECEIPT in verification and (
            old_receipt is None
            or native_context_digest(verification[PI_RESULT_RECEIPT]) != native_context_digest(old_receipt)
        ):
            raise ValueError("pi_native_result_receipt_not_worker_owned")
        has_result = "workflow_adapter_task_result" in verification or "native_node_result" in verification
        if old_receipt is not None:
            if not isinstance(old_receipt, dict):
                raise ValueError("pi_native_result_receipt_invalid")
            candidate = pi_task_result_candidate(task, command)
            previous = pi_task_result_candidate(old, command)
            require_pi_result_receipt(task=old, command=command, candidate=previous)
            if (
                candidate.digest != previous.digest or task.status != old.status
                or old_receipt.get("command_digest") != command_digest
                or old_receipt.get("result_digest") != candidate.digest
            ):
                raise ValueError("pi_native_result_receipt_immutable")
            verification[PI_RESULT_RECEIPT] = copy.deepcopy(old_receipt)
            task.verification_status = verification
            return task  # Exact replay does not renew authority or publish evidence.
        if old is not None and old.status in _TERMINAL and task.status != old.status:
            raise ValueError("pi_native_result_terminal_task_immutable")
        if task.status not in _TERMINAL:
            if has_result:
                raise ValueError("pi_native_result_terminal_required")
            return task
        if task.status != "completed" and not has_result:
            return task  # Hub-owned bounded dispatch/cancellation failure, not a Worker success.
        if old is None or old.status not in _ACTIVE:
            raise ValueError("pi_native_result_active_task_required")
        candidate = pi_task_result_candidate(task, command)
        authority = self._authority.require_current(session=session, command=command, task=old, now=self._clock())
        verification[PI_RESULT_RECEIPT] = {
            "schema": "ananta.pi-native-result-receipt.v1", "classification": "technical_observation",
            "hub_task_id": task.id, "tenant_id": task.tenant_id, "project_id": task.project_id,
            "command_digest": command_digest, "result_digest": candidate.digest,
            "authority": asdict(authority),
        }
        task.verification_status = verification
        require_pi_result_receipt(task=task, command=command, candidate=candidate)
        return task
