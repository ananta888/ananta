"""HeuristicProvenanceTracker — enriches and verifies heuristic provenance records.

Provenance record fields:
  - created_by: who created this heuristic (bootstrap / ananta-worker / operator)
  - normalized_from: source format (json / yaml)
  - schema_version: always "heuristic_definition.v1"
  - content_hash: SHA-256 of stable fields (computed by HeuristicNormalizer)
  - derived_from: optional parent heuristic_id (for evolutions)
  - normalized_at: ISO timestamp when normalization occurred
  - activation_ref: audit_event_id from HeuristicActivationGate (set on activation)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProvenanceRecord:
    created_by: str
    normalized_from: str = "json"
    schema_version: str = "heuristic_definition.v1"
    content_hash: str = ""
    derived_from: str | None = None
    normalized_at: str = ""
    activation_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.normalized_at:
            self.normalized_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "created_by": self.created_by,
            "normalized_from": self.normalized_from,
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "normalized_at": self.normalized_at,
        }
        if self.derived_from:
            d["derived_from"] = self.derived_from
        if self.activation_ref:
            d["activation_ref"] = self.activation_ref
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProvenanceRecord":
        return cls(
            created_by=str(d.get("created_by") or "unknown"),
            normalized_from=str(d.get("normalized_from") or "json"),
            schema_version=str(d.get("schema_version") or "heuristic_definition.v1"),
            content_hash=str(d.get("content_hash") or ""),
            derived_from=d.get("derived_from") or None,
            normalized_at=str(d.get("normalized_at") or _now_iso()),
            activation_ref=d.get("activation_ref") or None,
        )


@dataclass
class ProvenanceVerifyResult:
    valid: bool
    expected_hash: str = ""
    actual_hash: str = ""
    reason: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_content_hash(hdef: dict[str, Any]) -> str:
    """Recompute content_hash from stable fields (excludes content_hash and provenance)."""
    hashable = {k: v for k, v in hdef.items() if k not in ("content_hash", "provenance")}
    canonical_bytes = json.dumps(hashable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


class HeuristicProvenanceTracker:
    """Enriches provenance on normalization and verifies content_hash integrity."""

    def enrich(
        self,
        hdef: dict[str, Any],
        *,
        created_by: str = "unknown",
        source_format: str = "json",
        derived_from: str | None = None,
    ) -> dict[str, Any]:
        """Return a copy of hdef with enriched provenance and recomputed content_hash."""
        out = dict(hdef)

        # Recompute content_hash from stable fields
        content_hash = _compute_content_hash(out)
        out["content_hash"] = content_hash

        # Build enriched provenance
        existing = dict(hdef.get("provenance") or {})
        record = ProvenanceRecord(
            created_by=existing.get("created_by") or created_by,
            normalized_from=source_format,
            schema_version="heuristic_definition.v1",
            content_hash=content_hash,
            derived_from=derived_from or existing.get("derived_from"),
            normalized_at=existing.get("normalized_at") or _now_iso(),
            activation_ref=existing.get("activation_ref"),
        )
        out["provenance"] = record.to_dict()
        return out

    def mark_activated(self, hdef: dict[str, Any], activation_ref: str) -> dict[str, Any]:
        """Return a copy of hdef with activation_ref embedded in provenance."""
        out = dict(hdef)
        provenance = dict(out.get("provenance") or {})
        provenance["activation_ref"] = activation_ref
        out["provenance"] = provenance
        return out

    def verify(self, hdef: dict[str, Any]) -> ProvenanceVerifyResult:
        """Verify that content_hash in hdef matches the recomputed hash."""
        stored_hash = str(hdef.get("content_hash") or "")
        if not stored_hash:
            return ProvenanceVerifyResult(valid=False, reason="no_content_hash_in_hdef")

        expected = _compute_content_hash(hdef)
        if stored_hash != expected:
            return ProvenanceVerifyResult(
                valid=False,
                expected_hash=expected,
                actual_hash=stored_hash,
                reason="content_hash_mismatch",
            )
        return ProvenanceVerifyResult(valid=True, expected_hash=expected, actual_hash=stored_hash)

    def extract_record(self, hdef: dict[str, Any]) -> ProvenanceRecord:
        """Extract ProvenanceRecord from a hdef dict."""
        return ProvenanceRecord.from_dict(hdef.get("provenance") or {})
