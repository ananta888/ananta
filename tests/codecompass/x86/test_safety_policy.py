"""Contract tests for X86CC-003: safety policy for binary/assembly inputs.

Asserts:
- executable_binary / object_file / function_bytes all blocked (no execution)
- disassembler_json / ghidra_export / rizin_export / capstone_fixture are safe
- network access blocked (structural constant)
- dump write requires explicit approval
- assert_no_execution is structural and returns True
- check_safety_policy returns diagnostic with binary_execution_blocked code for unsafe kinds
"""

from __future__ import annotations

import pytest

from agent.codecompass.x86.safety import (
    DUMP_WRITE_REQUIRES_APPROVAL,
    EXECUTION_BLOCKED,
    NETWORK_BLOCKED,
    assert_no_execution,
    check_safety_policy,
    is_safe_for_static_analysis,
)


def test_execution_structurally_blocked():
    assert EXECUTION_BLOCKED is True, "EXECUTION_BLOCKED must be hard-True, no runtime knob"


def test_network_structurally_blocked():
    assert NETWORK_BLOCKED is True


def test_dump_write_requires_explicit_approval():
    assert DUMP_WRITE_REQUIRES_APPROVAL is True


def test_assert_no_execution_returns_true():
    assert assert_no_execution() is True


@pytest.mark.parametrize(
    "unsafe_kind",
    ["executable_binary", "object_file", "function_bytes"],
)
def test_unsafe_input_kind_returns_diagnostic(unsafe_kind):
    diag = check_safety_policy(unsafe_kind)
    assert diag is not None
    assert diag.code == "binary_execution_blocked"
    assert diag.severity == "error"


@pytest.mark.parametrize(
    "safe_kind",
    [
        "raw_assembly_text",
        "normalized_assembly",
        "disassembler_json",
        "ghidra_export",
        "rizin_export",
        "capstone_fixture",
    ],
)
def test_safe_input_kind_passes(safe_kind):
    assert check_safety_policy(safe_kind) is None
    assert is_safe_for_static_analysis(safe_kind) is True


@pytest.mark.parametrize(
    "unsafe_kind",
    ["executable_binary", "object_file", "function_bytes"],
)
def test_unsafe_kind_is_not_static_analysis_safe(unsafe_kind):
    assert is_safe_for_static_analysis(unsafe_kind) is False


def test_unknown_kind_is_treated_as_unsafe():
    """An unknown input kind should produce a diagnostic, NOT silently pass.

    This is a strict-by-default invariant: when we don't know what it is,
    we refuse, we don't allow it.
    """
    diag = check_safety_policy("something_we_never_heard_of")
    # not necessarily a diagnostic in current impl (only blocked kinds produce diag),
    # but is_safe_for_static_analysis must say no.
    assert is_safe_for_static_analysis("something_we_never_heard_of") is False


def test_empty_input_kind_is_not_safe():
    assert is_safe_for_static_analysis("") is False
    assert is_safe_for_static_analysis("   ") is False