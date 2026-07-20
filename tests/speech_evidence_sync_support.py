from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ananta_contracts.speech_evidence_sync import (
    GROUP_PREVIEW_VERSION,
    OFFER_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    canonical_sha256,
    group_preview_comparison_digest,
    group_preview_group_id,
    group_preview_resolution_digest,
    sign_message,
)

PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x11" * 32)
NOW_MS = 2_000_000


class StaticEvidenceKeys:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def resolve(self, **_scope):
        return PRIVATE_KEY.public_key() if self.available else None


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def comparison_preview(source_group_digest: str, revision: int, label: str) -> dict[str, Any]:
    candidate = {
        "ordinal": 1,
        "candidate_digest": digest(f"{label}:candidate"),
        "authority_digest": digest(f"{label}:authority"),
        "revision": revision,
    }
    return {
        "original_candidates": [candidate],
        "resolution_state": "resolved",
        "selected_candidate_digest": candidate["candidate_digest"],
        "unresolved_region_digests": [],
        "comparison_digest": group_preview_comparison_digest(
            source_group_digest=source_group_digest,
            revision=revision,
            original_candidates=[candidate],
            resolution_state="resolved",
            selected_candidate_digest=str(candidate["candidate_digest"]),
            unresolved_region_digests=[],
        ),
    }


def payload(message_type: str) -> dict[str, Any]:
    group_a_source = digest("source-group-a")
    group_b_source = digest("source-group-b")
    group_a = group_preview_group_id(group_a_source, 1)
    group_b = group_preview_group_id(group_b_source, 2)
    speaker_scope = canonical_sha256(
        {
            "domain": "ananta.speech-evidence-speaker-scope.v1",
            "epoch": 7,
            "pair_id": "pair-test",
            "speaker_id": "peer-a",
        }
    )
    quality_policy = canonical_sha256(
        {
            "accepted_source_states": ["corrected", "correction_failed", "final"],
            "domain": "ananta.speech-evidence-quality-policy.v1",
            "policy": "final-or-reviewed-transcript",
        }
    )
    common: dict[str, dict[str, Any]] = {
        "inventory": {
            "traffic_class": "control",
            "inventory_id": "inventory-test",
            "root_digest": digest("root"),
            "leaf_count": 2,
            "total_bytes": 128,
            "scope_digest": digest("scope"),
            "retention_until_ms": 3_000_000,
            "cursor_digest": digest("cursor"),
        },
        "diff": {
            "traffic_class": "control",
            "base_root_digest": digest("base-root"),
            "target_root_digest": digest("target-root"),
            "missing_group_ids": ["group-missing"],
            "changed_group_ids": ["group-changed"],
            "cursor_digest": digest("cursor"),
            "complete": True,
            "total_groups": 2,
        },
        "offer": {
            "traffic_class": "control",
            "offer_id": "offer-test",
            "stage": "proposal",
            "inventory_root_digest": digest("root"),
            "direction": "sender_to_receiver",
            "purpose": "speech_dataset_curation",
            "data_classes": ["text_corrections", "vocabulary"],
            "fields": ["transcript", "timing"],
            "retention_seconds": 3600,
            "trainer_class": "speech_adaptation",
            "group_ids": [group_a, group_b],
            "group_previews": [
                {
                    "preview_version": GROUP_PREVIEW_VERSION,
                    "group_id": group_a,
                    "source_group_digest": group_a_source,
                    "speaker_scope_digest": speaker_scope,
                    "quality_basis": "policy",
                    "quality_digest": quality_policy,
                    "resolution_digest": group_preview_resolution_digest(group_a_source, 1),
                    **comparison_preview(group_a_source, 1, "group-a"),
                    "revision": 1,
                    "size_bytes": 64,
                },
                {
                    "preview_version": GROUP_PREVIEW_VERSION,
                    "group_id": group_b,
                    "source_group_digest": group_b_source,
                    "speaker_scope_digest": speaker_scope,
                    "quality_basis": "policy",
                    "quality_digest": quality_policy,
                    "resolution_digest": group_preview_resolution_digest(group_b_source, 2),
                    **comparison_preview(group_b_source, 2, "group-b"),
                    "revision": 2,
                    "size_bytes": 64,
                },
            ],
            "total_bytes": 128,
            "sender_consent_digest": digest("sender-consent"),
            "recipient_consent_digest": digest("recipient-consent"),
            "scope_digest": digest("scope"),
        },
        "chunk": {
            "traffic_class": "evidence_bulk",
            "offer_id": "offer-test",
            "group_id": "group-a",
            "chunk_index": 0,
            "chunk_count": 1,
            "plaintext_bytes": 4,
            "plaintext_digest": hashlib.sha256(b"test").hexdigest(),
            "ciphertext_digest": hashlib.sha256(b"x" * 20).hexdigest(),
            "nonce_b64": base64.b64encode(b"n" * 12).decode(),
            "ciphertext_b64": base64.b64encode(b"x" * 20).decode(),
        },
        "chunk_ack": {
            "traffic_class": "control",
            "offer_id": "offer-test",
            "group_id": "group-a",
            "acknowledged_indices": [0],
            "first_missing_index": 1,
            "received_bytes": 4,
            "complete": True,
        },
        "resolution": {
            "traffic_class": "control",
            "resolution_id": "resolution-test",
            "policy_version": "policy-v1",
            "graph_digest": digest("graph"),
            "candidate_ids": ["candidate-a", "candidate-b"],
            "accepted_candidate_ids": ["candidate-a"],
            "unresolved_region_ids": [],
            "result_digest": digest("resolution-result"),
            "candidates": [
                {
                    "candidate_id": "candidate-a",
                    "source_id": "source-a",
                    "contributor_digest": digest("contributor-a"),
                    "revision": 1,
                    "lineage_digest": digest("lineage-a"),
                    "text": "Guten Tag.",
                    "confidence": 0.9,
                    "start_ms": 0,
                    "end_ms": 1000,
                },
                {
                    "candidate_id": "candidate-b",
                    "source_id": "source-b",
                    "contributor_digest": digest("contributor-b"),
                    "revision": 1,
                    "lineage_digest": digest("lineage-b"),
                    "text": "Guten Tag!",
                    "confidence": 0.8,
                    "start_ms": 0,
                    "end_ms": 1000,
                },
            ],
        },
        "receipt": {
            "traffic_class": "control",
            "receipt_id": "receipt-test",
            "offer_id": "offer-test",
            "inventory_root_digest": digest("root"),
            "resolution_digest": digest("resolution"),
            "accepted_group_ids": ["group-a"],
            "rejected_group_ids": ["group-b"],
            "quarantined_group_ids": [],
            "consent_digest": digest("consent"),
            "policy_digest": digest("policy"),
            "result_digest": digest("result"),
        },
        "revocation": {
            "traffic_class": "control",
            "revocation_id": "revocation-test",
            "group_ids": ["group-a"],
            "scope_digest": digest("scope"),
            "reason_code": "consent_revoked",
            "revocation_epoch": 2,
            "deadline_at_ms": 2_100_000,
            "requested_action": "delete",
        },
        "revocation_ack": {
            "traffic_class": "control",
            "revocation_id": "revocation-test",
            "scope_digest": digest("scope"),
            "revocation_epoch": 2,
            "impact_digest": digest("impact"),
            "group_results": [
                {"group_id": "group-a", "state": "deleted", "reason_code": "local_cleanup_complete"}
            ],
            "decision": "complete",
        },
    }
    return common[message_type]


def message(
    message_type: str = "inventory",
    *,
    sequence: int = 1,
    sender_id: str = "peer-a",
    audience_id: str = "peer-b",
    issued_at_ms: int = NOW_MS - 1_000,
    expires_at_ms: int = NOW_MS + 60_000,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = payload(message_type)
    if payload_override:
        body.update(payload_override)
    unsigned = {
        "protocol_version": OFFER_PROTOCOL_VERSION if message_type == "offer" else PROTOCOL_VERSION,
        "message_type": message_type,
        "message_id": f"message-{message_type}-{sequence}",
        "session_id": "session-test",
        "pair_id": "pair-test",
        "sender_id": sender_id,
        "audience_id": audience_id,
        "epoch": 7,
        "sequence": sequence,
        "consent_version": 3,
        "key_id": "peer-key-test",
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "payload_digest": canonical_sha256(body),
        "payload": body,
        "signature_algorithm": "Ed25519",
    }
    return sign_message(unsigned, PRIVATE_KEY)
