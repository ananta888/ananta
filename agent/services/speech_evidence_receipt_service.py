"""Hub-signed, content-free admission receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from dataclasses import asdict, dataclass, replace

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@dataclass(frozen=True)
class SpeechEvidenceAdmissionReceipt:
    version: str
    receipt_id: str
    admission_digest: str
    offer_id: str
    inventory_root_digest: str
    resolution_digest: str
    accepted_group_ids: tuple[str, ...]
    rejected_group_ids: tuple[str, ...]
    quarantined_group_ids: tuple[str, ...]
    consent_digest: str
    policy_digest: str
    result_digest: str
    pair_id: str
    direction: str
    issued_at_ms: int
    hub_key_id: str
    signature_b64: str = ""

    def unsigned_dict(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("signature_b64")
        return value

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


class SpeechEvidenceReceiptService:
    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        hub_key_id: str,
        clock_ms,
    ) -> None:
        self._key = private_key
        self._key_id = hub_key_id
        self._clock_ms = clock_ms
        self._receipts: dict[str, SpeechEvidenceAdmissionReceipt] = {}
        self._lock = threading.RLock()

    def public_key_dict(self) -> dict[str, str]:
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return {
            "key_id": self._key_id,
            "algorithm": "Ed25519",
            "public_key_b64": base64.b64encode(raw).decode("ascii"),
        }

    def issue(
        self,
        *,
        admission_digest: str | None = None,
        offer_id: str,
        inventory_root_digest: str,
        resolution_digest: str,
        accepted_group_ids: tuple[str, ...],
        rejected_group_ids: tuple[str, ...],
        quarantined_group_ids: tuple[str, ...],
        consent_digest: str,
        policy_digest: str,
        pair_id: str,
        direction: str,
    ) -> SpeechEvidenceAdmissionReceipt:
        groups = (*accepted_group_ids, *rejected_group_ids, *quarantined_group_ids)
        if len(groups) != len(set(groups)) or len(groups) > 4096:
            raise ValueError("speech_evidence_receipt_groups_invalid")
        for digest in (inventory_root_digest, resolution_digest, consent_digest, policy_digest):
            _digest(digest)
        result_digest = _canonical_digest(
            {
                "accepted": sorted(accepted_group_ids),
                "rejected": sorted(rejected_group_ids),
                "quarantined": sorted(quarantined_group_ids),
            }
        )
        derived_admission_digest = _canonical_digest(
            {
                "offer_id": offer_id,
                "inventory_root_digest": inventory_root_digest,
                "resolution_digest": resolution_digest,
                "consent_digest": consent_digest,
                "policy_digest": policy_digest,
                "result_digest": result_digest,
                "pair_id": pair_id,
                "direction": direction,
            }
        )
        if admission_digest is None:
            admission_digest = derived_admission_digest
        else:
            _digest(admission_digest)
        receipt = SpeechEvidenceAdmissionReceipt(
            version="ananta.speech-evidence-admission-receipt.v1",
            receipt_id=hashlib.sha256(f"receipt\0{admission_digest}".encode()).hexdigest(),
            admission_digest=admission_digest,
            offer_id=offer_id,
            inventory_root_digest=inventory_root_digest,
            resolution_digest=resolution_digest,
            accepted_group_ids=tuple(sorted(accepted_group_ids)),
            rejected_group_ids=tuple(sorted(rejected_group_ids)),
            quarantined_group_ids=tuple(sorted(quarantined_group_ids)),
            consent_digest=consent_digest,
            policy_digest=policy_digest,
            result_digest=result_digest,
            pair_id=pair_id,
            direction=direction,
            issued_at_ms=int(self._clock_ms()),
            hub_key_id=self._key_id,
        )
        with self._lock:
            existing = self._receipts.get(admission_digest)
            if existing is not None:
                return existing
            signed = replace(
                receipt,
                signature_b64=base64.b64encode(self._key.sign(_canonical(receipt.unsigned_dict()))).decode("ascii"),
            )
            self._receipts[admission_digest] = signed
            return signed


def verify_admission_receipt(
    receipt: SpeechEvidenceAdmissionReceipt,
    public_key: Ed25519PublicKey,
) -> bool:
    try:
        public_key.verify(
            base64.b64decode(receipt.signature_b64, validate=True),
            _canonical(receipt.unsigned_dict()),
        )
    except (ValueError, InvalidSignature):
        return False
    return receipt.result_digest == _canonical_digest(
        {
            "accepted": sorted(receipt.accepted_group_ids),
            "rejected": sorted(receipt.rejected_group_ids),
            "quarantined": sorted(receipt.quarantined_group_ids),
        }
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("speech_evidence_receipt_digest_invalid")


__all__ = [
    "SpeechEvidenceAdmissionReceipt",
    "SpeechEvidenceReceiptService",
    "verify_admission_receipt",
]
