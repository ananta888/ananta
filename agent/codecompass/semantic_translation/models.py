from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_ID = "codecompass_semantic_translation_graph.v1"

NODE_KINDS = {
    "syntax_node",
    "semantic_node",
    "type_node",
    "symbol_node",
    "control_flow_node",
    "data_flow_node",
    "effect_node",
    "contract_node",
    "equivalence_rule",
    "transform_artifact",
}

EDGE_TYPES = {
    "declares",
    "uses",
    "calls",
    "reads",
    "writes",
    "returns",
    "throws",
    "maps_to",
    "equivalent_to",
    "requires",
    "ensures",
    "generated_by",
    "verified_by",
}

SEMANTIC_KINDS = {
    "data_record",
    "property",
    "enum_value",
    "function_signature",
    "nullable_value",
    "optional_absence",
    "collection",
    "map",
    "pure_expression",
    "side_effect",
    "exception_flow",
    "interface_contract",
    "unsupported_construct",
}

NULLABILITY_STATES = {
    "nullable",
    "non_null",
    "unknown_nullability",
    "optional_absence",
    "empty_collection",
}

EFFECT_KINDS = {
    "db_read",
    "db_write",
    "io_read",
    "io_write",
    "network_call",
    "time_access",
    "random_access",
    "unknown_side_effect",
    "pure",
}


CONTROL_FLOW_KINDS = {
    "if_else_branch",
    "return_statement",
    "iteration_over_finite_collection",
    "switch_enum_match",
    "unsupported_control_flow",
}

CONTROL_FLOW_PRECONDITIONS: dict[str, list[str]] = {
    "if_else_branch": ["condition_is_boolean_expression", "no_nullable_condition_without_null_check"],
    "return_statement": ["return_type_mapped", "no_unchecked_exception_in_path"],
    "iteration_over_finite_collection": ["collection_is_finite", "no_mutating_iterator", "no_break_continue"],
    "switch_enum_match": ["all_enum_values_known", "no_fallthrough"],
}

UNSUPPORTED_CONTROL_FLOW_CONSTRUCTS = {
    "break",
    "continue",
    "labeled_break",
    "synchronized_block",
    "try_with_resources_complex",
    "mutating_iterator",
    "goto",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Provenance:
    file: str
    language: str
    symbol: str = ""
    line_start: int | None = None
    line_end: int | None = None
    parser: str = "codecompass.semantic_translation"
    confidence: float = 1.0
    created_at: str = field(default_factory=utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "language": self.language,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "parser": self.parser,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SemanticNode:
    id: str
    kind: str
    semantic_kind: str
    language: str
    symbol: str
    attributes: dict[str, Any]
    provenance: Provenance

    def as_record(self) -> dict[str, Any]:
        validate_node_kind(self.kind)
        validate_semantic_kind(self.semantic_kind)
        return {
            "schema": SCHEMA_ID,
            "id": self.id,
            "kind": self.kind,
            "semantic_kind": self.semantic_kind,
            "language": self.language,
            "symbol": self.symbol,
            "attributes": dict(self.attributes),
            "provenance": self.provenance.as_dict(),
            "_provenance": {"output_kind": "semantic_nodes"},
        }


@dataclass(frozen=True)
class SemanticEdge:
    source_id: str
    target_id: str
    edge_type: str
    rule_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None

    def as_record(self) -> dict[str, Any]:
        validate_edge_type(self.edge_type)
        return {
            "schema": SCHEMA_ID,
            "source": self.source_id,
            "target": self.target_id,
            "edge_type": self.edge_type,
            "rule_id": self.rule_id,
            "attributes": dict(self.attributes),
            "provenance": self.provenance.as_dict() if self.provenance else {},
            "_provenance": {"output_kind": "semantic_edges"},
        }


def validate_node_kind(kind: str) -> None:
    if str(kind or "") not in NODE_KINDS:
        raise ValueError(f"unknown semantic translation node kind: {kind}")


def validate_edge_type(edge_type: str) -> None:
    if str(edge_type or "") not in EDGE_TYPES:
        raise ValueError(f"unknown semantic translation edge type: {edge_type}")


def validate_semantic_kind(semantic_kind: str) -> None:
    if str(semantic_kind or "") not in SEMANTIC_KINDS:
        raise ValueError(f"unknown semantic kind: {semantic_kind}")


def diagnostic(code: str, message: str, *, severity: str = "warning", path: str = "", line: int | None = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "path": path, "line": line}
