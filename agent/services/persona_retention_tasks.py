"""Hub task queue adapter for private-store maintenance, not a publisher worker."""

import time


class HubPersonaRetentionTasks:
    def __init__(self, *, clock=time.time, kind="image"):
        if type(kind) is not str or kind not in ("image", "video"):
            raise ValueError("persona_retention_kind_invalid")
        self.kind = kind
        self.context_key = "persona_retention" if kind == "image" else "persona_video_retention"
        self.clock = clock

    def start(self, record):
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=record["task_id"],
            status="in_progress",
            title=f"Apply persona {self.kind} retention",
            description="Hub-owned exact-asset cleanup in the private artifact store; no publication authority.",
            created_by=record["actor"],
            source="persona_media",
            event_type="persona_retention_started",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": f"persona_{self.kind}_retention",
                "tenant_id": record["tenant_id"],
                "project_id": record["project_id"],
                "required_capabilities": ["hub_" + self.context_key],
                "worker_execution_context": {self.context_key: record},
            },
        )

    def _matches(self, task, record):
        return (
            task.task_kind == f"persona_{self.kind}_retention"
            and task.tenant_id == record["tenant_id"]
            and task.project_id == record["project_id"]
            and task.worker_execution_context == {self.context_key: record}
        )

    def require(self, record):
        from agent.services.repository_registry import get_repository_registry

        task = get_repository_registry().task_repo.get_by_id(record["task_id"])
        if (
            task is None
            or task.status != "in_progress"
            or not self._matches(task, record)
            or self.clock() * 1000 >= record["lease_until_ms"]
        ):
            raise PermissionError("persona_retention_task_changed")

    def finish(self, record, state):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        if not record["task_id"]:
            return False
        if state not in ("completed", "failed"):
            raise ValueError("persona_retention_task_status_invalid")
        return compare_and_set_local_task_status(
            record["task_id"],
            state,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: self._matches(task, record)
            and (state != "completed" or self.clock() * 1000 < record["lease_until_ms"]),
            event_type="persona_retention_" + state,
            event_actor="hub",
            event_details={"lease_id": record["lease_id"]},
        )
