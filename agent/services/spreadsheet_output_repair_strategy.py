"""Bounded syntax-only repair for Spreadsheet action JSON."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadsheetOutputRepair:
    text: str
    applied: bool
    reason_code: str | None
    original_digest: str
    repaired_digest: str


class SpreadsheetOutputRepairStrategy:
    """Remove at most one Markdown JSON fence; never alter action semantics."""

    MAX_BYTES = 1_048_576

    def repair(self, value: str) -> SpreadsheetOutputRepair:
        text = str(value)
        if len(text.encode("utf-8")) > self.MAX_BYTES:
            raise ValueError("spreadsheet_output_repair_size_exceeded")
        original_digest = hashlib.sha256(text.encode()).hexdigest()
        stripped = text.strip()
        repaired = stripped
        applied = False
        reason_code = None
        if stripped.startswith("```json\n") and stripped.endswith("\n```") and stripped.count("```") == 2:
            repaired = stripped[8:-4].strip()
            applied = True
            reason_code = "spreadsheet_output_json_fence_removed"
        return SpreadsheetOutputRepair(
            text=repaired,
            applied=applied,
            reason_code=reason_code,
            original_digest=original_digest,
            repaired_digest=hashlib.sha256(repaired.encode()).hexdigest(),
        )


__all__ = ["SpreadsheetOutputRepair", "SpreadsheetOutputRepairStrategy"]
