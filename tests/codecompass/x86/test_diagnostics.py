"""Contract tests for X86CC-005: x86 diagnostics + degradation model.

Asserts:
- All 11 reason codes are stable (don't accidentally rename)
- Valid severities: error/warning/info/degraded
- x86_diagnostic factory builds X86Diagnostic with provenance
- Invalid severity falls back to error (not silent acceptance)
- x86_diag_dict returns flat dict (adapter fixture format)
"""

from __future__ import annotations

import pytest

from agent.codecompass.x86.diagnostics import (
    ADAPTER_ERROR,
    ADDRESS_UNRESOLVED,
    BINARY_EXECUTION_BLOCKED,
    CFG_INCOMPLETE,
    DISASSEMBLER_UNAVAILABLE,
    INDEX_TRUNCATED,
    INPUT_CONTEXT_INCOMPLETE,
    INVALID_INSTRUCTION_RECORD,
    SECTION_UNKNOWN,
    SYMBOL_UNRESOLVED,
    UNSUPPORTED_X86_PROFILE,
    VALID_REASON_CODES,
    VALID_SEVERITIES,
    X86Diagnostic,
    x86_diag_dict,
    x86_diagnostic,
)


def test_all_eleven_reason_codes_present():
    expected = {
        "unsupported_x86_profile",
        "input_context_incomplete",
        "disassembler_unavailable",
        "invalid_instruction_record",
        "cfg_incomplete",
        "address_unresolved",
        "section_unknown",
        "symbol_unresolved",
        "adapter_error",
        "index_truncated",
        "binary_execution_blocked",
    }
    assert VALID_REASON_CODES == expected
    assert len(VALID_REASON_CODES) == 11


def test_reason_code_constants_match_set():
    """Each constant string is in the set (and vice versa) — pin against silent drift."""
    constants = {
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
    assert constants == VALID_REASON_CODES


def test_valid_severities():
    assert VALID_SEVERITIES == {"error", "warning", "info", "degraded"}


def test_x86_diagnostic_default_severity_is_error():
    diag = x86_diagnostic("cfg_incomplete", "CFG lost tail block")
    assert diag.severity == "error"


def test_x86_diagnostic_includes_provenance():
    diag = x86_diagnostic(
        "address_unresolved", "can't map 0x401014",
        path="/tmp/x.bin", line=42,
    )
    assert diag.provenance["path"] == "/tmp/x.bin"
    assert diag.provenance["line"] == 42


def test_x86_diagnostic_invalid_severity_falls_back_to_error():
    diag = x86_diagnostic("cfg_incomplete", "x", severity="made_up")
    assert diag.severity == "error"


@pytest.mark.parametrize(
    "severity", ["error", "warning", "info", "degraded"]
)
def test_x86_diagnostic_accepts_all_valid_severities(severity):
    diag = x86_diagnostic("cfg_incomplete", "x", severity=severity)
    assert diag.severity == severity


def test_x86_diag_dict_returns_flat_dict():
    d = x86_diag_dict("adapter_error", "fixture missing", path="/x.json", line=10)
    assert d == {
        "code": "adapter_error",
        "severity": "error",
        "message": "fixture missing",
        "path": "/x.json",
        "line": 10,
    }


def test_x86_diagnostic_as_dict_roundtrip():
    diag = x86_diagnostic("index_truncated", "hit max_functions", severity="warning", path="/x", line=0)
    blob = diag.as_dict()
    assert blob["code"] == "index_truncated"
    assert blob["severity"] == "warning"
    assert blob["message"] == "hit max_functions"
    assert blob["provenance"] == {"path": "/x", "line": 0}


def test_invalid_reason_code_not_silently_accepted():
    """An arbitrary string is not in VALID_REASON_CODES — verify the set is closed."""
    assert "my_custom_code" not in VALID_REASON_CODES
    assert "" not in VALID_REASON_CODES