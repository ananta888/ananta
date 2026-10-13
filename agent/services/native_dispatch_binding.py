"""Canonical immutable Hub send projection and Worker assignment binding."""

from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_task_execution import native_execution_request


def native_dispatch_binding_digest(task, *, worker_id, worker_url, assignment_revision):
    return native_context_digest({
        "projection": native_execution_request(task.model_dump(mode="json")),
        "worker_id": worker_id, "worker_url": worker_url,
        "assignment_revision": assignment_revision,
    })
