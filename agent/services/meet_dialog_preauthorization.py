"""Narrow Hub assignment adapter; no operator I/O or Worker authority."""

from typing import Protocol

from agent.models.meet_preauthorization_binding import assignment_projection


class PreauthorizationStore(Protocol):
    def reserve(self, assignment): ...
    def require_current(self, assignment, binding): ...


class MeetDialogPreauthorization:
    def __init__(self, store: PreauthorizationStore):
        self.store = store

    def reserve(self, task_id, tenant, project, origin, context):
        return self.store.reserve(assignment_projection(task_id, tenant, project, origin, context))

    def require_current(self, task_id, tenant, project, origin, context, binding):
        self.store.require_current(assignment_projection(task_id, tenant, project, origin, context), binding)
