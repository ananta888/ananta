from __future__ import annotations

import copy
import hashlib

import pytest

from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
    SpeechDatasetManifestError,
)
from agent.services.ml_intern_speech_reconciled_dataset_service import (
    MlInternSpeechReconciledDatasetService,
    ReconciledDatasetCandidate,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from ananta_contracts.speech_evidence_governance import canonical_json
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    consent_payload,
    digest,
    manifest_record,
    principal,
)


def test_default_builder_requires_current_explicit_dataset_import_consent() -> None:
    prefix = "manifest-consent-gate"
    consent_service = SpeechEvidenceConsentService()
    denied = consent_service.grant(principal(prefix), consent_payload(prefix))
    record = manifest_record(prefix)
    record["consent_refs"][0] = {
        "consent_id": denied.consent_id,
        "consent_version": denied.consent_version,
        "revocation_epoch": denied.revocation_epoch,
        "consent_digest": denied.consent_digest,
    }
    builder = MlInternSpeechDatasetBuildService(publisher=AcceptPublisher())
    with pytest.raises(SpeechDatasetManifestError) as denied_error:
        builder.build(
            principal(prefix),
            dataset_id=f"dataset-{prefix}",
            records=[record],
            curation_report_digest=digest(f"report-{prefix}"),
        )
    assert denied_error.value.reason_code == "speech_dataset_import_consent_stale"

    allowed_prefix = "manifest-consent-allowed"
    allowed = consent_service.grant(
        principal(allowed_prefix),
        consent_payload(allowed_prefix, grants={"dataset_import": True}),
    )
    allowed_record = manifest_record(allowed_prefix)
    allowed_record["consent_refs"][0] = {
        "consent_id": allowed.consent_id,
        "consent_version": allowed.consent_version,
        "revocation_epoch": allowed.revocation_epoch,
        "consent_digest": allowed.consent_digest,
    }
    manifest, created = builder.build(
        principal(allowed_prefix),
        dataset_id=f"dataset-{allowed_prefix}",
        records=[allowed_record],
        curation_report_digest=digest(f"report-{allowed_prefix}"),
    )
    assert created and manifest["manifest_digest"]


def test_manifest_is_content_addressed_immutable_and_roundtrips() -> None:
    prefix = "manifest-roundtrip"
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"speech-dataset-audit-test-key" * 2,
    )
    builder = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
        audit=audit,
    )
    manifest, created = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[manifest_record(f"{prefix}-a"), manifest_record(f"{prefix}-b", group_suffix="b")],
        curation_report_digest=digest(f"report-{prefix}"),
    )
    replay, replay_created = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[manifest_record(f"{prefix}-a"), manifest_record(f"{prefix}-b", group_suffix="b")],
        curation_report_digest=digest(f"report-{prefix}"),
    )
    assert created and manifest["version"] == f"sha256:{manifest['manifest_digest']}"
    assert not replay_created and replay == manifest
    builder.validate(manifest)
    rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", principal(prefix).tenant_id),
        scope_digest=audit.digest("scope", f"speech-job:dataset-{prefix}"),
        after_event_id=None,
        limit=10,
        now_ms=1_000_000,
    )
    assert [(row.event_type, row.transition) for row in rows] == [("speech_dataset", "published")]


def test_tamper_parent_mismatch_duplicate_lineage_and_local_paths_are_rejected() -> None:
    prefix = "manifest-security"
    builder = MlInternSpeechDatasetBuildService(publisher=AcceptPublisher(), consent_authority=AllowDatasetConsent())
    manifest, _ = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[manifest_record(f"{prefix}-a"), manifest_record(f"{prefix}-b", group_suffix="b")],
        curation_report_digest=digest(f"report-{prefix}"),
    )
    tampered = copy.deepcopy(manifest)
    tampered["records"][0]["duration_ms"] = 999
    with pytest.raises(SpeechDatasetManifestError) as error:
        builder.validate(tampered)
    assert error.value.reason_code == "speech_dataset_manifest_tampered"

    duplicate = manifest_record(f"{prefix}-duplicate")
    with pytest.raises(SpeechDatasetManifestError):
        builder.build(
            principal(f"{prefix}-duplicate"),
            dataset_id=f"dataset-{prefix}-duplicate",
            records=[duplicate, copy.deepcopy(duplicate)],
            curation_report_digest=digest("duplicate-report"),
        )

    path_manifest = copy.deepcopy(manifest)
    path_manifest["records"][0]["field_provenance"]["audio"]["source_digest"] = "/tmp/private.wav"
    with pytest.raises(SpeechDatasetManifestError):
        builder.validate(path_manifest)


def test_parent_chain_requires_exact_current_digest_and_never_mutates_parent() -> None:
    prefix = "manifest-parent-chain"
    builder = MlInternSpeechDatasetBuildService(publisher=AcceptPublisher(), consent_authority=AllowDatasetConsent())
    first, created = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[manifest_record(f"{prefix}-v1")],
        curation_report_digest=digest(f"report-{prefix}-v1"),
    )
    assert created
    parent_snapshot = copy.deepcopy(first)

    with pytest.raises(SpeechDatasetManifestError) as mismatch:
        builder.build(
            principal(prefix),
            dataset_id=f"dataset-{prefix}",
            parent_digest=digest("not-the-current-parent"),
            records=[manifest_record(f"{prefix}-bad")],
            curation_report_digest=digest(f"report-{prefix}-bad"),
        )
    assert mismatch.value.reason_code == "speech_dataset_parent_mismatch"

    second, second_created = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        parent_digest=str(first["manifest_digest"]),
        records=[manifest_record(f"{prefix}-v2")],
        curation_report_digest=digest(f"report-{prefix}-v2"),
    )
    assert second_created and second["parent_digest"] == first["manifest_digest"]
    assert builder.get_by_digest(principal(prefix), str(first["manifest_digest"])) == parent_snapshot

    with pytest.raises(SpeechDatasetManifestError) as stale_parent:
        builder.build(
            principal(prefix),
            dataset_id=f"dataset-{prefix}",
            parent_digest=str(first["manifest_digest"]),
            records=[manifest_record(f"{prefix}-v3")],
            curation_report_digest=digest(f"report-{prefix}-v3"),
        )
    assert stale_parent.value.reason_code == "speech_dataset_parent_mismatch"


def test_first_version_cannot_claim_parent_and_source_lineage_cannot_fork() -> None:
    prefix = "manifest-lineage-fork"
    builder = MlInternSpeechDatasetBuildService(publisher=AcceptPublisher(), consent_authority=AllowDatasetConsent())
    with pytest.raises(SpeechDatasetManifestError) as missing_parent:
        builder.build(
            principal(prefix),
            dataset_id=f"dataset-{prefix}",
            parent_digest=digest("foreign-parent"),
            records=[manifest_record(f"{prefix}-first")],
            curation_report_digest=digest(f"report-{prefix}"),
        )
    assert missing_parent.value.reason_code == "speech_dataset_parent_not_found"

    first = manifest_record(f"{prefix}-source-a", group_suffix="a")
    fork = manifest_record(f"{prefix}-source-b", group_suffix="b")
    fork["source_digest"] = first["source_digest"]
    fork["field_provenance"]["audio"]["source_digest"] = first["source_digest"]
    fork["field_provenance"]["transcript"]["source_digest"] = first["source_digest"]
    with pytest.raises(SpeechDatasetManifestError) as duplicate_lineage:
        builder.build(
            principal(prefix),
            dataset_id=f"dataset-{prefix}",
            records=[first, fork],
            curation_report_digest=digest(f"report-{prefix}-fork"),
        )
    assert duplicate_lineage.value.reason_code == "speech_dataset_duplicate_lineage"


def test_reconciliation_curation_categories_are_disjoint_digest_bound_and_fail_closed() -> None:
    prefix = "manifest-reconciliation-curation"
    builder = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
    )
    service = MlInternSpeechReconciledDatasetService(builder)
    materialized = service.materialize(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        candidates=(
            ReconciledDatasetCandidate("resolved", manifest_record(f"{prefix}-resolved")),
            ReconciledDatasetCandidate(
                "unresolved",
                manifest_record(f"{prefix}-unresolved", group_suffix="unresolved"),
                unresolved_region_ids=(digest(f"{prefix}-region"),),
                disposition_reason="speech_reconciliation_conflicts_unresolved",
            ),
        ),
        reconciliation_digest=digest(f"{prefix}-resolution"),
        parent_digest=None,
        terminal=True,
    )
    manifest = dict(materialized.manifest)
    builder.validate(manifest)
    assert (materialized.resolved_count, materialized.unresolved_count) == (1, 1)
    assert materialized.trainable is False
    assert {record["curation_status"] for record in manifest["records"]} == {
        "resolved",
        "unresolved",
    }

    forged = copy.deepcopy(manifest)
    forged["curation_summary"]["unresolved_count"] = 0
    forged["curation_report_digest"] = hashlib.sha256(canonical_json(forged["curation_summary"])).hexdigest()
    unsigned = {key: value for key, value in forged.items() if key not in {"version", "manifest_digest"}}
    forged["manifest_digest"] = hashlib.sha256(canonical_json(unsigned)).hexdigest()
    forged["version"] = f"sha256:{forged['manifest_digest']}"
    with pytest.raises(SpeechDatasetManifestError) as invalid:
        builder.validate(forged)
    assert invalid.value.reason_code == "speech_dataset_curation_counts_invalid"
