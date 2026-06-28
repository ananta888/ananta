from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

TYPE_CONFIDENCE_LEVELS = {"annotated", "inferred_local", "inferred_from_default", "unknown", "dynamic"}

PYTHON_NONE_STATES = {"none_literal", "optional_type", "default_none", "missing_value", "falsy_empty"}

PYTHON_COLLECTION_KINDS = {"list", "set", "dict", "tuple", "frozenset"}

_FALSY_LITERALS = {"0", "0.0", '""', "''", "[]", "{}", "()", "set()", "None", "False"}


@dataclass(frozen=True)
class TypeAnnotation:
    raw: str
    confidence: str
    none_model: str | None = None
    collection_kind: str | None = None
    element_type: str | None = None
    key_type: str | None = None
    value_type: str | None = None
    is_optional: bool = False
    source: str = ""
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "confidence": self.confidence,
            "none_model": self.none_model,
            "collection_kind": self.collection_kind,
            "element_type": self.element_type,
            "key_type": self.key_type,
            "value_type": self.value_type,
            "is_optional": self.is_optional,
            "source": self.source,
            "warnings": list(self.warnings),
        }


def parse_python_type(annotation_node: ast.expr | None, *, source: str = "annotation") -> TypeAnnotation:
    if annotation_node is None:
        return TypeAnnotation(raw="", confidence="unknown", source=source, warnings=("no_type_annotation",))
    raw = ast.unparse(annotation_node)
    return _classify_type(raw, source=source)


def infer_type_from_default(default_node: ast.expr | None) -> TypeAnnotation:
    if default_node is None:
        return TypeAnnotation(raw="", confidence="unknown", source="default", warnings=("no_default",))
    raw = ast.unparse(default_node)
    if isinstance(default_node, ast.Constant):
        if default_node.value is None:
            return TypeAnnotation(raw="None", confidence="inferred_from_default", none_model="default_none", is_optional=True, source="default")
        if isinstance(default_node.value, bool):
            return TypeAnnotation(raw="bool", confidence="inferred_from_default", source="default")
        if isinstance(default_node.value, int):
            return TypeAnnotation(raw="int", confidence="inferred_from_default", source="default")
        if isinstance(default_node.value, float):
            return TypeAnnotation(raw="float", confidence="inferred_from_default", source="default")
        if isinstance(default_node.value, str):
            return TypeAnnotation(raw="str", confidence="inferred_from_default", source="default")
        if isinstance(default_node.value, bytes):
            return TypeAnnotation(raw="bytes", confidence="inferred_from_default", source="default")
    if isinstance(default_node, ast.List):
        return TypeAnnotation(raw="list", confidence="inferred_from_default", collection_kind="list", source="default")
    if isinstance(default_node, ast.Dict):
        return TypeAnnotation(raw="dict", confidence="inferred_from_default", collection_kind="dict", source="default")
    if isinstance(default_node, ast.Set):
        return TypeAnnotation(raw="set", confidence="inferred_from_default", collection_kind="set", source="default")
    if isinstance(default_node, ast.Tuple):
        return TypeAnnotation(raw="tuple", confidence="inferred_from_default", collection_kind="tuple", source="default")
    return TypeAnnotation(raw=raw, confidence="dynamic", source="default", warnings=("dynamic_default_value",))


def _classify_type(raw: str, *, source: str = "annotation") -> TypeAnnotation:
    stripped = raw.strip()
    if stripped in ("None", "type[None]"):
        return TypeAnnotation(raw=stripped, confidence="annotated", none_model="none_literal", is_optional=True, source=source)
    if stripped.startswith("Optional[") and stripped.endswith("]"):
        inner = stripped[9:-1].strip()
        return TypeAnnotation(raw=stripped, confidence="annotated", none_model="optional_type", is_optional=True, element_type=inner, source=source)
    if " | " in stripped:
        parts = [p.strip() for p in stripped.split(" | ")]
        non_none = [p for p in parts if p != "None"]
        if "None" in parts and len(non_none) == 1:
            return TypeAnnotation(raw=stripped, confidence="annotated", none_model="optional_type", is_optional=True, element_type=non_none[0], source=source)
        return TypeAnnotation(raw=stripped, confidence="annotated", source=source, warnings=("union_type_requires_review",))
    if stripped.startswith("Union[") and stripped.endswith("]"):
        inner = stripped[6:-1]
        parts = [p.strip() for p in inner.split(",")]
        non_none = [p for p in parts if p != "None"]
        if "None" in parts and len(non_none) == 1:
            return TypeAnnotation(raw=stripped, confidence="annotated", none_model="optional_type", is_optional=True, element_type=non_none[0], source=source)
        return TypeAnnotation(raw=stripped, confidence="annotated", source=source, warnings=("union_type_requires_review",))
    for collection_prefix, kind in [("list[", "list"), ("List[", "list"), ("set[", "set"), ("Set[", "set"), ("frozenset[", "frozenset"), ("FrozenSet[", "frozenset")]:
        if stripped.startswith(collection_prefix) and stripped.endswith("]"):
            inner = stripped[len(collection_prefix):-1].strip()
            return TypeAnnotation(raw=stripped, confidence="annotated", collection_kind=kind, element_type=inner, source=source)
    for prefix in ("dict[", "Dict["):
        if stripped.startswith(prefix) and stripped.endswith("]"):
            inner = stripped[len(prefix):-1]
            parts = _split_generic_args(inner)
            if len(parts) == 2:
                return TypeAnnotation(raw=stripped, confidence="annotated", collection_kind="dict", key_type=parts[0].strip(), value_type=parts[1].strip(), source=source)
    for prefix in ("tuple[", "Tuple["):
        if stripped.startswith(prefix) and stripped.endswith("]"):
            inner = stripped[len(prefix):-1]
            return TypeAnnotation(raw=stripped, confidence="annotated", collection_kind="tuple", element_type=inner, source=source)
    if stripped in ("Any", "typing.Any"):
        return TypeAnnotation(raw=stripped, confidence="dynamic", source=source, warnings=("any_type_requires_review",))
    if not stripped or stripped.startswith("Callable") or stripped.startswith("Protocol"):
        return TypeAnnotation(raw=stripped, confidence="unknown", source=source, warnings=("complex_type_requires_review",))
    return TypeAnnotation(raw=stripped, confidence="annotated", source=source)


def _split_generic_args(value: str) -> list[str]:
    depth = 0
    current: list[str] = []
    parts: list[str] = []
    for ch in value:
        if ch in ("<", "["):
            depth += 1
        elif ch in (">", "]"):
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts
