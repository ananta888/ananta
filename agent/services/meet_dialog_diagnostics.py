"""Admission and owner-scoped observation reads; never execution authorization."""

import time
from typing import Protocol

from agent.models.meet_dialog_diagnostics import TERMINAL, record_for, task_binding, timestamp_ms, validate_record
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog_diagnostics import validate_request


class DiagnosticLedger(Protocol):
    def read_task(self, task_id): ...
    def read_record(self, task_id): ...
    def append(self, snapshot, record, valid_until_ms): ...


class DiagnosticAccess(Protocol):
    def require_write_access(self, principal, project, parent=""): ...


class MeetDialogDiagnostics:
    def __init__(self, ledger: DiagnosticLedger, access: DiagnosticAccess, *, clock=time.time):
        self.ledger, self.access, self.clock = ledger, access, clock

    def _task(self, task_id):
        value = self.ledger.read_task(task_id)
        if value is None:
            raise MeetError("meet_dialog_not_found", 404)
        try:
            task_binding(value)
        except (ValueError, TypeError):
            raise MeetError("meet_dialog_diagnostics_binding_invalid", 409) from None
        return value

    def accept(self, payload):
        try:
            payload = validate_request(payload, self.clock())
        except ValueError:
            raise MeetError("meet_dialog_diagnostics_request_invalid") from None
        task = self._task(payload["task_id"])
        context = task["worker_execution_context"]["meet_dialog"]
        if task["status"] not in TERMINAL:
            raise MeetError("meet_dialog_diagnostics_not_terminal", 409)
        if any(context.get(k) != payload[k] for k in ("lease_id", "runtime_id")):
            raise MeetError("meet_dialog_diagnostics_binding_denied", 403)
        limit = min(context["deadline"] + 30, payload["sent_at"] + 10) * 1000
        try:
            now = timestamp_ms(self.clock())
            if now >= limit:
                raise MeetError("meet_dialog_diagnostics_expired", 409)
            self.ledger.append(task, record_for(task, payload["observation"], now), limit)
        except MeetError:
            raise
        except ValueError:
            raise MeetError("meet_dialog_diagnostics_record_invalid", 409) from None
        return {"schema": "ananta.meet-dialog-diagnostics-accepted.v1", "nonce": payload["nonce"], "accepted": True}

    def inspect(self, principal, project, task_id):
        self.access.require_write_access(principal, project)
        task = self._task(task_id)
        if (task["tenant_id"], task["project_id"]) != (principal.tenant_id, project):
            raise MeetError("meet_dialog_not_found", 404)
        context = task["worker_execution_context"]["meet_dialog"]
        if context.get("owner_subject") != principal.subject_id:
            raise MeetError("meet_dialog_owner_required", 403)
        record = self.ledger.read_record(task_id)
        try:
            if record is not None:
                record = validate_record(record, task)
        except ValueError:
            raise MeetError("meet_dialog_diagnostics_record_invalid", 409) from None
        self.access.require_write_access(principal, project, context.get("binding_task_id", ""))
        if self._task(task_id) != task:
            raise MeetError("meet_dialog_diagnostics_conflict", 409)
        return {
            "schema": "ananta.meet-dialog-diagnostics.v1",
            "task_id": task_id,
            "observation_status": "recorded" if record is not None else "missing",
            "classification": "unverified_worker_observation",
            "observation": record["observation"] if record is not None else None,
            "recorded_at_ms": record["recorded_at_ms"] if record is not None else None,
            "hub_task_status": task["status"],
            "hub_control_revision_at_receipt": record["hub_control_revision_at_receipt"]
            if record is not None
            else None,
        }
