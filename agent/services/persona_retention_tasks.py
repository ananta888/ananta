"""Hub task queue adapter for private-store maintenance, not a publisher worker."""

import time


class HubPersonaRetentionTasks:
    def __init__(self, *, clock=time.time):
        self.clock = clock

    def start(self, record):
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=record["task_id"],
            status="in_progress",
            title="Apply persona image retention",
            description="Hub-owned exact-asset cleanup in the private artifact store; no publication authority.",
            created_by=record["actor"],
            source="persona_media",
            event_type="persona_retention_started",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "persona_image_retention",
                "tenant_id": record["tenant_id"],
                "project_id": record["project_id"],
                "required_capabilities": ["hub_persona_retention"],
                "worker_execution_context": {"persona_retention": record},
            },
        )

    @staticmethod
    def _matches(task, record):
        return (
            task.task_kind == "persona_image_retention"
            and task.tenant_id == record["tenant_id"]
            and task.project_id == record["project_id"]
            and task.worker_execution_context == {"persona_retention": record}
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
