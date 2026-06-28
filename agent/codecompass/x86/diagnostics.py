"""X86CC-005: Diagnostic codes and helpers for the x86 CodeCompass extension."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Reason codes as constants
UNSUPPORTED_X86_PROFILE = "unsupported_x86_profile"
INPUT_CONTEXT_INCOMPLETE = "input_context_incomplete"
DISASSEMBLER_UNAVAILABLE = "disassembler_unavailable"
INVALID_INSTRUCTION_RECORD = "invalid_instruction_record"
CFG_INCOMPLETE = "cfg_incomplete"
ADDRESS_UNRESOLVED = "address_unresolved"
SECTION_UNKNOWN = "section_unknown"
SYMBOL_UNRESOLVED = "symbol_unresolved"
ADAPTER_ERROR = "adapter_error"
INDEX_TRUNCATED = "index_truncated"
BINARY_EXECUTION_BLOCKED = "binary_execution_blocked"

VALID_REASON_CODES = {
    UNSUPPORTED_X86_PROFILE,
    INPUT_CONTEXT_INCOMPLETE,
    DISASSEMBLER_UNAVAILABLE,
    INVALID_INSTRUCTION_RECORD,
    CFG_INCOMPLETE,
    ADDRESS_UNRESOLVED,
    SECTION_UNKNOWN,
    SYMBOL_UNRESOLVED,
    ADAPTER_ERROR,
    INDEX_TRUNCATED,
    BINARY_EXECUTION_BLOCKED,
}

VALID_SEVERITIES = {"error", "warning", "info", "degraded"}


@dataclass
class X86Diagnostic:
    code: str
    severity: str  # error / warning / info / degraded
    message: str
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "provenance": dict(self.provenance),
        }


def x86_diagnostic(
    code: str,
    message: str,
    *,
    severity: str = "error",
    path: str = "",
    line: int = 0,
) -> X86Diagnostic:
    """Create an X86Diagnostic with standard provenance fields."""
    if severity not in VALID_SEVERITIES:
        severity = "error"
    return X86Diagnostic(
        code=code,
        severity=severity,
        message=message,
        provenance={"path": path, "line": line},
    )


def x86_diag_dict(
    code: str,
    message: str,
    *,
    severity: str = "error",
    path: str = "",
    line: int = 0,
) -> dict[str, Any]:
    """Return diagnostic as a plain dict (matches fixture/adapter format)."""
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "path": path,
        "line": line,
    }
