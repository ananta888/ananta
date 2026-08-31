"""Canonical, tamper-evident receipts for scientific skill use."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

from agent.db_models.scientific_skill_provenance import ScientificSkillProvenanceReceiptDB
from agent.repositories.scientific_skill_provenance import ScientificSkillProvenanceRepository

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(?:private[-_ ]?key|password|bearer\s+|(?<![A-Za-z])sk-[A-Za-z0-9]|api[-_]?key\s*[=:])"
)


@dataclass(frozen=True)
class ScientificSkillToolCallEvidence:
    tool_name: str
    target_digest: str
    outcome: str


@dataclass(frozen=True)
class ScientificSkillProvenanceReceipt:
    receipt_digest: str
    payload: dict[str, object]
    verified: bool


class ScientificSkillProvenanceService:
    def __init__(self, repository: ScientificSkillProvenanceRepository) -> None:
        self._repository = repository

    def record(
        self,
        *,
        tenant_id: str,
        project_id: str,
        task_id: str,
        entry_id: str,
        upstream_pin: str,
        skill_sha256: str,
        catalog_digest: str,
        catalog_entry_status: str,
        policy_decision_digest: str,
        approval_request_id: str | None,
        approval_digest: str | None,
        model_id: str,
        tool_calls: tuple[ScientificSkillToolCallEvidence, ...],
        source_references: tuple[str, ...],
        artifact_digests: tuple[str, ...],
        result_digest: str,
        created_at_epoch: float | None = None,
    ) -> ScientificSkillProvenanceReceipt:
        payload: dict[str, object] = {
            "schema": "ananta.scientific-skill-provenance.v2",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "task_id": task_id,
            "entry_id": entry_id,
            "upstream_pin": upstream_pin,
            "skill_sha256": skill_sha256,
            "catalog_digest": catalog_digest,
            "catalog_entry_status": catalog_entry_status,
            "policy_decision_digest": policy_decision_digest,
            "approval_request_id": approval_request_id,
            "approval_digest": approval_digest,
            "model_id": model_id,
            "tool_calls": [call.__dict__ for call in tool_calls],
            "source_references": sorted(source_references),
            "artifact_digests": sorted(artifact_digests),
            "result_digest": result_digest,
        }
        _validate_payload(payload)
        receipt_digest = _digest(payload)
        row = ScientificSkillProvenanceReceiptDB(
            receipt_digest=receipt_digest,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            entry_id=entry_id,
            payload=payload,
            created_at_epoch=float(created_at_epoch if created_at_epoch is not None else time.time()),
        )
        persisted = self._repository.append(row)
        return self.verify(persisted.payload, receipt_digest=persisted.receipt_digest)

    def get_verified(
        self,
        *,
        tenant_id: str,
        project_id: str,
        receipt_digest: str,
    ) -> ScientificSkillProvenanceReceipt | None:
        """Return only same-scope, digest-valid evidence for API projection."""

        row = self._repository.get(
            tenant_id=tenant_id,
            project_id=project_id,
            receipt_digest=receipt_digest,
        )
        if row is None:
            return None
        receipt = self.verify(row.payload, receipt_digest=row.receipt_digest)
        return receipt if receipt.verified else None

    @staticmethod
    def verify(payload: object, *, receipt_digest: str) -> ScientificSkillProvenanceReceipt:
        if not isinstance(payload, dict):
            return ScientificSkillProvenanceReceipt(receipt_digest, {}, False)
        try:
            _validate_payload(payload)
            verified = _DIGEST.fullmatch(receipt_digest) is not None and _digest(payload) == receipt_digest
        except (TypeError, ValueError):
            verified = False
        return ScientificSkillProvenanceReceipt(receipt_digest, dict(payload), verified)


def _validate_payload(payload: dict[str, object]) -> None:
    base_required = {
        "schema", "tenant_id", "project_id", "task_id", "entry_id", "upstream_pin",
        "skill_sha256", "catalog_digest", "policy_decision_digest", "approval_request_id",
        "approval_digest", "model_id", "tool_calls", "source_references", "artifact_digests", "result_digest",
    }
    schema = payload.get("schema")
    required = (
        base_required
        if schema == "ananta.scientific-skill-provenance.v1"
        else base_required | {"catalog_entry_status"}
    )
    if set(payload) != required or schema not in {
        "ananta.scientific-skill-provenance.v1",
        "ananta.scientific-skill-provenance.v2",
    }:
        raise ValueError("scientific_skill_receipt_incomplete")
    if schema == "ananta.scientific-skill-provenance.v2" and payload.get(
        "catalog_entry_status"
    ) not in {"approved", "disabled"}:
        raise ValueError("scientific_skill_receipt_catalog_status_invalid")
    for name in ("skill_sha256", "catalog_digest", "policy_decision_digest", "result_digest"):
        if not isinstance(payload.get(name), str) or _DIGEST.fullmatch(str(payload[name])) is None:
            raise ValueError("scientific_skill_receipt_digest_invalid")
    approval_digest = payload.get("approval_digest")
    if approval_digest is not None and (
        not isinstance(approval_digest, str)
        or _DIGEST.fullmatch(approval_digest) is None
    ):
        raise ValueError("scientific_skill_receipt_digest_invalid")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if _SECRET.search(serialized):
        raise ValueError("scientific_skill_receipt_secret_material_denied")


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ScientificSkillProvenanceReceipt",
    "ScientificSkillProvenanceService",
    "ScientificSkillToolCallEvidence",
]
