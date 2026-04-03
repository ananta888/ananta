from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from rag_helper.domain.java_records import RelationRecord


CSharpMethodRecord = TypedDict(
    "CSharpMethodRecord",
    {
        "kind": str,
        "file": str,
        "id": str,
        "parent_id": str,
        "class": str,
        "name": str,
        "signature": str,
        "return_type": str | None,
        "resolved_return_types": list[str],
        "parameters": list[str],
        "modifiers": list[str],
        "attributes": list[str],
        "documentation": str | None,
        "documentation_summary": str | None,
        "parameter_count": int,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "is_getter": bool,
        "is_setter": bool,
        "is_trivial": bool,
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
    },
)


CSharpMethodDetailRecord = TypedDict(
    "CSharpMethodDetailRecord",
    {
        "kind": str,
        "file": str,
        "id": str,
        "parent_id": str,
        "class": str,
        "name": str,
        "signature": str,
        "return_type": str | None,
        "resolved_return_types": list[str],
        "parameters": list[str],
        "modifiers": list[str],
        "attributes": list[str],
        "documentation": str | None,
        "documentation_summary": str | None,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "is_getter": bool,
        "is_setter": bool,
        "is_trivial": bool,
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
        "code_snippet": NotRequired[str],
    },
)


CSharpPropertyRecord = TypedDict(
    "CSharpPropertyRecord",
    {
        "kind": str,
        "file": str,
        "id": str,
        "parent_id": str,
        "class": str,
        "name": str,
        "property_type": str | None,
        "resolved_property_types": list[str],
        "modifiers": list[str],
        "attributes": list[str],
        "documentation": str | None,
        "documentation_summary": str | None,
        "accessors": list[str],
        "is_auto_property": bool,
        "is_trivial": bool,
        "embedding_text": str,
    },
)


CSharpConstructorRecord = TypedDict(
    "CSharpConstructorRecord",
    {
        "kind": str,
        "file": str,
        "id": str,
        "parent_id": str,
        "class": str,
        "name": str,
        "signature": str,
        "parameters": list[str],
        "modifiers": list[str],
        "attributes": list[str],
        "documentation": str | None,
        "documentation_summary": str | None,
        "parameter_count": int,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
    },
)


CSharpConstructorDetailRecord = TypedDict(
    "CSharpConstructorDetailRecord",
    {
        "kind": str,
        "file": str,
        "id": str,
        "parent_id": str,
        "class": str,
        "name": str,
        "signature": str,
        "parameters": list[str],
        "modifiers": list[str],
        "attributes": list[str],
        "documentation": str | None,
        "documentation_summary": str | None,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
        "code_snippet": NotRequired[str],
    },
)


class CSharpTypeRecord(TypedDict):
    kind: str
    file: str
    id: str
    parent_id: str
    namespace: str | None
    usings: list[str]
    name: str
    type_kind: str
    modifiers: list[str]
    attributes: list[str]
    documentation: str | None
    documentation_summary: str | None
    extends: str | None
    extends_resolved: list[str]
    implements: list[str]
    implements_resolved: list[str]
    fields: list[dict[str, Any]]
    properties: list[str]
    methods: list[str]
    constructors: list[str]
    used_types: list[str]
    called_methods: list[str]
    role_labels: list[str]
    roles: dict[str, Any]
    type_resolution_conflicts: list[dict[str, object]]
    embedding_text: str
    summary: str
