"""Worker-side public-key-only verifier for expert artifacts."""

from __future__ import annotations

from ananta_contracts.parametric_knowledge import KnowledgeExpertBank, KnowledgeExpertManifest
from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing

_MANIFEST_NAMESPACE = "ananta.knowledge-expert-manifest.v1"
_BANK_NAMESPACE = "ananta.knowledge-expert-bank.v1"


class KnowledgeExpertSignatureVerifier:
    def __init__(self, verifier: Ed25519VerificationKeyRing) -> None:
        self._verifier = verifier

    def verify_manifest(self, manifest: KnowledgeExpertManifest) -> None:
        payload = manifest.to_dict()
        payload["signature"] = ""
        self._verifier.verify(
            namespace=_MANIFEST_NAMESPACE,
            payload=payload,
            key_id=manifest.signing_key_id,
            signature=manifest.signature,
            contract_id=manifest.expert_id,
        )

    def verify_bank(self, bank: KnowledgeExpertBank) -> None:
        payload = bank.to_dict()
        payload["signature"] = ""
        self._verifier.verify(
            namespace=_BANK_NAMESPACE,
            payload=payload,
            key_id=bank.signing_key_id,
            signature=bank.signature,
            contract_id=bank.bank_id,
        )


__all__ = ["KnowledgeExpertSignatureVerifier"]
