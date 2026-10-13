"""Registry completion after actual SQL Pi result admission, never raw success."""

import copy

import pytest
from sqlalchemy import update
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubSourceEvidenceIdentityDB
from agent.services.pi_run_evidence_completion import PiRunEvidenceCompletionService
from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_result_completion_policy import complete
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reserve


@pytest.mark.parametrize("mutate_source", [False, True], ids=["expiry-replay", "mutated-source-denied"])
def test_reserved_registry_run_completes_after_committed_pi_receipt_and_replays_after_expiry(
    reservation, mutate_source,
):
    run = reserve(reservation)
    projection = reservation.registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id, task_id=run.task_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )
    complete(reservation.admission)
    service = PiRunEvidenceCompletionService(registry=reservation.registry)
    with Session(reservation.admission.engine) as session:
        accepted = service.complete(session=session, task_id=run.task_id, assignment=projection)
    assert accepted.state == "succeeded" and accepted.synthetic and accepted.evidence_scope == "test"
    reservation.admission.clock.now += 1000
    if mutate_source:
        with Session(reservation.admission.engine) as session:
            session.exec(update(HubSourceEvidenceIdentityDB).values(content_digest="f" * 64))
            session.commit()
    with Session(reservation.admission.engine) as session:
        if mutate_source:
            with pytest.raises(ValueError):
                service.complete(session=session, task_id=run.task_id, assignment=projection)
        else:
            assert service.complete(session=session, task_id=run.task_id, assignment=projection) == accepted


@pytest.mark.parametrize("change", ["no-receipt", "input", "sources", "run", "task"])
def test_invalid_receipt_or_reserved_projection_cannot_close_run(reservation, change):
    run = reserve(reservation)
    projection = reservation.registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id, task_id=run.task_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )
    complete(reservation.admission)
    if change in {"no-receipt", "input"}:
        with Session(reservation.admission.engine) as session:
            task = session.get(TaskDB, run.task_id)
            if change == "no-receipt":
                verification = copy.deepcopy(task.verification_status)
                verification.pop("pi_native_result_receipt")
                task.verification_status = verification
            else:
                task.worker_execution_context = {**task.worker_execution_context, "changed_input": "unbound"}
            session.add(task)
            session.commit()
    else:
        values = {key: value for key, value in projection.items() if key not in {"schema", "projection_digest"}}
        if change == "sources":
            values["source_ids"] = ["SRC_unknown"]
        elif change == "run":
            values["run_id"] = "RUN_unknown"
        else:
            values["task_id"] = "foreign-task"
        projection = build_hub_evidence_assignment(**values)
    service = PiRunEvidenceCompletionService(registry=reservation.registry)
    with Session(reservation.admission.engine) as session:
        with pytest.raises(ValueError):
            service.complete(session=session, task_id=run.task_id, assignment=projection)
    assert reservation.registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id, task_id=run.task_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )["run_id"] == run.run_id
