"""Real SQL Registry issuance, with explicit synthetic scheduler authority."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.db_models.evidence_identity import HubSourceEvidenceIdentityDB
from agent.services.pi_runtime_manifest_admission import PiRuntimeManifestAdmissionService
from tests.hub.test_hub_evidence_registry_service import service as service  # noqa: F401
from tests.pi.test_pi_runtime_manifest import manifest


def test_manifest_source_is_issued_only_by_real_registry_and_replay_is_immutable(service):
    expected = manifest()
    calls = []

    def observed(**identity):
        calls.append(identity)
        return expected

    admission = PiRuntimeManifestAdmissionService(
        deployments=SimpleNamespace(require_manifest=observed), registry=service,
    )
    first = admission.admit(manifest=expected, policy_digest="c" * 64)
    assert admission.admit(manifest=expected, policy_digest="c" * 64) == first
    assert first.source.source_id.startswith("SRC_")
    assert first.source.synthetic and first.source.evidence_scope == "test"
    assert first.source.content_digest == expected.digest
    assert calls[0] == {key: getattr(expected, key) for key in (
        "tenant_id", "project_id", "worker_id", "worker_url",
    )}
    assert service.require_source_identity(
        tenant_id=expected.tenant_id, project_id=expected.project_id,
        source_id=first.source.source_id, expected_binding_digest=first.source.binding_digest,
    ) == first.source


@pytest.mark.parametrize("field,value", [
    ("worker_id", "foreign"), ("project_id", "foreign"), ("repository_revision", "d" * 40),
    ("image_digest", "sha256:" + "d" * 64), ("node_version", "24.19.0"),
    ("synthetic", False), ("evidence_scope", "local"),
])
def test_different_scheduler_facts_cannot_issue_source(service, field, value):
    expected = manifest()
    changes = {field: value}
    if field == "evidence_scope":
        changes["synthetic"] = False
    admission = PiRuntimeManifestAdmissionService(
        deployments=SimpleNamespace(require_manifest=lambda **identity: replace(expected, **changes)), registry=service,
    )
    with pytest.raises(ValueError, match="deployment_binding_mismatch"):
        admission.admit(manifest=expected, policy_digest="c" * 64)
    with Session(service._repository._database) as session:
        assert session.exec(select(HubSourceEvidenceIdentityDB)).all() == []


def test_unavailable_deployment_cannot_issue_source(service):
    def unavailable(**identity):
        raise ValueError("pi_runtime_deployment_unavailable")

    admission = PiRuntimeManifestAdmissionService(
        deployments=SimpleNamespace(require_manifest=unavailable), registry=service,
    )
    with pytest.raises(ValueError, match="deployment_unavailable"):
        admission.admit(manifest=manifest(), policy_digest="c" * 64)
    with Session(service._repository._database) as session:
        assert session.exec(select(HubSourceEvidenceIdentityDB)).all() == []


@pytest.mark.parametrize("change", ["none", "policy", "origin", "content", "scope", "deployment"])
def test_pinned_manifest_lookup_checks_source_and_fresh_deployment_without_issuing(service, change):
    expected = manifest()
    deployment = [expected]
    admission = PiRuntimeManifestAdmissionService(
        deployments=SimpleNamespace(require_manifest=lambda **identity: deployment[0]), registry=service,
    )
    fields = {
        "tenant_id": expected.tenant_id, "project_id": expected.project_id,
        "origin_type": "pi_runtime_deployment", "origin_digest": expected.digest,
        "content_digest": expected.digest, "policy_digest": "c" * 64, "evidence_scope": "test", "synthetic": True,
    }
    if change == "policy":
        fields["policy_digest"] = "d" * 64
    elif change == "origin":
        fields["origin_type"] = "unrelated_document"
    elif change == "content":
        fields["content_digest"] = "d" * 64
    elif change == "scope":
        fields.update(evidence_scope="local", synthetic=False)
    elif change == "deployment":
        deployment[0] = replace(expected, image_digest="sha256:" + "d" * 64)
    source = service.register_source(**fields)
    values = {"manifest": expected, "source_id": source.source_id,
              "source_binding_digest": source.binding_digest, "policy_digest": "c" * 64}
    if change == "none":
        assert admission.require_admitted(**values).source == source
    else:
        with pytest.raises(ValueError, match="pi_runtime_(source|deployment)_binding_mismatch"):
            admission.require_admitted(**values)
    with Session(service._repository._database) as session:
        assert len(session.exec(select(HubSourceEvidenceIdentityDB)).all()) == 1
