"""Dispatch only to the destination already admitted in the Hub task binding."""

import time
from copy import deepcopy
from types import MappingProxyType
from typing import Protocol

from agent.models.meet_role_assignment import publisher_origin
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment


class DialogWorkerPort(Protocol):
    def start_dialog(self, assignment: dict) -> dict: ...


class MeetDialogWorkerRouter:
    def __init__(
        self, authority, tasks, workers: dict[str, DialogWorkerPort], default, clock=time.time, *, capacity=None
    ):
        if not isinstance(workers, dict) or not 1 <= len(workers) <= 8 or default not in workers:
            raise ValueError("meet_dialog_publishers_invalid")
        for origin in workers:
            publisher_origin(origin)
        self.authority, self.tasks, self.clock = authority, tasks, clock
        self.workers, self.default = MappingProxyType(dict(workers)), default
        self.capacity = capacity

    def start_dialog(self, assignment):
        validate_assignment(assignment, self.clock())
        ids = tuple(assignment[k] for k in ("task_id", "lease_id", "runtime_id"))
        scope = self.authority.current(*ids)
        task = self.tasks.get_by_id(scope.task_id)
        if task is None:
            raise MeetError("meet_dialog_publisher_binding_denied", 403)
        bound_origin = task.assigned_agent_url
        role = deepcopy((task.worker_execution_context or {}).get("meet_role_assignment"))
        if role is not None:
            if not isinstance(role, dict) or bound_origin != role.get("publisher_url"):
                raise MeetError("meet_dialog_publisher_binding_denied", 403)
            destination = role["publisher_url"]
        elif getattr(task, "organization_id", None) or getattr(task, "role_slot_id", None):
            raise MeetError("meet_dialog_publisher_binding_denied", 403)
        else:
            destination = self.default
        worker = self.workers.get(destination)
        if (
            worker is None
            or any(
                assignment[key] != getattr(scope, key)
                for key in (
                    "task_id",
                    "lease_id",
                    "runtime_id",
                    "session_id",
                    "tenant_id",
                    "project_id",
                    "deadline",
                    "audio_mode",
                )
            )
            or set(assignment["capabilities"]) != set(scope.capabilities)
            or any(assignment["meeting"][key] != getattr(scope, key) for key in ("origin", "room_id"))
        ):
            raise MeetError("meet_dialog_publisher_binding_denied", 403)
        if (
            (assignment.get("avatar_images") is True) != (scope.avatar_selection is not None)
            or (assignment.get("voice_profiles") is True) != (scope.voice_selection is not None)
            or (assignment.get("avatar_videos") is True) != scope.avatar_videos
            or assignment.get("initial_persona") != scope.initial_persona
            or (assignment.get("browser_workspace") is True) != getattr(scope, "browser_workspace", False)
            or any(
                (assignment.get(field) is True) != getattr(scope, field, False)
                for field in ("media_timing", "speaker_floor", "reconnect")
            )
            or assignment.get("audio_profile")
            != (scope.audio_profile.projection() if getattr(scope, "audio_profile", None) is not None else None)
        ):
            raise MeetError("meet_dialog_publisher_binding_denied", 403)

        def operation():
            return self._send_exact(scope, assignment, bound_origin, role, worker)

        if self.capacity is not None:
            return self.capacity.dispatch(scope, destination, operation)
        return operation()

    def _send_exact(self, scope, assignment, bound_origin, role, worker):
        ids = scope.task_id, scope.lease_id, scope.runtime_id
        # Run after any capacity wait: fence a concurrent destination/role edit;
        # selection never runs again here and an uncertain send is never retried.
        if self.authority.current(*ids) != scope:
            raise MeetError("meet_dialog_publisher_binding_denied", 403)
        current = self.tasks.get_by_id(scope.task_id)
        if (
            current is None
            or current.assigned_agent_url != bound_origin
            or (current.worker_execution_context or {}).get("meet_role_assignment") != role
        ):
            raise MeetError("meet_dialog_publisher_binding_denied", 403)
        return worker.start_dialog(assignment)
