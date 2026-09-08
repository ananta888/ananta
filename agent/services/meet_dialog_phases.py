"""Hub phase coordinator; observational metadata never dispatches or authorizes work."""

import time

from agent.models.meet_dialog_phase import (
    phase_binding,
    projection,
    publication_observation,
    queued,
    transition,
    validate_record,
)
from agent.services.meet_contract import MeetError


class MeetDialogPhases:
    def __init__(self, store, authority, meet, clock=time.time):
        self.store, self.authority, self.meet, self.clock = store, authority, meet, clock

    def queued(self, task_id, tenant, project, context):
        return queued(phase_binding(task_id, tenant, project, context), self._now())

    def _now(self):
        try:
            return int(self.clock() * 1000)
        except (ValueError, TypeError, OverflowError):
            raise MeetError("meet_dialog_phase_clock_invalid", 409) from None

    def _read(self, task_id):
        task = self.store.read(task_id)
        if task is None or task.task_kind != "meet_dialog_session" or getattr(task, "archived", False):
            raise MeetError("meet_dialog_phase_task_inactive", 409)
        execution = task.worker_execution_context or {}
        record = validate_record(
            execution.get("meet_phase"),
            phase_binding(task.id, task.tenant_id, task.project_id, execution.get("meet_dialog", {})),
        )
        return task, record

    def _write(self, task, record, changed):
        if changed != record and not self.store.replace(task, changed):
            raise MeetError("meet_dialog_phase_conflict", 409)
        return changed

    def advance(self, scope, target, state=None):
        self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id)
        legacy = self.store.read(scope.task_id)
        if legacy is not None and "meet_phase" not in (legacy.worker_execution_context or {}):
            return  # Old assignments keep their existing exchange, without invented history.
        task, record = self._read(scope.task_id)
        context = task.worker_execution_context["meet_dialog"]
        if (task.tenant_id, task.project_id, context["lease_id"], context["runtime_id"], context["session_id"]) != (
            scope.tenant_id,
            scope.project_id,
            scope.lease_id,
            scope.runtime_id,
            scope.session_id,
        ):
            raise MeetError("meet_dialog_phase_conflict", 409)
        expected = {
            key: getattr(scope, key)
            for key in (
                "lease_id",
                "runtime_id",
                "session_id",
                "room_id",
                "owner_subject",
                "binding_task_id",
                "deadline",
                "capabilities",
                "chat_mode",
                "audio_mode",
            )
        }
        if scope.avatar_selection is not None:
            expected["avatar_selection"] = scope.avatar_selection
        if scope.voice_selection is not None:
            expected["voice_selection"] = scope.voice_selection
        if record["binding"] != phase_binding(scope.task_id, scope.tenant_id, scope.project_id, expected):
            raise MeetError("meet_dialog_phase_conflict", 409)
        membership = None if state is None else {"session_id": state["lease"]["sessionId"], "peer_id": state["peerId"]}
        # Authorization proves continued membership, not a source stop. Never
        # downgrade an observed publishing phase merely because exchange ran.
        if target == "joined" and record["phase"] == "publishing":
            target = "publishing"
        changed = transition(record, target, self._now(), membership=membership)
        self._write(task, record, changed)
        self.authority.current(scope.task_id, scope.lease_id, scope.runtime_id)

    def stopping(self, task_id, lease_id, runtime_id):
        legacy = self.store.read(task_id)
        if legacy is not None and "meet_phase" not in (legacy.worker_execution_context or {}):
            return
        task, record = self._read(task_id)
        context = task.worker_execution_context["meet_dialog"]
        if (context["lease_id"], context["runtime_id"]) != (lease_id, runtime_id):
            raise MeetError("meet_dialog_phase_conflict", 409)
        if task.status == "in_progress":
            self._write(task, record, transition(record, "stopping", self._now()))

    def inspect(self, principal, project, task_id, *, refresh=False):
        self.authority.binding.require_write_access(principal, project)
        task = self.store.read(task_id)
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.tenant_id != principal.tenant_id
            or task.project_id != project
        ):
            raise MeetError("meet_dialog_not_found", 404)
        context = (task.worker_execution_context or {}).get("meet_dialog", {})
        if context.get("owner_subject") != principal.subject_id:
            raise MeetError("meet_dialog_owner_required", 403)
        task, record = self._read(task_id)
        # The second read must not cross an ownership/scope replacement.
        current = (task.worker_execution_context or {}).get("meet_dialog", {})
        if (task.tenant_id, task.project_id, current) != (principal.tenant_id, project, context):
            raise MeetError("meet_dialog_phase_conflict", 409)
        ids = task_id, context["lease_id"], context["runtime_id"]
        if task.status == "in_progress":
            scope = self.authority.current(*ids)
            if refresh:
                if record["phase"] not in {"joined", "publishing"} or record["membership"] is None:
                    raise MeetError("meet_dialog_phase_not_joined", 409)
                state = self.meet.observe(*ids, record["membership"]["session_id"])
                membership = {"session_id": state["lease"]["sessionId"], "peer_id": state["peerId"]}
                observation = publication_observation(state, self._now(), scope.deadline)
                changed = transition(
                    record,
                    "publishing" if observation["sources"] else "joined",
                    self._now(),
                    membership=membership,
                    observation=observation,
                )
                self._write(task, record, changed)
                self.authority.current(*ids)
                record = changed
        elif refresh:
            raise MeetError("meet_dialog_phase_task_inactive", 409)
        return projection(task_id, task.status, record, self._now())
