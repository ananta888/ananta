"""Contract tests for X86CC-004: input source taxonomy + X86InputRecord validation.

Asserts:
- all 9 input kinds are recognised
- X86InputRecord validates required fields (kind/architecture/bitness)
- missing fields produce INPUT_CONTEXT_INCOMPLETE diagnostics
- JSON-serializable
- make_input_record factory returns (record, diagnostics)
"""

from __future__ import annotations

import json

import pytest

from agent.codecompass.x86.diagnostics import INPUT_CONTEXT_INCOMPLETE
from agent.codecompass.x86.input_taxonomy import (
    ALL_INPUT_KINDS,
    INPUT_KIND_CAPSTONE_FIXTURE,
    INPUT_KIND_DISASSEMBLER_JSON,
    INPUT_KIND_EXECUTABLE_BINARY,
    INPUT_KIND_FUNCTION_BYTES,
    INPUT_KIND_GHIDRA_EXPORT,
    INPUT_KIND_NORMALIZED_ASSEMBLY,
    INPUT_KIND_OBJECT_FILE,
    INPUT_KIND_RAW_ASSEMBLY_TEXT,
    INPUT_KIND_RIZIN_EXPORT,
    VALID_ABIS,
    VALID_ARCHITECTURES,
    VALID_BITNESS,
    VALID_ENDIANNESS,
    VALID_SYNTAXES,
    X86InputRecord,
    make_input_record,
)


def test_all_nine_input_kinds_recognised():
    expected = {
        "raw_assembly_text",
        "normalized_assembly",
        "object_file",
        "executable_binary",
        "function_bytes",
        "disassembler_json",
        "ghidra_export",
        "rizin_export",
        "capstone_fixture",
    }
    assert ALL_INPUT_KINDS == expected
    assert len(ALL_INPUT_KINDS) == 9


def test_input_kind_constants_present():
    for k in (
        INPUT_KIND_RAW_ASSEMBLY_TEXT,
        INPUT_KIND_NORMALIZED_ASSEMBLY,
        INPUT_KIND_OBJECT_FILE,
        INPUT_KIND_EXECUTABLE_BINARY,
        INPUT_KIND_FUNCTION_BYTES,
        INPUT_KIND_DISASSEMBLER_JSON,
        INPUT_KIND_GHIDRA_EXPORT,
        INPUT_KIND_RIZIN_EXPORT,
        INPUT_KIND_CAPSTONE_FIXTURE,
    ):
        assert k in ALL_INPUT_KINDS


def test_validate_complete_record_returns_no_issues():
    rec = X86InputRecord(
        kind=INPUT_KIND_DISASSEMBLER_JSON,
        architecture="x86_64",
        bitness=64,
        syntax="intel",
        abi="x86_64_sysv",
        endianness="little",
        source_path="/tmp/sample.dis.json",
    )
    issues = rec.validate()
    assert issues == []


def test_missing_kind_produces_incomplete_diagnostic():
    rec = X86InputRecord(kind="", architecture="x86_64", bitness=64)
    issues = rec.validate()
    assert any(i["code"] == INPUT_CONTEXT_INCOMPLETE for i in issues)


def test_missing_architecture_produces_incomplete_diagnostic():
    rec = X86InputRecord(kind=INPUT_KIND_RAW_ASSEMBLY_TEXT, architecture="", bitness=64)
    issues = rec.validate()
    assert any(i["code"] == INPUT_CONTEXT_INCOMPLETE for i in issues)


def test_invalid_bitness_produces_incomplete_diagnostic():
    rec = X86InputRecord(kind=INPUT_KIND_RAW_ASSEMBLY_TEXT, architecture="x86_64", bitness=128)
    issues = rec.validate()
    assert any(i["code"] == INPUT_CONTEXT_INCOMPLETE for i in issues)


def test_unknown_kind_warns_but_does_not_silently_drop():
    rec = X86InputRecord(kind="unknown_kind", architecture="x86_64", bitness=64)
    issues = rec.validate()
    codes = [i["code"] for i in issues]
    assert INPUT_CONTEXT_INCOMPLETE in codes


def test_as_dict_is_json_serializable():
    rec = X86InputRecord(
        kind=INPUT_KIND_GHIDRA_EXPORT,
        architecture="x86_64",
        bitness=64,
        source_path="/tmp/x.json",
    )
    payload = rec.as_dict()
    # round-trip
    blob = json.dumps(payload)
    parsed = json.loads(blob)
    assert parsed["kind"] == INPUT_KIND_GHIDRA_EXPORT
    assert parsed["bitness"] == 64


def test_make_input_record_factory_returns_tuple():
    rec, issues = make_input_record(
        kind=INPUT_KIND_RIZIN_EXPORT,
        architecture="x86_64",
        bitness=64,
        source_path="/tmp/x.json",
    )
    assert isinstance(rec, X86InputRecord)
    assert issues == []


def test_make_input_record_factory_propagates_issues():
    _, issues = make_input_record(kind="", architecture="", bitness=0)
    assert any(i["code"] == INPUT_CONTEXT_INCOMPLETE for i in issues)


@pytest.mark.parametrize(
    "arch",
    ["x86_64", "x86_32", "x86_16", "unknown_x86"],
)
def test_valid_architectures(arch):
    assert arch in VALID_ARCHITECTURES


@pytest.mark.parametrize("bitness", [16, 32, 64])
def test_valid_bitness(bitness):
    assert bitness in VALID_BITNESS


@pytest.mark.parametrize(
    "syntax", ["intel", "att", "unknown"]
)
def test_valid_syntaxes(syntax):
    assert syntax in VALID_SYNTAXES


@pytest.mark.parametrize(
    "abi", ["x86_64_sysv", "x86_64_windows", "x86_32_cdecl", "x86_32_stdcall", "unknown_x86"]
)
def test_valid_abis(abi):
    assert abi in VALID_ABIS


@pytest.mark.parametrize("endianness", ["little", "big", "unknown"])
def test_valid_endianness(endianness):
    assert endianness in VALID_ENDIANNESS