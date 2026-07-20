"""Consent-filtered, pair-keyed speech-evidence inventory builder."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

from voice_runtime.evidence_merkle import merkle_root

MAX_INVENTORY_LEAVES = 100_000
MAX_INVENTORY_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class EvidenceLeaf:
    group_id: str
    pair_id: str
    direction: str
    purpose: str
    data_class: str
    payload_digest: str
    size_bytes: int
    consent_version: int
    retention_until_ms: int
    epoch: int
    revoked: bool = False


@dataclass(frozen=True)
class EvidenceConsentScope:
    pair_id: str
    direction: str
    purpose: str
    data_classes: frozenset[str]
    consent_version: int
    retention_until_ms: int
    epoch: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "pair_id": self.pair_id,
                    "direction": self.direction,
                    "purpose": self.purpose,
                    "data_classes": sorted(self.data_classes),
                    "consent_version": self.consent_version,
                    "retention_until_ms": self.retention_until_ms,
                    "epoch": self.epoch,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class SpeechEvidenceInventory:
    root_digest: str
    leaf_count: int
    total_bytes: int
    scope_digest: str
    consent_version: int
    epoch: int
    retention_until_ms: int
    leaves: Mapping[str, str]
    cursor_digest: str


class EvidenceInventoryBuilder:
    def __init__(self, *, pair_key: bytes) -> None:
        if len(pair_key) < 32:
            raise ValueError("speech_evidence_pair_key_invalid")
        self._pair_key = bytes(pair_key)

    def build(
        self,
        leaves: Iterable[EvidenceLeaf],
        *,
        scope: EvidenceConsentScope,
        now_ms: int,
    ) -> SpeechEvidenceInventory:
        admitted: dict[str, str] = {}
        total_bytes = 0
        for leaf in leaves:
            if not self._allowed(leaf, scope=scope, now_ms=now_ms):
                continue
            if len(admitted) >= MAX_INVENTORY_LEAVES:
                raise ValueError("speech_evidence_inventory_leaf_limit")
            if leaf.size_bytes < 0 or total_bytes + leaf.size_bytes > MAX_INVENTORY_BYTES:
                raise ValueError("speech_evidence_inventory_byte_limit")
            opaque_id = self._opaque_group_id(leaf.group_id)
            digest = self._leaf_digest(opaque_id, leaf)
            existing = admitted.get(opaque_id)
            if existing is not None and existing != digest:
                raise ValueError("speech_evidence_inventory_group_collision")
            admitted[opaque_id] = digest
            total_bytes += leaf.size_bytes
        root = merkle_root(admitted.items())
        cursor = hmac.new(
            self._pair_key,
            (
                "ananta.speech-evidence-inventory-cursor.v1\0"
                f"{root}\0{scope.digest}\0{scope.consent_version}\0{scope.epoch}"
            ).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        return SpeechEvidenceInventory(
            root_digest=root,
            leaf_count=len(admitted),
            total_bytes=total_bytes,
            scope_digest=scope.digest,
            consent_version=scope.consent_version,
            epoch=scope.epoch,
            retention_until_ms=scope.retention_until_ms,
            leaves=MappingProxyType(admitted),
            cursor_digest=cursor,
        )

    @staticmethod
    def _allowed(leaf: EvidenceLeaf, *, scope: EvidenceConsentScope, now_ms: int) -> bool:
        return (
            not leaf.revoked
            and leaf.pair_id == scope.pair_id
            and leaf.direction == scope.direction
            and leaf.purpose == scope.purpose
            and leaf.data_class in scope.data_classes
            and leaf.consent_version == scope.consent_version
            and leaf.epoch == scope.epoch
            and now_ms < leaf.retention_until_ms <= scope.retention_until_ms
            and len(leaf.payload_digest) == 64
        )

    def _opaque_group_id(self, group_id: str) -> str:
        return hmac.new(
            self._pair_key,
            f"ananta.speech-evidence-group.v1\0{group_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _leaf_digest(self, opaque_id: str, leaf: EvidenceLeaf) -> str:
        return hmac.new(
            self._pair_key,
            (
                "ananta.speech-evidence-leaf.v1\0"
                f"{opaque_id}\0{leaf.payload_digest}\0{leaf.size_bytes}\0"
                f"{leaf.data_class}\0{leaf.consent_version}\0{leaf.retention_until_ms}\0{leaf.epoch}"
            ).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()


__all__ = [
    "EvidenceConsentScope",
    "EvidenceInventoryBuilder",
    "EvidenceLeaf",
    "SpeechEvidenceInventory",
]
