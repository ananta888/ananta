from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class RelationRecord(TypedDict):
    kind: str
    file: str
    id: str
    source_id: str
    source_kind: str
    source_name: str
    relation: str
    target: str
    target_resolved: str | None
    weight: int
    confidence: NotRequired[str]
    heuristic: NotRequired[str]


JavaMethodRecord = TypedDict(
    "JavaMethodRecord",
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
        "annotations": list[str],
        "javadoc": str | None,
        "javadoc_summary": str | None,
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


JavaMethodDetailRecord = TypedDict(
    "JavaMethodDetailRecord",
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
        "annotations": list[str],
        "javadoc": str | None,
        "javadoc_summary": str | None,
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


JavaConstructorRecord = TypedDict(
    "JavaConstructorRecord",
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
        "annotations": list[str],
        "javadoc": str | None,
        "javadoc_summary": str | None,
        "parameter_count": int,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
    },
)


JavaConstructorDetailRecord = TypedDict(
    "JavaConstructorDetailRecord",
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
        "annotations": list[str],
        "javadoc": str | None,
        "javadoc_summary": str | None,
        "calls": list[str],
        "type_refs": list[str],
        "resolved_type_refs": list[str],
        "type_resolution_conflicts": list[dict[str, object]],
        "resolved_call_targets": list[dict[str, str]],
        "embedding_text": str,
        "code_snippet": NotRequired[str],
    },
)


class JavaTypeRecord(TypedDict):
    kind: str
    file: str
    id: str
    parent_id: str
    package: str | None
    imports: list[str]
    name: str
    type_kind: str
    modifiers: list[str]
    annotations: list[str]
    javadoc: str | None
    javadoc_summary: str | None
    extends: str | None
    extends_resolved: list[str]
    implements: list[str]
    implements_resolved: list[str]
    fields: list[dict[str, Any]]
    methods: list[str]
    constructors: list[str]
    used_types: list[str]
    called_methods: list[str]
    role_labels: list[str]
    roles: dict[str, Any]
    type_resolution_conflicts: list[dict[str, object]]
    embedding_text: str
    summary: str
