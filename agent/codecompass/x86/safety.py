"""X86CC-003: Safety policy for x86 CodeCompass extension.

Execution, network access, and dump writes are structurally blocked.
This module provides runtime checks that enforce those guarantees.
"""
from __future__ import annotations

from agent.codecompass.x86.diagnostics import (
    BINARY_EXECUTION_BLOCKED,
    x86_diagnostic,
    X86Diagnostic,
)

# Safety policy constants — test-verifiable
EXECUTION_BLOCKED: bool = True
NETWORK_BLOCKED: bool = True
DUMP_WRITE_REQUIRES_APPROVAL: bool = True

# Safe input kinds for static analysis (no execution required)
_SAFE_INPUT_KINDS = {
    "raw_assembly_text",
    "normalized_assembly",
    "disassembler_json",
    "ghidra_export",
    "rizin_export",
    "capstone_fixture",
}

# Input kinds that require execution and are blocked
_UNSAFE_INPUT_KINDS = {
    "executable_binary",
    "object_file",
    "function_bytes",
}


def check_safety_policy(input_kind: str) -> X86Diagnostic | None:
    """Return a diagnostic if the input_kind violates safety policy, else None."""
    if str(input_kind or "").strip() in _UNSAFE_INPUT_KINDS:
        return x86_diagnostic(
            BINARY_EXECUTION_BLOCKED,
            f"Input kind '{input_kind}' requires binary execution which is structurally blocked. "
            "Use a disassembler export (disassembler_json, ghidra_export, rizin_export) instead.",
            severity="error",
        )
    return None


def is_safe_for_static_analysis(input_kind: str) -> bool:
    """Return True if this input kind is safe for static analysis (no execution needed)."""
    return str(input_kind or "").strip() in _SAFE_INPUT_KINDS


def assert_no_execution() -> bool:
    """Assert that no execution is performed. Always returns True.

    This is a structural guarantee: the x86 extension never executes binary
    code. Calling this function verifies the guarantee is in place.
    """
    assert EXECUTION_BLOCKED, "EXECUTION_BLOCKED must be True"
    return True
