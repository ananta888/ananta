"""X86CC-006 to X86CC-010: Data models for x86 CodeCompass extension."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_ID = "codecompass_x86_graph.v1"

# ---------------------------------------------------------------------------
# X86CC-006: Node kinds
# ---------------------------------------------------------------------------
class X86NodeKind:
    BINARY_FILE = "binary_file"
    SECTION = "section"
    SEGMENT = "segment"
    SYMBOL = "symbol"
    FUNCTION = "function"
    BASIC_BLOCK = "basic_block"
    INSTRUCTION = "instruction"
    OPERAND = "operand"
    REGISTER = "register"
    FLAG = "flag"
    STACK_SLOT = "stack_slot"
    MEMORY_REGION = "memory_region"
    CALLSITE = "callsite"
    IMPORT_SYMBOL = "import_symbol"
    EXPORT_SYMBOL = "export_symbol"
    RELOCATION = "relocation"
    STRING_LITERAL = "string_literal"
    ADDRESS_RANGE = "address_range"

ALL_NODE_KINDS = {
    X86NodeKind.BINARY_FILE, X86NodeKind.SECTION, X86NodeKind.SEGMENT,
    X86NodeKind.SYMBOL, X86NodeKind.FUNCTION, X86NodeKind.BASIC_BLOCK,
    X86NodeKind.INSTRUCTION, X86NodeKind.OPERAND, X86NodeKind.REGISTER,
    X86NodeKind.FLAG, X86NodeKind.STACK_SLOT, X86NodeKind.MEMORY_REGION,
    X86NodeKind.CALLSITE, X86NodeKind.IMPORT_SYMBOL, X86NodeKind.EXPORT_SYMBOL,
    X86NodeKind.RELOCATION, X86NodeKind.STRING_LITERAL, X86NodeKind.ADDRESS_RANGE,
}

# ---------------------------------------------------------------------------
# X86CC-007: Edge types
# ---------------------------------------------------------------------------
class X86EdgeType:
    CONTAINS = "contains"
    BELONGS_TO = "belongs_to"
    NEXT_INSTRUCTION = "next_instruction"
    CFG_FALLTHROUGH = "cfg_fallthrough"
    CFG_TRUE = "cfg_true"
    CFG_FALSE = "cfg_false"
    CFG_JUMP = "cfg_jump"
    CFG_INDIRECT_JUMP = "cfg_indirect_jump"
    CALLS = "calls"
    INDIRECT_CALLS = "indirect_calls"
    RETURNS_TO = "returns_to"
    READS_REGISTER = "reads_register"
    WRITES_REGISTER = "writes_register"
    READS_FLAG = "reads_flag"
    WRITES_FLAG = "writes_flag"
    READS_MEMORY = "reads_memory"
    WRITES_MEMORY = "writes_memory"
    USES_STACK_SLOT = "uses_stack_slot"
    REFERENCES_STRING = "references_string"
    REFERENCES_IMPORT = "references_import"
    REFERENCES_ADDRESS = "references_address"
    RELOCATES_TO = "relocates_to"

ALL_EDGE_TYPES = {
    X86EdgeType.CONTAINS, X86EdgeType.BELONGS_TO, X86EdgeType.NEXT_INSTRUCTION,
    X86EdgeType.CFG_FALLTHROUGH, X86EdgeType.CFG_TRUE, X86EdgeType.CFG_FALSE,
    X86EdgeType.CFG_JUMP, X86EdgeType.CFG_INDIRECT_JUMP, X86EdgeType.CALLS,
    X86EdgeType.INDIRECT_CALLS, X86EdgeType.RETURNS_TO, X86EdgeType.READS_REGISTER,
    X86EdgeType.WRITES_REGISTER, X86EdgeType.READS_FLAG, X86EdgeType.WRITES_FLAG,
    X86EdgeType.READS_MEMORY, X86EdgeType.WRITES_MEMORY, X86EdgeType.USES_STACK_SLOT,
    X86EdgeType.REFERENCES_STRING, X86EdgeType.REFERENCES_IMPORT,
    X86EdgeType.REFERENCES_ADDRESS, X86EdgeType.RELOCATES_TO,
}

# ---------------------------------------------------------------------------
# X86CC-008: AddressRef
# ---------------------------------------------------------------------------
class AddressKind:
    ABSOLUTE = "absolute_address"
    RELATIVE = "relative_address"
    FILE_OFFSET = "file_offset"
    RVA = "rva"
    UNKNOWN = "unknown"

ALL_ADDRESS_KINDS = {
    AddressKind.ABSOLUTE, AddressKind.RELATIVE, AddressKind.FILE_OFFSET,
    AddressKind.RVA, AddressKind.UNKNOWN,
}


@dataclass
class AddressRef:
    kind: str  # absolute_address / relative_address / file_offset / rva / unknown
    value: int | None
    label: str = ""  # human-readable like "main+0x14"
    section: str = ""
    base_address: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "label": self.label or (f"0x{self.value:x}" if self.value is not None else "unknown"),
            "section": self.section,
            "base_address": self.base_address,
        }


# ---------------------------------------------------------------------------
# X86CC-008: LocationRef
# ---------------------------------------------------------------------------
@dataclass
class LocationRef:
    address_ref: AddressRef
    symbol: str = ""
    function_id: str = ""
    basic_block_id: str = ""
    section: str = ""
    instruction_index: int = -1

    def as_evidence_excerpt(self) -> str:
        parts = []
        if self.symbol:
            parts.append(self.symbol)
        if self.address_ref.label:
            parts.append(self.address_ref.label)
        elif self.address_ref.value is not None:
            parts.append(f"0x{self.address_ref.value:x}")
        if self.section:
            parts.append(f"section={self.section}")
        return " ".join(parts) or "unknown_location"


# ---------------------------------------------------------------------------
# X86CC-010: Provenance
# ---------------------------------------------------------------------------
@dataclass
class X86Provenance:
    source_file: str = ""
    adapter: str = ""
    adapter_version: str = ""
    disassembler: str = ""
    profile: str = ""
    heuristic: bool = False
    symbol_table: bool = False
    debug_info: bool = False
    manual_fixture: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "disassembler": self.disassembler,
            "profile": self.profile,
            "heuristic": self.heuristic,
            "symbol_table": self.symbol_table,
            "debug_info": self.debug_info,
            "manual_fixture": self.manual_fixture,
        }


# ---------------------------------------------------------------------------
# X86CC-006: X86Node
# ---------------------------------------------------------------------------
@dataclass
class X86Node:
    id: str
    kind: str
    source_type: str
    path: str = ""
    address: int | None = None
    offset: int | None = None
    architecture_profile: str = "unknown_x86"
    confidence: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "id": self.id,
            "kind": self.kind,
            "source_type": self.source_type,
            "path": self.path,
            "address": self.address,
            "offset": self.offset,
            "architecture_profile": self.architecture_profile,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "attributes": dict(self.attributes),
            "_provenance": {"output_kind": "x86_nodes"},
        }


# ---------------------------------------------------------------------------
# X86CC-007: X86Edge
# ---------------------------------------------------------------------------
@dataclass
class X86Edge:
    source_id: str
    target_id: str
    edge_type: str
    confidence: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)
    address: int | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "source": self.source_id,
            "target": self.target_id,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
            "provenance": dict(self.provenance),
            "address": self.address,
            "context": dict(self.context),
            "_provenance": {"output_kind": "x86_edges"},
        }


# ---------------------------------------------------------------------------
# X86CC-009: Evidence kinds
# ---------------------------------------------------------------------------
class X86EvidenceKind:
    INSTRUCTION = "x86_instruction"
    BASIC_BLOCK = "x86_basic_block"
    FUNCTION = "x86_function"
    CFG_EDGE = "x86_cfg_edge"
    CALLSITE = "x86_callsite"
    SECTION = "x86_section"
    IMPORT = "x86_import"
    STRING = "x86_string"
    DIAGNOSTIC = "x86_diagnostic"

ALL_EVIDENCE_KINDS = {
    X86EvidenceKind.INSTRUCTION, X86EvidenceKind.BASIC_BLOCK, X86EvidenceKind.FUNCTION,
    X86EvidenceKind.CFG_EDGE, X86EvidenceKind.CALLSITE, X86EvidenceKind.SECTION,
    X86EvidenceKind.IMPORT, X86EvidenceKind.STRING, X86EvidenceKind.DIAGNOSTIC,
}
