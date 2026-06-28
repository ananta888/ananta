"""X86CC-004: Input taxonomy and X86InputRecord for the x86 extension."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.codecompass.x86.diagnostics import INPUT_CONTEXT_INCOMPLETE, x86_diag_dict

# All recognised input kinds
INPUT_KIND_RAW_ASSEMBLY_TEXT = "raw_assembly_text"
INPUT_KIND_NORMALIZED_ASSEMBLY = "normalized_assembly"
INPUT_KIND_OBJECT_FILE = "object_file"
INPUT_KIND_EXECUTABLE_BINARY = "executable_binary"
INPUT_KIND_FUNCTION_BYTES = "function_bytes"
INPUT_KIND_DISASSEMBLER_JSON = "disassembler_json"
INPUT_KIND_GHIDRA_EXPORT = "ghidra_export"
INPUT_KIND_RIZIN_EXPORT = "rizin_export"
INPUT_KIND_CAPSTONE_FIXTURE = "capstone_fixture"

ALL_INPUT_KINDS = {
    INPUT_KIND_RAW_ASSEMBLY_TEXT,
    INPUT_KIND_NORMALIZED_ASSEMBLY,
    INPUT_KIND_OBJECT_FILE,
    INPUT_KIND_EXECUTABLE_BINARY,
    INPUT_KIND_FUNCTION_BYTES,
    INPUT_KIND_DISASSEMBLER_JSON,
    INPUT_KIND_GHIDRA_EXPORT,
    INPUT_KIND_RIZIN_EXPORT,
    INPUT_KIND_CAPSTONE_FIXTURE,
}

VALID_ARCHITECTURES = {"x86_64", "x86_32", "x86_16", "unknown_x86"}
VALID_BITNESS = {16, 32, 64}
VALID_SYNTAXES = {"intel", "att", "unknown"}
VALID_ABIS = {"x86_64_sysv", "x86_64_windows", "x86_32_cdecl", "x86_32_stdcall", "unknown_x86"}
VALID_ENDIANNESS = {"little", "big", "unknown"}

_REQUIRED_FIELDS = ("kind", "architecture", "bitness")


@dataclass
class X86InputRecord:
    kind: str
    architecture: str
    bitness: int
    syntax: str = "intel"
    abi: str = "unknown_x86"
    endianness: str = "little"
    base_address: int = 0
    section_context: str = ""
    source_path: str = ""
    content: str | None = None
    raw_bytes: bytes | None = None
    fixture_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> list[dict[str, Any]]:
        """Validate required fields; return list of diagnostics for any issues."""
        issues: list[dict[str, Any]] = []
        if not self.kind:
            issues.append(x86_diag_dict(INPUT_CONTEXT_INCOMPLETE, "kind is required"))
        elif self.kind not in ALL_INPUT_KINDS:
            issues.append(x86_diag_dict(INPUT_CONTEXT_INCOMPLETE, f"unknown input kind: {self.kind}", severity="warning"))
        if not self.architecture:
            issues.append(x86_diag_dict(INPUT_CONTEXT_INCOMPLETE, "architecture is required"))
        if self.bitness not in VALID_BITNESS:
            issues.append(x86_diag_dict(INPUT_CONTEXT_INCOMPLETE, f"invalid bitness: {self.bitness}", severity="warning"))
        return issues

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable representation."""
        return {
            "kind": self.kind,
            "architecture": self.architecture,
            "bitness": self.bitness,
            "syntax": self.syntax,
            "abi": self.abi,
            "endianness": self.endianness,
            "base_address": self.base_address,
            "section_context": self.section_context,
            "source_path": self.source_path,
            "fixture_path": self.fixture_path,
            "extra": dict(self.extra),
            "diagnostics": list(self.diagnostics),
        }


def make_input_record(
    kind: str,
    architecture: str = "x86_64",
    bitness: int = 64,
    **kwargs: Any,
) -> tuple[X86InputRecord, list[dict[str, Any]]]:
    """Create an X86InputRecord and return it with any validation diagnostics."""
    record = X86InputRecord(kind=kind, architecture=architecture, bitness=bitness, **kwargs)
    issues = record.validate()
    return record, issues
