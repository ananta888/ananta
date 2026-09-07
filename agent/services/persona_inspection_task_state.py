"""Infrastructure adapter to the existing Hub task queue and terminal CAS."""

import time

from agent.services.persona_inspection_tasks import task_context


class HubPersonaTaskState:
    def __init__(self, *, clock=time.time, kind="image"):
        if kind not in ("image", "video", "voice"):
            raise ValueError("persona_inspection_kind_invalid")
        self.clock = clock
        self.kind = kind

    def start(self, assignment, actor, *, admission):
        from agent.services.task_queue_service import get_task_queue_service

        if assignment["schema"] != f"ananta.persona-{self.kind}-task.v1":
            raise ValueError("persona_inspection_kind_mismatch")
        get_task_queue_service().ingest_task(
            task_id=assignment["task_id"],
            status="in_progress",
            title=f"Inspect persona {self.kind}",
            description=f"Hub-delegated bounded {self.kind} normalization; no publication authority.",
            created_by=actor,
            source="persona_media",
            event_type=f"persona_{self.kind}_delegated",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": f"persona_{self.kind}_inspection",
                "tenant_id": assignment["tenant_id"],
                "project_id": assignment["project_id"],
                "required_capabilities": [f"persona_{self.kind}_inspection"],
                "worker_execution_context": {f"persona_{self.kind}": task_context(assignment)},
                "verification_spec": {"persona_admission": admission.model_dump(mode="json")},
            },
        )

    def get(self, task_id):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    def finish(self, assignment, status, *, receipt_digest=None):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        if assignment.get("schema") != f"ananta.persona-{self.kind}-task.v1":
            return False
        context = task_context(assignment)
        if status not in ("completed", "failed") or (status == "completed" and not receipt_digest):
            raise ValueError("persona_inspection_terminal_invalid")
        return compare_and_set_local_task_status(
            assignment["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: (
                task.task_kind == f"persona_{self.kind}_inspection"
                and task.tenant_id == assignment["tenant_id"]
                and task.project_id == assignment["project_id"]
                and task.worker_execution_context == {f"persona_{self.kind}": context}
                and (status != "completed" or self.clock() < assignment["deadline"])
            ),
            worker_execution_context={
                f"persona_{self.kind}": context | ({"result_digest": receipt_digest} if receipt_digest else {})
            },
            event_type=f"persona_{self.kind}_{status}",
            event_actor="hub",
            event_details={"lease_id": assignment["lease_id"], "run_id": assignment["run_id"]},
        )
