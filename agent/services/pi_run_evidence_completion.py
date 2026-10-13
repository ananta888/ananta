"""Close a pre-reserved Registry run from an immutable Hub Pi receipt only."""

from agent.db_models import TaskDB
from agent.ports.native_result_authority import NativeResultAuthority
from agent.repositories.sqlalchemy_support import require_locked_row
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryError
from agent.services.native_artifact_dispatch import native_dispatch_binding_digest
from agent.services.pi_context_evidence import PiContextEvidenceAdmission
from agent.services.pi_result_artifact_admission import require_pi_result_artifacts
from agent.services.pi_result_receipt import require_pi_result_receipt
from agent.services.pi_result_task_projection import pi_task_command, pi_task_result_candidate
from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.native_context_bundle import native_context_digest


class PiRunEvidenceCompletionService:
    def __init__(self, *, registry, contexts=None):
        self._registry = registry
        self._context_evidence = PiContextEvidenceAdmission(registry=registry, contexts=contexts)

    def complete(self, *, session, task_id, assignment):
        projection = validate_hub_evidence_assignment(assignment)
        task = require_locked_row(session, TaskDB, task_id, missing_reason="pi_evidence_task_missing")
        if task.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("pi_evidence_terminal_task_required")
        command = pi_task_command(task)
        candidate = pi_task_result_candidate(task, command)
        receipt = require_pi_result_receipt(task=task, command=command, candidate=candidate)
        authority = NativeResultAuthority(**receipt["authority"])
        admitted = require_pi_result_artifacts(session=session, task=task, command=command,
            candidate=candidate, authority=authority)
        if admitted != receipt.get('artifacts', []):
            raise ValueError('pi_native_result_receipt_invalid')
        dispatch_id = "native-dispatch:" + native_dispatch_binding_digest(command=command, authority=authority)
        if (
            projection["task_id"] != task.id or projection["assignment_id"] != authority.assignment_id
            or projection["dispatch_lease_id"] != dispatch_id
        ):
            raise ValueError("pi_evidence_result_assignment_mismatch")
        self._context_evidence.require_reserved(session=session, task=task, command=command,
            worker_id=authority.worker_id, assignment=projection)
        state = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}[task.status]
        facts = {
            "tenant_id": task.tenant_id, "project_id": task.project_id, "run_id": projection["run_id"],
            "task_id": task.id, "assignment_id": authority.assignment_id, "dispatch_lease_id": dispatch_id,
            "input_digest": native_context_digest(task.worker_execution_context),
            "expected_binding_digest": projection["binding_digest"],
        }
        try:
            registered = self._registry.assignment_projection(**facts)
        except HubEvidenceRegistryError as exc:
            if exc.reason_code != "evidence_run_reservation_unavailable":
                raise
            return self._registry.require_run_result(
                **facts, source_ids=projection["source_ids"], result_digest=candidate.digest, terminal_state=state,
            )
        if registered != projection:
            raise ValueError("pi_evidence_reserved_projection_mismatch")
        return self._registry.record_result(
            tenant_id=task.tenant_id, project_id=task.project_id, run_id=projection["run_id"],
            assignment_id=authority.assignment_id, dispatch_lease_id=dispatch_id,
            terminal_state=state, result_digest=candidate.digest,
        )
