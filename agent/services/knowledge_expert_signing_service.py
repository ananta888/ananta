"""Hub-only signing of admitted knowledge-expert artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.parametric_knowledge import KnowledgeExpertBank, KnowledgeExpertManifest
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing

_MANIFEST_NAMESPACE = "ananta.knowledge-expert-manifest.v1"
_BANK_NAMESPACE = "ananta.knowledge-expert-bank.v1"


class KnowledgeExpertSigningService:
    def __init__(self, signer: Ed25519SigningKeyRing) -> None:
        self._signer = signer

    def sign_manifest(self, raw: Mapping[str, Any]) -> KnowledgeExpertManifest:
        payload = dict(raw)
        payload["signing_key_id"] = self._signer.active_key_id
        payload["signature"] = ""
        key_id, signature = self._signer.sign(
            namespace=_MANIFEST_NAMESPACE,
            payload=payload,
        )
        payload["signing_key_id"] = key_id
        payload["signature"] = signature
        return KnowledgeExpertManifest.from_mapping(payload)

    def sign_bank(self, raw: Mapping[str, Any]) -> KnowledgeExpertBank:
        payload = dict(raw)
        payload["signing_key_id"] = self._signer.active_key_id
        payload["signature"] = ""
        key_id, signature = self._signer.sign(namespace=_BANK_NAMESPACE, payload=payload)
        payload["signing_key_id"] = key_id
        payload["signature"] = signature
        return KnowledgeExpertBank.from_mapping(payload)


__all__ = ["KnowledgeExpertSigningService"]
