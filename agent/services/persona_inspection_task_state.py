"""Infrastructure adapter to the existing Hub task queue and terminal CAS."""

import time

from agent.services.persona_inspection_tasks import task_context


class HubPersonaTaskState:
    def __init__(self, *, clock=time.time):
        self.clock = clock

    def start(self, assignment, actor):
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=assignment["task_id"],
            status="in_progress",
            title="Inspect persona image",
            description="Hub-delegated bounded image normalization; no publication authority.",
            created_by=actor,
            source="persona_media",
            event_type="persona_image_delegated",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "persona_image_inspection",
                "tenant_id": assignment["tenant_id"],
                "project_id": assignment["project_id"],
                "required_capabilities": ["persona_image_inspection"],
                "worker_execution_context": {"persona_image": task_context(assignment)},
            },
        )

    def get(self, task_id):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    def finish(self, assignment, status, *, receipt_digest=None):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        context = task_context(assignment)
        if status not in ("completed", "failed") or (status == "completed" and not receipt_digest):
            raise ValueError("persona_inspection_terminal_invalid")
        return compare_and_set_local_task_status(
            assignment["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: (
                task.task_kind == "persona_image_inspection"
                and task.tenant_id == assignment["tenant_id"]
                and task.project_id == assignment["project_id"]
                and task.worker_execution_context == {"persona_image": context}
                and (status != "completed" or self.clock() < assignment["deadline"])
            ),
            worker_execution_context={
                "persona_image": context | ({"result_digest": receipt_digest} if receipt_digest else {})
            },
            event_type=f"persona_image_{status}",
            event_actor="hub",
            event_details={"lease_id": assignment["lease_id"], "run_id": assignment["run_id"]},
        )
