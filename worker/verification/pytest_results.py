"""Bounded machine-readable result contract for verification Pytest runs."""

from __future__ import annotations

import json
from dataclasses import dataclass

RESULT_PREFIX = "ANANTA_VERIFICATION_PYTEST_RESULT="


@dataclass(frozen=True, slots=True)
class PytestRunSummary:
    collected: int
    passed: int
    failed: int
    errors: int
    skipped: int
    failed_node_ids: tuple[str, ...]

    @classmethod
    def from_output(cls, output: str) -> "PytestRunSummary | None":
        candidates = [line[len(RESULT_PREFIX) :] for line in output.splitlines() if line.startswith(RESULT_PREFIX)]
        if not candidates:
            return None
        try:
            raw = json.loads(candidates[-1])
        except json.JSONDecodeError:
            return None
        required = {"collected", "passed", "failed", "errors", "skipped", "failed_node_ids"}
        if set(raw) != required:
            return None
        counts = tuple(raw[key] for key in ("collected", "passed", "failed", "errors", "skipped"))
        node_ids = raw["failed_node_ids"]
        if any(type(value) is not int or value < 0 for value in counts):
            return None
        if not isinstance(node_ids, list) or any(not isinstance(item, str) or len(item) > 512 for item in node_ids):
            return None
        return cls(*counts, tuple(node_ids))

    def metadata(self) -> dict[str, int]:
        return {"collection_errors": self.errors, "skipped_tests": self.skipped}


__all__ = ["PytestRunSummary", "RESULT_PREFIX"]
