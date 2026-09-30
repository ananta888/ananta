"""Result types of the pattern gates (PAT-017).

Split out of ``pattern_gate_service`` so code-pattern and notation-pattern
gates share them without importing the service; the service re-exports both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckDetail:
    name: str
    passed: bool
    message: str = ""
    remediation: str = ""


@dataclass
class GateResult:
    pattern_id: str
    language: str
    passed: bool
    checked_files: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    details: list[CheckDetail] = field(default_factory=list)
    remediation_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "passed": self.passed,
            "checked_files": self.checked_files,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "details": [
                {"name": d.name, "passed": d.passed, "message": d.message, "remediation": d.remediation}
                for d in self.details
            ],
            "remediation_hint": self.remediation_hint,
        }


__all__ = ["CheckDetail", "GateResult"]
