from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_payload_authorization import (
    KnowledgeIndexPayloadAuthorizationError,
    KnowledgeIndexPayloadCapabilityAuthorizer,
)
from ananta_contracts.knowledge_index_payload_capability import (
    decode_knowledge_index_payload_capability,
    encode_knowledge_index_payload_capability,
)


FINGERPRINT = "a" * 64
ARTIFACT_ID = "artifact-1"
WORKER_ID = "worker-1"
WORKER_URL = "http://worker-1:5000"


def _job():
    return {
        "job_id": f"knowledge-index-{FINGERPRINT[:32]}",
        "idempotency_fingerprint": FINGERPRINT,
        "authority_binding": {
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "source_revision_id": "revision-1",
            "source_revision_digest": "b" * 64,
            "destination_id": "destination-1",
            "destination_digest": "c" * 64,
            "source_access_grant_id": "grant-1",
            "source_access_grant_digest": "d" * 64,
            "policy_snapshot_digest": "e" * 64,
        },
        "assignment": {
            "assignment_id": "assignment-1",
            "lease_id": "lease-1",
        },
        "file_manifest": {
            "manifest_id": "manifest-1",
            "manifest_digest": "f" * 64,
        },
        "payload": {
            "payload_artifact_ref": {
                "artifact_id": ARTIFACT_ID,
                "sha256": "1" * 64,
                "size_bytes": 42,
                "media_type": (
                    "application/vnd.ananta.knowledge-index-job+json"
                ),
            }
        },
    }


def _manifest():
    job = _job()
    authority = job["authority_binding"]
    return {
        "schema": "ananta.source-control.enforcement-manifest.v1",
        "authority": "hub",
        "tenant_id": authority["tenant_id"],
        "project_id": authority["project_id"],
        "source_revision_id": authority["source_revision_id"],
        "source_revision_digest": authority["source_revision_digest"],
        "destination_id": authority["destination_id"],
        "destination_digest": authority["destination_digest"],
        "source_access_grant_id": authority["source_access_grant_id"],
        "source_access_grant_digest": authority[
            "source_access_grant_digest"
        ],
        "policy_digest": authority["policy_snapshot_digest"],
        "content_manifest_id": "manifest-1",
        "content_manifest_digest": "f" * 64,
        "assignment_id": "assignment-1",
        "lease_id": "lease-1",
        "grant_expires_at_epoch_ms": 20_000,
        "signature": "signed",
    }


class _Verifier:
    def verify_manifest(self, manifest):
        return manifest.get("signature") == "signed"


class _Bindings:
    def validate_delegated_payload_access(
        self,
        *,
        assignment_id,
        lease_id,
        authenticated_worker_id,
    ):
        assert assignment_id == "assignment-1"
        assert lease_id == "lease-1"
        assert authenticated_worker_id == WORKER_ID
        return SimpleNamespace(
            job=SimpleNamespace(to_wire=_job)
        )


class _Agents:
    def get_by_url(self, worker_url):
        if worker_url != WORKER_URL:
            return None
        return SimpleNamespace(
            name=WORKER_ID,
            role="worker",
            registration_validated=True,
            status="online",
        )


def _authorizer():
    return KnowledgeIndexPayloadCapabilityAuthorizer(
        execution_binding_service=_Bindings(),
        manifest_verifier=_Verifier(),
        agent_repository=_Agents(),
        clock_ms=lambda: 10_000,
    )


def test_capability_codec_and_live_assignment_authorization():
    manifest = _manifest()
    encoded = encode_knowledge_index_payload_capability(manifest)

    assert decode_knowledge_index_payload_capability(encoded) == manifest
    assert _authorizer().authorize(
        artifact_id=ARTIFACT_ID,
        artifact_sha256="1" * 64,
        artifact_size_bytes=42,
        artifact_media_type=(
            "application/vnd.ananta.knowledge-index-job+json"
        ),
        manifest=manifest,
        worker_id=WORKER_ID,
        worker_url=WORKER_URL,
    ) == f"knowledge-index-{FINGERPRINT[:32]}"


def test_capability_rejects_artifact_or_assignment_mismatch():
    with pytest.raises(KnowledgeIndexPayloadAuthorizationError):
        _authorizer().authorize(
            artifact_id="artifact-other",
            artifact_sha256="1" * 64,
            artifact_size_bytes=42,
            artifact_media_type=(
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            manifest=_manifest(),
            worker_id=WORKER_ID,
            worker_url=WORKER_URL,
        )

    changed = _manifest()
    changed["assignment_id"] = "assignment-other"
    with pytest.raises(KnowledgeIndexPayloadAuthorizationError):
        _authorizer().authorize(
            artifact_id=ARTIFACT_ID,
            artifact_sha256="1" * 64,
            artifact_size_bytes=42,
            artifact_media_type=(
                "application/vnd.ananta.knowledge-index-job+json"
            ),
            manifest=changed,
            worker_id=WORKER_ID,
            worker_url=WORKER_URL,
        )
