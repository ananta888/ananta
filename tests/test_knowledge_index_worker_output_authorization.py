from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.common.errors import NotFoundError
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.knowledge_index_dispatch import (
    build_knowledge_index_dispatch,
    parse_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_worker_output_capability import (
    KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER,
    encode_knowledge_index_output_capability,
)
from worker.retrieval.knowledge_index_output_authorization import (
    KnowledgeIndexWorkerOutputAuthorizationError,
    KnowledgeIndexWorkerOutputCapabilityAuthorizer,
)

JOB_ID = "knowledge-index-" + "a" * 32
MANIFEST = {
    "assignment_id": "assignment-1",
    "lease_id": "lease-1",
    "binding_digest": "d" * 64,
    "grant_expires_at_epoch_ms": 20_000,
    "signature": "signed",
}


class Repository:
    def __init__(self, task):
        self.task = task

    def get_by_id(self, task_id):
        return self.task if task_id == JOB_ID else None


class Verifier:
    def __init__(self, accepted=True):
        self.accepted = accepted

    def verify_manifest(self, _manifest):
        return self.accepted


class Ledger:
    def __init__(self, receipt):
        self.receipt = receipt

    def get_receipt(self, *, worker_id, job_id):
        assert worker_id == "worker-a"
        assert job_id == JOB_ID
        return self.receipt


def receipt(**overrides):
    marker = build_knowledge_index_dispatch(
        job_id=JOB_ID,
        phase="execute",
        source_access_manifest=MANIFEST,
    )
    value = {
        "schema": "ananta.knowledge_index_worker_dispatch_receipt.v1",
        "job_id": JOB_ID,
        "phase": "execute",
        "worker_id": "worker-a",
        "assignment_id": "assignment-1",
        "lease_id": "lease-1",
        "marker_digest": parse_knowledge_index_dispatch(
            marker,
            expected_phase="execute",
            expected_job_id=JOB_ID,
        ).marker_digest,
        "manifest_binding_digest": "d" * 64,
        "claimed_at_epoch_ms": 9_000,
    }
    value.update(overrides)
    return value


def task(*, worker_url="http://worker-a:5000"):
    return {
        "id": JOB_ID,
        "status": "proposing",
        "assigned_agent_url": worker_url,
        "worker_execution_context": {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_execution_job.v2",
                "job_id": JOB_ID,
            }
        },
    }


def parsed_job(*, worker_id="worker-a", lease_expires=15_000):
    return SimpleNamespace(
        assignment=SimpleNamespace(
            assignment_id="assignment-1",
            worker_id=worker_id,
            lease_id="lease-1",
            lease_expires_epoch_ms=lease_expires,
        )
    )


def authorizer(
    *,
    stored_task=None,
    verifier=None,
    parsed=None,
    ledger=None,
):
    return KnowledgeIndexWorkerOutputCapabilityAuthorizer(
        task_repository=Repository(stored_task or task()),
        receipt_ledger=ledger or Ledger(receipt()),
        manifest_verifier=verifier or Verifier(),
        worker_id="worker-a",
        worker_url="http://worker-a:5000",
        clock_ms=lambda: 10_000,
        execution_job_parser=lambda _job: parsed or parsed_job(),
    )


def authorize(service, *, metadata=None, role="manifest"):
    service.authorize(
        artifact_id="artifact-1",
        artifact_sha256="b" * 64,
        artifact_size_bytes=42,
        artifact_media_type="application/json",
        artifact_metadata=metadata
        or {
            "system_artifact_kind": "knowledge_index_worker_output",
            "knowledge_index_job_id": JOB_ID,
            "knowledge_index_id": "idx-1",
            "knowledge_index_run_id": "run-1",
            "output_role": role,
        },
        manifest=MANIFEST,
        job_id=JOB_ID,
        knowledge_index_id="idx-1",
        run_id="run-1",
        output_role=role,
    )


def test_authorizes_exact_live_worker_job_and_artifact_binding() -> None:
    authorize(authorizer())


def test_authorizes_revision_bound_domain_supplement_role() -> None:
    authorize(
        authorizer(),
        role=DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    )


@pytest.mark.parametrize(
    ("service", "reason_code"),
    [
        (
            authorizer(verifier=Verifier(False)),
            "knowledge_index_output_capability_signature_invalid",
        ),
        (
            authorizer(parsed=parsed_job(worker_id="worker-b")),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(parsed=parsed_job(lease_expires=9_999)),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(stored_task=task(worker_url="http://worker-b:5000")),
            "knowledge_index_output_worker_url_mismatch",
        ),
        (
            authorizer(ledger=Ledger(None)),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(ledger=Ledger(receipt(manifest_binding_digest="e" * 64))),
            "knowledge_index_output_execution_binding_invalid",
        ),
        (
            authorizer(ledger=Ledger(receipt(marker_digest="f" * 64))),
            "knowledge_index_output_execution_binding_invalid",
        ),
    ],
)
def test_denies_invalid_capability_or_delegation(service, reason_code) -> None:
    with pytest.raises(
        KnowledgeIndexWorkerOutputAuthorizationError,
        match=reason_code,
    ):
        authorize(service)


def test_denies_cross_job_artifact_metadata() -> None:
    metadata = {
        "system_artifact_kind": "knowledge_index_worker_output",
        "knowledge_index_job_id": "knowledge-index-" + "c" * 32,
        "knowledge_index_id": "idx-1",
        "knowledge_index_run_id": "run-1",
        "output_role": "manifest",
    }

    with pytest.raises(
        KnowledgeIndexWorkerOutputAuthorizationError,
        match="knowledge_index_output_artifact_binding_mismatch",
    ):
        authorize(authorizer(), metadata=metadata)


def test_internal_output_route_pins_verified_bytes_before_delivery(
    monkeypatch,
    tmp_path,
) -> None:
    from agent.routes.artifacts import (
        get_knowledge_index_worker_output_artifact,
    )

    artifact_id = "artifact-1"
    version_id = "version-1"
    content = b'{"schema":"index-manifest-v1"}'
    replacement = b"x" * len(content)
    digest = hashlib.sha256(content).hexdigest()
    storage_path = tmp_path / "manifest.json"
    storage_path.write_bytes(content)
    artifact = SimpleNamespace(
        latest_version_id=version_id,
        artifact_metadata={
            "system_artifact_kind": "knowledge_index_worker_output",
            "knowledge_index_job_id": JOB_ID,
            "knowledge_index_id": "idx-1",
            "knowledge_index_run_id": "run-1",
            "output_role": "manifest",
        },
    )
    version = SimpleNamespace(
        sha256=digest,
        size_bytes=len(content),
        media_type="application/json",
        storage_path=str(storage_path),
        original_filename="manifest.json",
    )

    class ByIdRepository:
        def __init__(self, expected_id, value):
            self.expected_id = expected_id
            self.value = value

        def get_by_id(self, item_id):
            return self.value if item_id == self.expected_id else None

    captured = {}

    class RouteAuthorizer:
        def authorize(self, **kwargs):
            captured.update(kwargs)

    app = Flask(__name__)
    app.extensions["repository_registry"] = SimpleNamespace(
        artifact_repo=ByIdRepository(artifact_id, artifact),
        artifact_version_repo=ByIdRepository(version_id, version),
    )
    app.extensions[
        "knowledge_index_worker_output_capability_authorizer"
    ] = RouteAuthorizer()

    def swap_path_before_send(source, **kwargs):
        storage_path.write_bytes(replacement)
        if hasattr(source, "read"):
            served = source.read()
        else:
            with open(source, "rb") as path_handle:
                served = path_handle.read()
        return app.response_class(
            served,
            mimetype=kwargs.get("mimetype"),
        )

    monkeypatch.setattr(
        "agent.routes.artifacts.send_file",
        swap_path_before_send,
    )
    headers = {
        KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER: (
            encode_knowledge_index_output_capability(MANIFEST)
        ),
        KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER: JOB_ID,
        KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER: "idx-1",
        KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER: "run-1",
        KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER: "manifest",
        KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER: digest,
        KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER: str(len(content)),
        KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER: "application/json",
    }
    with app.test_request_context(headers=headers):
        response = get_knowledge_index_worker_output_artifact(artifact_id)

    assert response.status_code == 200
    assert response.get_data() == content
    assert storage_path.read_bytes() == replacement
    assert response.headers["X-Artifact-SHA256"] == digest
    assert response.headers["X-Artifact-Size"] == str(len(content))
    assert captured["artifact_id"] == artifact_id
    response.close()

    with app.test_request_context(headers=headers):
        with pytest.raises(
            NotFoundError,
            match="knowledge_index_output_integrity_invalid",
        ):
            get_knowledge_index_worker_output_artifact(artifact_id)


def test_pinned_artifact_reader_rejects_hardlinked_storage(tmp_path) -> None:
    from agent.routes.artifacts import _read_pinned_verified_artifact_bytes

    content = b"worker output"
    storage_path = tmp_path / "output.bin"
    hardlink_path = tmp_path / "output-alias.bin"
    storage_path.write_bytes(content)
    os.link(storage_path, hardlink_path)

    with pytest.raises(
        NotFoundError,
        match="knowledge_index_output_integrity_invalid",
    ):
        _read_pinned_verified_artifact_bytes(
            storage_path,
            expected_size=len(content),
            expected_sha256=hashlib.sha256(content).hexdigest(),
            maximum_size=128 * 1024 * 1024,
            not_found_reason="knowledge_index_output_not_found",
            integrity_reason="knowledge_index_output_integrity_invalid",
        )
