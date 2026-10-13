"""Real Task/lease/Registry SQL; synthetic admitted deployment, no execution."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.pi_result_authority import SQLAlchemyPiResultAuthority
from agent.services.pi_run_evidence_reservation import PiRunEvidenceReservationService
from agent.services.pi_runtime_manifest_admission import PiRuntimeManifestAdmissionService
from ananta_contracts.pi_runtime_manifest import PiRuntimeManifest
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401


@pytest.fixture
def reservation(admission):
    with Session(admission.engine) as session:
        task = session.get(TaskDB, admission.task_id)
        task.project_id = "synthetic-project"
        task.status = "assigned"
        session.add(task)
        session.commit()
    manifest = PiRuntimeManifest(
        tenant_id=admission.command.tenant_id, project_id="synthetic-project", worker_id="worker-1",
        worker_url="http://worker-1:5000", repository_revision="a" * 40, image_digest="sha256:" + "b" * 64,
        node_version="24.18.0", pi_version="0.85.1", evidence_scope="test", synthetic=True,
    )
    registry = HubEvidenceRegistryService(
        SqlEvidenceIdentityRepository(admission.engine), clock=lambda: admission.clock.now,
    )
    manifests = PiRuntimeManifestAdmissionService(
        deployments=SimpleNamespace(require_manifest=lambda **identity: manifest), registry=registry,
    )
    source = manifests.admit(manifest=manifest, policy_digest="c" * 64).source
    service = PiRunEvidenceReservationService(
        manifests=manifests, registry=registry,
        authority=SQLAlchemyPiResultAuthority(key_ring=lambda: admission.keys), clock=lambda: admission.clock.now,
    )
    return SimpleNamespace(admission=admission, registry=registry, service=service, values={
        "task_id": admission.task_id, "manifest": manifest, "source_id": source.source_id,
        "source_binding_digest": source.binding_digest, "policy_digest": "c" * 64,
    })


def reserve(value):
    with Session(value.admission.engine) as session:
        return value.service.reserve(session=session, **value.values)


def test_registry_reserves_exact_task_dispatch_before_execution_and_replays(reservation):
    run = reserve(reservation)
    assert reserve(reservation) == run
    assert run.run_id.startswith("RUN_") and run.state == "reserved"
    assert run.task_id == reservation.admission.task_id
    assert run.synthetic and run.evidence_scope == "test" and len(run.source_ids) == 2
    assert reservation.registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id, task_id=run.task_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )["run_id"] == run.run_id


@pytest.mark.parametrize("change", ["expired", "worker", "project", "policy", "terminal", "started"])
def test_invalid_current_authority_never_reserves_run(reservation, change):
    if change == "expired":
        reservation.admission.clock.now += 1000
    elif change in {"worker", "project"}:
        field = "worker_id" if change == "worker" else "project_id"
        reservation.values["manifest"] = replace(reservation.values["manifest"], **{field: "foreign"})
    elif change == "policy":
        reservation.values["policy_digest"] = "d" * 64
    else:
        with Session(reservation.admission.engine) as session:
            task = session.get(TaskDB, reservation.admission.task_id)
            task.status = "completed" if change == "terminal" else "in_progress"
            session.add(task)
            session.commit()
    with pytest.raises(ValueError):
        reserve(reservation)
    with Session(reservation.admission.engine) as session:
        assert session.exec(select(HubRunEvidenceIdentityDB)).all() == []


@pytest.mark.parametrize("change", ["none", "context", "expired", "digest", "started", "deployment", "manifest-source"])
def test_dispatch_projection_revalidates_current_task_and_reserved_input(reservation, change):
    run = reserve(reservation)
    digest = run.binding_digest
    if change == "expired":
        reservation.admission.clock.now += 1000
    elif change == "digest":
        digest = "e" * 64
    elif change == "deployment":
        changed = replace(reservation.values["manifest"], node_version="24.19.0")
        reservation.service._manifests._deployments.require_manifest = lambda **identity: changed
    elif change == "manifest-source":
        source = reservation.service._manifests.admit(
            manifest=reservation.values["manifest"], policy_digest="d" * 64,
        ).source
        reservation.values.update(source_id=source.source_id, source_binding_digest=source.binding_digest,
                                  policy_digest="d" * 64)
    elif change in {"context", "started"}:
        with Session(reservation.admission.engine) as session:
            task = session.get(TaskDB, reservation.admission.task_id)
            if change == "context":
                task.worker_execution_context = {**task.worker_execution_context, "changed_input": "unexpected"}
            else:
                task.status = "in_progress"
            session.add(task)
            session.commit()
    with Session(reservation.admission.engine) as session:
        values = {**reservation.values, "session": session, "task_id": run.task_id, "run_id": run.run_id,
                  "expected_binding_digest": digest}
        if change == "none":
            projection = reservation.service.assignment_projection(**values)
            assert projection["run_id"] == run.run_id
            assert projection["source_ids"] == sorted(run.source_ids)
            assert "input_digest" not in projection and "environment_digest" not in projection
            assert "manifest" not in projection and "worker_execution_context" not in projection
        else:
            with pytest.raises(ValueError):
                reservation.service.assignment_projection(**values)
