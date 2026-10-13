"""Real Hub SQL/immutable storage and dispatch adapter; synthetic test inputs."""

import base64
import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.db_models import ArtifactDB, ArtifactVersionDB, TaskDB
from agent.services.artifact_store import ArtifactStore
from agent.services.native_artifact_admission import NativeArtifactAdmissionService
from agent.services.native_artifact_dispatch import NativeArtifactDispatch
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from ananta_contracts.native_artifact_transfer import SCHEMA
from ananta_contracts.native_context_bundle import native_context_digest
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import (
    admission,  # noqa: F401
    complete,
)


@pytest.fixture
def ingress(native, tmp_path):  # noqa: F811
    command = replace(native.command, node=replace(native.command.node, output_artifacts=("report",)))
    native.command = command
    with Session(native.engine) as session:
        task = session.get(TaskDB, native.task_id)
        task.worker_execution_context = {**task.worker_execution_context, "native_node_command": command.to_dict()}
        session.add(task)
        session.commit()
    authority = SQLAlchemyNativeResultAuthority(key_ring=lambda: native.keys)
    dispatch = NativeArtifactDispatch(authority=authority, worker_id="worker-1", worker_url="http://worker-1:5000")
    store = ArtifactStore(tmp_path / "hub-artifacts")
    service = NativeArtifactAdmissionService(
        authority=authority, dispatch=dispatch, storage=store, clock=lambda: native.clock.now,
    )
    with Session(native.engine) as session:
        task = session.get(TaskDB, native.task_id)
        current = authority.require_current(session=session, command=command, task=task, now=native.clock.now)
        lease_digest = dispatch.require_current_digest(
            session=session, command=command, task=task, now=native.clock.now,
        )
    content = b"synthetic result\n"
    transfer = {"schema": SCHEMA, "binding": {
        "hub_task_id": native.task_id, "command_digest": native_context_digest(command.to_dict()),
        "assignment_id": current.assignment_id, "assignment_revision": current.assignment_revision,
        "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
        "dispatch_lease_digest": lease_digest,
    }, "artifacts": [{
        "output_name": "report", "filename": "report.txt", "media_type": "text/plain",
        "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }]}
    return SimpleNamespace(
        native=native, service=service, dispatch=dispatch, store=store, transfer=transfer, content=content,
    )


def materialize(ingress, *, commit=True):
    with Session(ingress.native.engine) as session:
        receipts = ingress.service.materialize(
            session=session, task_id=ingress.native.task_id, transfer=ingress.transfer,
        )
        if commit:
            session.commit()
        return receipts


def test_real_hub_storage_registration_and_idempotent_retry(ingress):
    first = materialize(ingress)
    assert materialize(ingress) == first
    receipt, = first
    assert receipt["classification"] == "technical_observation"
    with Session(ingress.native.engine) as session:
        artifact = session.exec(select(ArtifactDB)).one()
        version = session.exec(select(ArtifactVersionDB)).one()
        assert artifact.latest_version_id == version.id == receipt["artifact_version_id"]
        assert Path(version.storage_path).read_bytes() == ingress.content


def test_transaction_rollback_has_no_admitted_rows_and_retry_repairs_orphan_bytes(ingress):
    first = materialize(ingress, commit=False)
    with Session(ingress.native.engine) as session:
        assert session.exec(select(ArtifactDB)).all() == []
        assert session.exec(select(ArtifactVersionDB)).all() == []
    assert materialize(ingress) == first


@pytest.mark.parametrize("tamper", ["lease", "assignment", "hash", "output", "receipt"])
def test_untrusted_transfer_never_writes_bytes_or_rows(ingress, tamper):
    if tamper == "lease":
        ingress.transfer["binding"]["dispatch_lease_digest"] = "e" * 64
    elif tamper == "assignment":
        ingress.transfer["binding"]["assignment_revision"] += 1
    elif tamper == "hash":
        ingress.transfer["artifacts"][0]["sha256"] = "0" * 64
    elif tamper == "output":
        ingress.transfer["artifacts"][0]["output_name"] = "secret"
    else:
        ingress.transfer["artifacts"][0]["hub_receipt"] = "forged"
    with pytest.raises(ValueError):
        materialize(ingress)
    assert list(ingress.store.base_dir.iterdir()) == []
    with Session(ingress.native.engine) as session:
        assert session.exec(select(ArtifactDB)).all() == []


def test_expired_authority_does_not_touch_storage(ingress):
    ingress.native.clock.now += 1000
    with pytest.raises(ValueError, match="lease_not_current"):
        materialize(ingress)
    assert list(ingress.store.base_dir.iterdir()) == []


@pytest.mark.parametrize("worker_id,worker_url", [
    ("foreign-worker", "http://worker-1:5000"), ("worker-1", "http://foreign-worker:5000"),
])
def test_authenticated_worker_must_match_current_assignment(ingress, worker_id, worker_url):
    ingress.service._dispatch = NativeArtifactDispatch(
        authority=ingress.service._authority, worker_id=worker_id, worker_url=worker_url,
    )
    with pytest.raises(ValueError, match="worker_assignment_mismatch"):
        materialize(ingress)
    assert list(ingress.store.base_dir.iterdir()) == []


def test_time_advance_within_live_lease_preserves_dispatch_identity(ingress):
    ingress.native.clock.now += 1
    receipt, = materialize(ingress)
    assert receipt["binding"]["dispatch_lease_digest"] == ingress.transfer["binding"]["dispatch_lease_digest"]


def test_registration_mutation_cannot_be_replayed(ingress):
    materialize(ingress)
    with Session(ingress.native.engine) as session:
        artifact = session.exec(select(ArtifactDB)).one()
        metadata = copy.deepcopy(artifact.artifact_metadata)
        metadata["native_artifact_ingress"]["classification"] = "production"
        artifact.artifact_metadata = metadata
        session.add(artifact)
        session.commit()
    with pytest.raises(ValueError, match="registration_conflict"):
        materialize(ingress)


def test_immutable_bytes_detect_storage_tampering(ingress):
    materialize(ingress)
    with Session(ingress.native.engine) as session:
        path = session.exec(select(ArtifactVersionDB)).one().storage_path
    Path(path).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        materialize(ingress)


def set_result_artifact(ingress, reference):
    outer = ingress.native.response["workflow_adapter_verification"]["workflow_adapter_task_result"]
    native_result = outer["adapter_result"]
    native_result["artifact_refs"] = {"report": reference}
    native_result["verification"]["native_node_result"]["artifact_refs"] = {"report": reference}


@pytest.mark.parametrize("mode", ["save", "cas"])
def test_native_completion_requires_committed_hub_artifact_registration(ingress, mode):
    receipt, = materialize(ingress)
    set_result_artifact(ingress, receipt["artifact_ref"])
    task = complete(ingress.native, mode)
    artifacts = task.verification_status["native_result_receipt"]["artifacts"]
    assert artifacts[0]["artifact_id"] == receipt["artifact_id"]
    assert artifacts[0]["artifact_version_id"] == receipt["artifact_version_id"]


@pytest.mark.parametrize("reference", ["artifact://worker-local-id", "artifact://native-artifact-" + "a" * 64])
def test_worker_local_or_nonexistent_artifact_cannot_complete_task(ingress, reference):
    set_result_artifact(ingress, reference)
    with pytest.raises(ValueError):
        complete(ingress.native)
    assert ingress.native.repository.get_by_id(ingress.native.task_id).status == "in_progress"


def test_rolled_back_artifact_receipt_cannot_complete_task(ingress):
    receipt, = materialize(ingress, commit=False)
    set_result_artifact(ingress, receipt["artifact_ref"])
    with pytest.raises(ValueError):
        complete(ingress.native)
    assert ingress.native.repository.get_by_id(ingress.native.task_id).status == "in_progress"


def test_mutated_registration_cannot_be_used_for_completion(ingress):
    receipt, = materialize(ingress)
    set_result_artifact(ingress, receipt["artifact_ref"])
    with Session(ingress.native.engine) as session:
        version = session.exec(select(ArtifactVersionDB)).one()
        version.sha256 = "0" * 64
        session.add(version)
        session.commit()
    with pytest.raises(ValueError, match="registration_conflict"):
        complete(ingress.native)
    assert ingress.native.repository.get_by_id(ingress.native.task_id).status == "in_progress"
