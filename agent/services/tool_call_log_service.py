"""ToolCallLogService — TRANS-004

Strukturierter Nachweis für jeden Tool-Call.
Input/Output werden niemals raw gespeichert — nur SHA-256-Hashes.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

# Fields whose values are considered secrets and will be redacted before hashing.
_SECRET_FIELD_KEYWORDS = ("key", "token", "secret", "password")


def _redact_secrets(data: Any) -> Any:
    """Recursively redact dict fields whose name contains a secret keyword."""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(kw in k_lower for kw in _SECRET_FIELD_KEYWORDS):
                result[k] = "REDACTED"
            else:
                result[k] = _redact_secrets(v)
        return result
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data


def _canonical_str(data: Any) -> str:
    """Produce a stable canonical string representation for hashing."""
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(data)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class ToolCallRecord:
    tool_call_id: str
    run_id: str
    worker_id: str | None
    tool_name: str
    timestamp: float
    input_hash: str          # sha256 of canonical (redacted) input
    output_hash: str         # sha256 of canonical output, "blocked", or "redacted"
    policy_decision: str     # "allowed" | "denied" | "redacted"
    duration_ms: int
    status: str              # "success" | "error" | "timeout" | "blocked"
    error_code: str | None
    redaction_applied: bool
    correlation_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "run_id": self.run_id,
            "worker_id": self.worker_id,
            "tool_name": self.tool_name,
            "timestamp": self.timestamp,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "policy_decision": self.policy_decision,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_code": self.error_code,
            "redaction_applied": self.redaction_applied,
            "correlation_id": self.correlation_id,
        }


class ToolCallLogService:
    """Creates structured ToolCallRecords without ever storing raw data."""

    def record(
        self,
        *,
        run_id: str,
        worker_id: str | None,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        policy_decision: str = "allowed",
        status: str = "success",
        error_code: str | None = None,
        duration_ms: int = 0,
        correlation_id: str | None = None,
        redact_output: bool = False,
    ) -> ToolCallRecord:
        """Create a ToolCallRecord. Hashes inputs/outputs; redacts if flagged."""
        # Redact secrets from input before hashing
        redacted_input = _redact_secrets(input_data)
        redaction_applied = redacted_input != input_data

        input_hash = _sha256(_canonical_str(redacted_input))

        # Determine output hash
        if policy_decision == "denied":
            output_hash = "blocked"
        elif redact_output:
            output_hash = "redacted"
            redaction_applied = True
        else:
            output_hash = _sha256(_canonical_str(output_data))

        return ToolCallRecord(
            tool_call_id=str(uuid.uuid4()),
            run_id=str(run_id or ""),
            worker_id=str(worker_id) if worker_id is not None else None,
            tool_name=str(tool_name or ""),
            timestamp=time.time(),
            input_hash=input_hash,
            output_hash=output_hash,
            policy_decision=str(policy_decision or "allowed"),
            duration_ms=max(0, int(duration_ms)),
            status=str(status or "success"),
            error_code=str(error_code) if error_code is not None else None,
            redaction_applied=bool(redaction_applied),
            correlation_id=str(correlation_id) if correlation_id is not None else None,
        )

    def to_jsonl_line(self, record: ToolCallRecord) -> str:
        """Serialize as JSONL line (no trailing newline)."""
        return json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":"))

    def export_run(self, run_id: str, records: list[ToolCallRecord]) -> str:
        """Export all records for a run as JSONL string (one record per line)."""
        lines = [self.to_jsonl_line(r) for r in records if r.run_id == run_id]
        return "\n".join(lines)
