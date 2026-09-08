"""Ordinary Hub TaskQueue persistence for one parent-bound public browser task."""

from copy import deepcopy

from agent.models.meet_browser_state import browser_state, initial_browser_state
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import organization_tuple
from ananta_contracts.meet_browser_workspace import validate_browser_job


class HubBrowserTasks:
    def __init__(self, tasks, *, ingest=None, compare_and_set=None):
        self.tasks, self.ingest, self.compare_and_set = tasks, ingest, compare_and_set

    def _cas(self, *args, **kwargs):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        return (self.compare_and_set or compare_and_set_local_task_status)(*args, **kwargs)

    def get_parent(self, task_id):
        return self.tasks.get_by_id(task_id)

    def parent(self, scope):
        task = self.get_parent(scope.task_id)
        if task is None:
            raise MeetError("meet_browser_parent_inactive", 403)
        value = (task.worker_execution_context or {}).get("meet_dialog", {})
        if (
            task.task_kind != "meet_dialog_session"
            or task.status != "in_progress"
            or getattr(task, "archived", False)
            or task.tenant_id != scope.tenant_id
            or task.project_id != scope.project_id
            or (value.get("lease_id"), value.get("runtime_id"), value.get("session_id"), value.get("owner_subject"))
            != (scope.lease_id, scope.runtime_id, scope.session_id, scope.owner_subject)
            or value.get("browser_workspace") is not True
        ):
            raise MeetError("meet_browser_parent_inactive", 403)
        return task

    def read(self, scope):
        parent = self.parent(scope)
        return browser_state((parent.worker_execution_context or {}).get("meet_browser", initial_browser_state()))

    def replace(self, scope, expected, changed):
        expected, changed = browser_state(expected), browser_state(changed)
        if changed["revision"] != expected["revision"] + 1:
            return False
        parent = self.parent(scope)
        context = deepcopy(parent.worker_execution_context)
        if context.get("meet_browser", initial_browser_state()) != expected:
            return False
        inherited = organization_tuple(parent)
        return self._cas(
            parent.id,
            "in_progress",
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_dialog_session"
                and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id
                and row.worker_execution_context == context
                and row.assigned_agent_url == parent.assigned_agent_url
                and row.parent_task_id == parent.parent_task_id
                and organization_tuple(row) == inherited
            ),
            worker_execution_context=context | {"meet_browser": changed},
            event_type="meet_browser_control_changed",
            event_actor=scope.owner_subject,
            event_details={"revision": changed["revision"], "mode": changed["mode"]},
        )

    def create(self, scope, job):
        from agent.services.task_queue_service import get_task_queue_service

        job = validate_browser_job(job)
        parent = self.parent(scope)
        if self.read(scope)["job"] != job:
            raise MeetError("meet_browser_reservation_changed", 409)
        inherited = organization_tuple(parent)
        ingest = self.ingest or get_task_queue_service().ingest_task
        ingest(
            task_id=job["task_id"],
            status="in_progress",
            title="Authorized public browser workspace",
            description="Bounded read-only public document; sanitized view only, no content artifacts.",
            created_by=scope.owner_subject,
            source="meet_browser",
            team_id=inherited.get("team_id"),
            event_type="meet_browser_ingested",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "meet_browser_workspace",
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                **{key: value for key, value in inherited.items() if key != "team_id"},
                "parent_task_id": scope.task_id,
                "assigned_agent_url": parent.assigned_agent_url,
                "required_capabilities": ["meet_browser_workspace"],
                "worker_execution_context": self._execution(scope, job),
            },
        )

    @staticmethod
    def _execution(scope, job):
        return {"meet_browser_job": job, "parent_dispatch": scope.lease_id, "runtime_id": scope.runtime_id}

    def require_job(self, scope, job):
        job = validate_browser_job(job)
        parent, child = self.parent(scope), self.tasks.get_by_id(job["task_id"])
        if self.read(scope)["job"] != job or child is None or not self._child_matches(child, parent, scope, job):
            raise MeetError("meet_browser_task_inactive", 403)
        return child

    def child_status(self, scope, job):
        child = self.tasks.get_by_id(job["task_id"])
        if (
            child is None
            or child.task_kind != "meet_browser_workspace"
            or (child.parent_task_id, child.tenant_id, child.project_id, child.worker_execution_context)
            != (scope.task_id, scope.tenant_id, scope.project_id, self._execution(scope, job))
        ):
            return "unavailable"
        return child.status if child.status in {"in_progress", "completed", "failed", "cancelled"} else "unavailable"

    def _child_matches(self, child, parent, scope, job):
        return (
            child.task_kind == "meet_browser_workspace"
            and child.status == "in_progress"
            and not getattr(child, "archived", False)
            and child.parent_task_id == scope.task_id
            and child.tenant_id == scope.tenant_id
            and child.project_id == scope.project_id
            and child.assigned_agent_url == parent.assigned_agent_url
            and organization_tuple(child) == organization_tuple(parent)
            and child.worker_execution_context == self._execution(scope, job)
        )

    def finish(self, scope, job, status):
        if status not in {"completed", "cancelled", "failed", "timeout"}:
            raise ValueError("meet_browser_terminal_invalid")
        job = validate_browser_job(job)
        # Use the existing Task state machine; timeout is a bounded failure
        # reason, not an unsupported new terminal transition.
        terminal = "failed" if status == "timeout" else status
        # Cleanup also works after parent completion; it cannot publish or revive.
        return self._cas(
            job["task_id"],
            terminal,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_browser_workspace"
                and row.parent_task_id == scope.task_id
                and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id
                and row.worker_execution_context == self._execution(scope, job)
            ),
            event_type="meet_browser_finished",
            event_actor="hub",
            event_details={"status": terminal, **({"reason": "deadline_exceeded"} if status == "timeout" else {})},
        )
