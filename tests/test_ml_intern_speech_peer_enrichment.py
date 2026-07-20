from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.speech_evidence_peer_enrichment_service import (
    SpeechContributorManifest,
    SpeechDatasetVersionReference,
    SpeechEvidencePeerEnrichmentService,
)
from agent.services.speech_evidence_receipt_service import (
    SpeechEvidenceReceiptService,
    verify_admission_receipt,
)
from tests.speech_evidence_sync_support import digest


class _Tasks:
    def __init__(self) -> None:
        self.rows = {}
        self.calls = 0

    def enqueue_once(self, *, idempotency_key, task_type, payload):
        assert task_type == "speech_peer_evidence_curation"
        assert "transcript" not in payload and "local_path" not in payload
        if idempotency_key in self.rows:
            return self.rows[idempotency_key], True
        self.calls += 1
        self.rows[idempotency_key] = "hub-task-peer-curation"
        return self.rows[idempotency_key], False


class _Datasets:
    def __init__(self) -> None:
        self.rows = {}
        self.calls = 0

    def build_child_version_once(
        self,
        *,
        idempotency_key,
        parent_dataset_id,
        parent_version,
        receipt,
        contributors,
    ):
        assert receipt.admission_digest == idempotency_key
        assert parent_dataset_id == "speech-dataset"
        assert contributors[0].field_provenance_digest == digest("fields")
        if idempotency_key not in self.rows:
            self.calls += 1
            self.rows[idempotency_key] = SpeechDatasetVersionReference(
                dataset_id=parent_dataset_id,
                version="v2",
                manifest_digest=digest("manifest-v2"),
                parent_version=parent_version,
            )
        return self.rows[idempotency_key]


def _receipt():
    key = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
    service = SpeechEvidenceReceiptService(key, hub_key_id="hub-key-test", clock_ms=lambda: 1_000_000)
    receipt = service.issue(
        offer_id="offer-test",
        inventory_root_digest=digest("inventory"),
        resolution_digest=digest("resolution"),
        accepted_group_ids=("group-a",),
        rejected_group_ids=("group-b",),
        quarantined_group_ids=("group-c",),
        consent_digest=digest("consent"),
        policy_digest=digest("policy"),
        pair_id="pair-test",
        direction="sender_to_receiver",
    )
    return service, key, receipt


def test_signed_receipt_binds_admission_without_paths_or_content() -> None:
    service, key, receipt = _receipt()
    assert verify_admission_receipt(receipt, key.public_key())
    assert service.issue(
        offer_id="offer-test",
        inventory_root_digest=digest("inventory"),
        resolution_digest=digest("resolution"),
        accepted_group_ids=("group-a",),
        rejected_group_ids=("group-b",),
        quarantined_group_ids=("group-c",),
        consent_digest=digest("consent"),
        policy_digest=digest("policy"),
        pair_id="pair-test",
        direction="sender_to_receiver",
    ) == receipt
    public = receipt.public_dict()
    assert "path" not in " ".join(public)
    assert set(receipt.accepted_group_ids).isdisjoint(receipt.rejected_group_ids)


def test_retry_creates_exactly_one_hub_task_and_at_most_one_child_dataset_version() -> None:
    _service, _key, receipt = _receipt()
    tasks = _Tasks()
    datasets = _Datasets()
    builder = SpeechEvidencePeerEnrichmentService(tasks=tasks, datasets=datasets)
    first = builder.submit(receipt, parent_dataset_id="speech-dataset", parent_version="v1")
    replay = builder.submit(receipt, parent_dataset_id="speech-dataset", parent_version="v1")
    assert first.task_id == replay.task_id
    assert tasks.calls == 1 and replay.duplicate is True

    contributors = (
        SpeechContributorManifest(
            contributor_digest=digest("contributor"),
            direction="sender_to_receiver",
            consent_digest=digest("consent"),
            field_provenance_digest=digest("fields"),
            accepted_group_ids=("group-a",),
        ),
    )
    one = builder.execute_hub_curation(
        receipt,
        parent_dataset_id="speech-dataset",
        parent_version="v1",
        contributors=contributors,
    )
    two = builder.execute_hub_curation(
        receipt,
        parent_dataset_id="speech-dataset",
        parent_version="v1",
        contributors=contributors,
    )
    assert one == two and datasets.calls == 1
    assert one.parent_version == "v1" and one.version == "v2"
    assert not hasattr(builder, "split") and not hasattr(builder, "train") and not hasattr(builder, "approve_adapter")
