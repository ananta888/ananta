"""Hub TaskQueue reservations for bounded analysis; no raw frames or results stored."""

from copy import deepcopy

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_lifecycle import organization_tuple
from ananta_contracts.meet_visual_receive import validate_visual_job


class HubVisualTasks:
    def __init__(self, tasks, *, ingest=None, compare_and_set=None):
        self.tasks, self.ingest, self.compare_and_set = tasks, ingest, compare_and_set

    def _cas(self, *args, **kwargs):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        return (self.compare_and_set or compare_and_set_local_task_status)(*args, **kwargs)

    def parent(self, scope):
        task = self.tasks.get_by_id(scope.task_id)
        value = (task.worker_execution_context or {}).get("meet_dialog", {}) if task else {}
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.status != "in_progress"
            or getattr(task, "archived", False)
            or (task.tenant_id, task.project_id) != (scope.tenant_id, scope.project_id)
            or (value.get("lease_id"), value.get("runtime_id"), value.get("session_id"), value.get("owner_subject"))
            != (scope.lease_id, scope.runtime_id, scope.session_id, scope.owner_subject)
            or "video.receive" not in value.get("capabilities", [])
        ):
            raise MeetError("meet_visual_parent_inactive", 403)
        return task

    def read(self, scope):
        value = (self.parent(scope).worker_execution_context or {}).get("meet_visual", {"count": 0, "job": None})
        if (
            not isinstance(value, dict)
            or set(value) != {"count", "job"}
            or type(value["count"]) is not int
            or not 0 <= value["count"] <= 360
        ):
            raise MeetError("meet_visual_state_invalid", 403)
        if value["job"] is not None:
            try:
                job = value["job"]
                validate_visual_job(job, job.get("issued_at", 0))
            except (ValueError, TypeError, AttributeError):
                raise MeetError("meet_visual_state_invalid", 403) from None
        return deepcopy(value)

    @staticmethod
    def execution(scope, job):
        return {"meet_visual_job": job, "parent_dispatch": scope.lease_id, "runtime_id": scope.runtime_id}

    def claim(self, scope, job, now):
        from agent.services.task_queue_service import get_task_queue_service

        validate_visual_job(job, now)
        parent, state = self.parent(scope), self.read(scope)
        if state["count"] >= 360 or state["job"] is not None and state["job"]["deadline"] > now:
            raise MeetError("meet_visual_busy_or_exhausted", 409)
        context = deepcopy(parent.worker_execution_context)
        inherited, parent_id, worker_url = organization_tuple(parent), parent.parent_task_id, parent.assigned_agent_url
        if not self._cas(
            scope.task_id,
            "in_progress",
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_dialog_session"
                and (row.tenant_id, row.project_id, row.parent_task_id, row.assigned_agent_url)
                == (scope.tenant_id, scope.project_id, parent_id, worker_url)
                and row.worker_execution_context == context
                and organization_tuple(row) == inherited
            ),
            worker_execution_context=context | {"meet_visual": {"count": state["count"] + 1, "job": job}},
            event_type="meet_visual_delegated",
            event_actor="hub",
        ):
            raise MeetError("meet_visual_reservation_conflict", 409)
        if state["job"] is not None:
            self.finish(scope, state["job"], "failed")
        # Reservation precedes ingestion. An uncertain result cannot start a second
        # job; even successful completion retains its original cooldown deadline.
        ingest = self.ingest or get_task_queue_service().ingest_task
        ingest(
            task_id=job["task_id"],
            status="in_progress",
            title="Authorized Meet visual analysis",
            description="Bounded local image statistics; no raw frames or results persisted.",
            created_by=scope.owner_subject,
            source="meet_visual",
            team_id=inherited.get("team_id"),
            event_type="meet_visual_ingested",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "meet_visual_receive",
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                **{key: value for key, value in inherited.items() if key != "team_id"},
                "parent_task_id": scope.task_id,
                "assigned_agent_url": worker_url,
                "required_capabilities": ["meet_visual_receive"],
                "worker_execution_context": self.execution(scope, job),
            },
        )

    def require_job(self, scope, job, *, allow_completed=False):
        parent, child = self.parent(scope), self.tasks.get_by_id(job["task_id"])
        if (
            self.read(scope)["job"] != job
            or child is None
            or child.task_kind != "meet_visual_receive"
            or child.status not in ({"in_progress", "completed"} if allow_completed else {"in_progress"})
            or getattr(child, "archived", False)
            or (child.tenant_id, child.project_id, child.parent_task_id, child.assigned_agent_url)
            != (scope.tenant_id, scope.project_id, scope.task_id, parent.assigned_agent_url)
            or organization_tuple(child) != organization_tuple(parent)
            or child.worker_execution_context != self.execution(scope, job)
        ):
            raise MeetError("meet_visual_task_inactive", 403)
        return child

    def finish(self, scope, job, status):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_visual_terminal_invalid")
        return self._cas(
            job["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_visual_receive"
                and (row.tenant_id, row.project_id, row.parent_task_id)
                == (scope.tenant_id, scope.project_id, scope.task_id)
                and row.worker_execution_context == self.execution(scope, job)
            ),
            event_type="meet_visual_" + status,
            event_actor="hub",
        )
