"""Generation uses the normal Hub queue and exact terminal compare-and-set."""

import time

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generation import PersonaGenerationRequest, admission_digest


def _verification_matches(task, assignment):
    try:
        spec = task.verification_spec or {}
        request = PersonaGenerationRequest.model_validate(spec.get("persona_generation"))
        source = PersonaSourcePin.model_validate(spec.get("recipe_source"))
        return (
            set(spec) == {"persona_generation", "recipe_source"}
            and admission_digest(request, source) == assignment["admission_digest"]
        )
    except (ValueError, TypeError, AttributeError):
        return False


class HubPersonaGenerationState:
    def __init__(self, *, clock=time.time):
        self.clock = clock

    def start(self, assignment, request, *, source):
        from agent.services.task_queue_service import get_task_queue_service

        get_task_queue_service().ingest_task(
            task_id=assignment["task_id"],
            status="in_progress",
            title="Generate synthetic persona asset",
            description="Bounded Hub-delegated procedural media; no publication or inspection authority.",
            created_by=assignment["owner_subject"],
            source="persona_media",
            event_type="persona_generation_delegated",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "persona_media_generation",
                "tenant_id": assignment["tenant_id"],
                "project_id": assignment["project_id"],
                "required_capabilities": ["persona_media_generation"],
                "worker_execution_context": {"persona_generation": assignment},
                "verification_spec": {
                    "persona_generation": request.model_dump(mode="json"),
                    "recipe_source": source.model_dump(mode="json"),
                },
            },
        )

    def get(self, task_id):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    def finish(self, assignment, status, *, result_digest=None):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        if status not in ("completed", "failed") or (status == "completed" and not result_digest):
            raise ValueError("persona_generation_terminal_invalid")
        return compare_and_set_local_task_status(
            assignment["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: (
                task.task_kind == "persona_media_generation"
                and (task.tenant_id, task.project_id) == (assignment["tenant_id"], assignment["project_id"])
                and task.worker_execution_context == {"persona_generation": assignment}
                and (
                    status != "completed"
                    or (self.clock() < assignment["deadline"] and _verification_matches(task, assignment))
                )
            ),
            worker_execution_context={
                "persona_generation": assignment | ({"result_digest": result_digest} if result_digest else {})
            },
            event_type=f"persona_generation_{status}",
            event_actor="hub",
            event_details={"lease_id": assignment["lease_id"], "run_id": assignment["run_id"]},
        )
