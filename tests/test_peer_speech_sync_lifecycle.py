from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.services.speech_evidence_peer_enrichment_service import (
    SpeechContributorManifest,
    SpeechDatasetVersionReference,
    SpeechEvidencePeerEnrichmentService,
)
from agent.services.speech_evidence_receipt_service import SpeechEvidenceReceiptService
from tests.speech_evidence_sync_support import digest
from voice_runtime.evidence_inventory import (
    EvidenceConsentScope,
    EvidenceInventoryBuilder,
    EvidenceLeaf,
)
from voice_runtime.peer_evidence_admission import (
    InMemoryRecipientQuarantineRepository,
    LocalEvidenceValidation,
    RecipientPeerEvidenceQuarantine,
)


class _Keys:
    key = b"q" * 32

    def resolve(self, **_scope):
        return self.key

    def destroy(self, **_scope):
        return None


class _Validator:
    def validate(self, plaintext, **_scope):
        payload = bytes(plaintext)
        return LocalEvidenceValidation(
            schema_valid=True,
            signature_valid=True,
            consent_valid=True,
            speaker_scope_valid=True,
            resolution_valid=True,
            quality_valid=True,
            source_group_valid=True,
            content_digest=hashlib.sha256(payload).hexdigest(),
            feature_digest=digest("features"),
            reason_codes=(),
        )


class _Tasks:
    def __init__(self):
        self.rows = {}

    def enqueue_once(self, *, idempotency_key, **_values):
        duplicate = idempotency_key in self.rows
        self.rows.setdefault(idempotency_key, "hub-curation-task")
        return self.rows[idempotency_key], duplicate


class _Datasets:
    def __init__(self):
        self.rows = {}

    def build_child_version_once(self, *, idempotency_key, parent_dataset_id, parent_version, **_values):
        self.rows.setdefault(
            idempotency_key,
            SpeechDatasetVersionReference(
                parent_dataset_id,
                "v2",
                digest("manifest-v2"),
                parent_version,
            ),
        )
        return self.rows[idempotency_key]


@pytest.mark.parametrize("transport_mode", ["p2p", "relay"])
def test_encrypted_peer_sync_duplicate_quarantine_curation_receipt_and_dataset_lineage(transport_mode) -> None:
    scope = EvidenceConsentScope(
        pair_id="pair-test",
        direction="sender_to_receiver",
        purpose="speech_dataset_curation",
        data_classes=frozenset({"text_corrections"}),
        consent_version=3,
        retention_until_ms=3_000_000,
        epoch=7,
    )
    inventory = EvidenceInventoryBuilder(pair_key=b"p" * 32).build(
        (
            EvidenceLeaf(
                group_id="private-group-name",
                pair_id="pair-test",
                direction="sender_to_receiver",
                purpose="speech_dataset_curation",
                data_class="text_corrections",
                payload_digest=digest("peer-evidence"),
                size_bytes=32,
                consent_version=3,
                retention_until_ms=2_000_000,
                epoch=7,
            ),
        ),
        scope=scope,
        now_ms=1_000_000,
    )
    group_id = next(iter(inventory.leaves))
    clear = f"encrypted-{transport_mode}-evidence".encode()
    nonce = b"n" * 12
    aad = f"pair-test:{transport_mode}:7".encode()
    ciphertext = AESGCM(_Keys.key).encrypt(nonce, clear, aad)
    quarantine = RecipientPeerEvidenceQuarantine(
        keys=_Keys(),
        validator=_Validator(),
        repository=InMemoryRecipientQuarantineRepository(),
        clock_ms=lambda: 1_000_000,
    )
    values = {
        "pair_id": "pair-test",
        "offer_id": "offer-test",
        "group_id": group_id,
        "sender_id": "peer-a",
        "speaker_digest": digest("speaker"),
        "source_group_digest": digest("source-group"),
        "consent_digest": digest("consent"),
        "resolution_digest": digest("resolution"),
        "payload_digest": hashlib.sha256(clear).hexdigest(),
        "ciphertext": ciphertext,
        "nonce": nonce,
        "aad": aad,
        "key_id": "pair-key",
        "epoch": 7,
        "retention_until_ms": 2_000_000,
    }
    record = quarantine.quarantine_encrypted(**values)
    assert quarantine.quarantine_encrypted(**values).record_id == record.record_id
    checked, validation = quarantine.pre_admit(record.record_id)
    checked = quarantine.transition(
        checked.record_id,
        target="accepted",
        decision_digest=checked.decision_digest or digest("pre-admission"),
        reason_codes=validation.reason_codes,
    )
    assert checked.state == "accepted"

    receipt_service = SpeechEvidenceReceiptService(
        Ed25519PrivateKey.from_private_bytes(b"r" * 32),
        hub_key_id="hub-key",
        clock_ms=lambda: 1_000_000,
    )
    receipt = receipt_service.issue(
        offer_id="offer-test",
        inventory_root_digest=inventory.root_digest,
        resolution_digest=digest("resolution"),
        accepted_group_ids=(group_id,),
        rejected_group_ids=(),
        quarantined_group_ids=(),
        consent_digest=digest("consent"),
        policy_digest=digest("policy"),
        pair_id="pair-test",
        direction="sender_to_receiver",
    )
    tasks = _Tasks()
    datasets = _Datasets()
    enrichment = SpeechEvidencePeerEnrichmentService(tasks=tasks, datasets=datasets)
    one = enrichment.submit(receipt, parent_dataset_id="speech-dataset", parent_version="v1")
    two = enrichment.submit(receipt, parent_dataset_id="speech-dataset", parent_version="v1")
    assert one.task_id == two.task_id and two.duplicate
    child = enrichment.execute_hub_curation(
        receipt,
        parent_dataset_id="speech-dataset",
        parent_version="v1",
        contributors=(
            SpeechContributorManifest(
                contributor_digest=digest("peer-a"),
                direction="sender_to_receiver",
                consent_digest=digest("consent"),
                field_provenance_digest=digest("fields"),
                accepted_group_ids=(group_id,),
            ),
        ),
    )
    assert child.parent_version == "v1" and child.version == "v2"
    assert len(tasks.rows) == len(datasets.rows) == 1
