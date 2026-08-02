from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "worker" / "task_followup_proposal.v1.json"
_MAX_ENVELOPE_BYTES = 256 * 1024


def proposal_payload_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def proposal_envelope_digest(envelope: dict[str, Any]) -> str:
    encoded = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class WorkerTaskProposalContractService:
    """Pure schema, size and digest validation for untrusted Worker evidence."""

    def validate(self, envelope: dict[str, Any]) -> dict[str, Any]:
        candidate = dict(envelope or {})
        rendered = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        issues: list[dict[str, str]] = []
        if len(rendered) > _MAX_ENVELOPE_BYTES:
            issues.append(self._issue("$", "worker_task_proposal_too_large", "Proposal exceeds 256 KiB."))
        if not _SCHEMA_PATH.exists():
            issues.append(self._issue("$", "worker_task_proposal_schema_missing", "Proposal schema is unavailable."))
        else:
            schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            for error in sorted(Draft202012Validator(schema).iter_errors(candidate), key=lambda row: list(row.path)):
                issues.append(
                    self._issue(
                        "/".join(map(str, error.path)) or "$",
                        "worker_task_proposal_schema_invalid",
                        error.message,
                    )
                )
        payload = dict(candidate.get("payload") or {})
        actual_digest = proposal_payload_digest(payload)
        if str(candidate.get("payload_digest") or "") != actual_digest:
            issues.append(
                self._issue("payload_digest", "worker_task_proposal_digest_mismatch", "Payload digest does not match.")
            )
        return {
            "valid": not issues,
            "issues": issues,
            "envelope": candidate,
            "payload_digest": actual_digest,
            "envelope_digest": proposal_envelope_digest(candidate),
            "size_bytes": len(rendered),
        }

    @staticmethod
    def _issue(path: str, reason_code: str, message: str) -> dict[str, str]:
        return {"path": path, "reason_code": reason_code, "human_message": message}


__all__ = [
    "WorkerTaskProposalContractService",
    "proposal_envelope_digest",
    "proposal_payload_digest",
]
