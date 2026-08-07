from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.codecompass.semantic_translation.models import diagnostic
from agent.codecompass.semantic_translation.nullability import infer_java_nullability
from agent.repository_map_tree_sitter import resolve_tree_sitter_parser

_TYPE_KIND_BY_NODE = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}


@dataclass(frozen=True)
class JavaParseResult:
    available: bool
    types: tuple[dict, ...]
    imports: tuple[dict, ...]
    diagnostics: tuple[dict, ...]
    parser_strategy: str
    confidence: float


class JavaTreeSitterExtractor:
    """Focused Java CST reader; it never compiles or executes repository code."""

    def parse(self, path: str, content: str) -> JavaParseResult:
        resolution = resolve_tree_sitter_parser("java")
        if resolution.parser is None:
            return JavaParseResult(
                available=False,
                types=(),
                imports=(),
                diagnostics=tuple(
                    diagnostic(code, "Java tree-sitter runtime is unavailable.", path=path)
                    for code in resolution.status.diagnostics
                ),
                parser_strategy="regex-java-v1",
                confidence=0.58,
            )
        try:
            source = content.encode("utf-8")
            tree = resolution.parser.parse(source)
            root = tree.root_node
            if root is None:
                raise RuntimeError("java_tree_sitter_root_missing")
            if bool(getattr(root, "has_error", False)):
                return JavaParseResult(
                    available=False,
                    types=(),
                    imports=(),
                    diagnostics=(
                        diagnostic(
                            "java_tree_sitter_syntax_error",
                            "Java CST contains syntax errors; regex fallback is used.",
                            path=path,
                        ),
                    ),
                    parser_strategy="regex-java-v1",
                    confidence=0.58,
                )
            imports = tuple(self._imports(root, source))
            types: list[dict] = []
            self._collect_types(root, source, types, parent_name=None)
            return JavaParseResult(
                available=True,
                types=tuple(types),
                imports=imports,
                diagnostics=tuple(
                    diagnostic(code, "Java parser runtime fallback note.", path=path)
                    for code in resolution.status.diagnostics
                ),
                parser_strategy=f"tree-sitter-java-v1:{resolution.status.strategy}",
                confidence=0.9,
            )
        except Exception as exc:
            return JavaParseResult(
                available=False,
                types=(),
                imports=(),
                diagnostics=(diagnostic("java_tree_sitter_parse_failed", str(exc), path=path),),
                parser_strategy="regex-java-v1",
                confidence=0.58,
            )

    def _imports(self, root: Any, source: bytes) -> list[dict]:
        results = []
        for node in root.named_children:
            if node.type != "import_declaration":
                continue
            value = _text(node, source).removeprefix("import").removesuffix(";").strip()
            static = value.startswith("static ")
            if static:
                value = value.removeprefix("static ").strip()
            results.append(
                {
                    "name": value,
                    "kind": "static_import" if static else "import",
                    "line_start": node.start_point.row + 1,
                    "column_start": node.start_point.column + 1,
                }
            )
        return results

    def _collect_types(
        self,
        node: Any,
        source: bytes,
        output: list[dict],
        *,
        parent_name: str | None,
    ) -> None:
        for child in node.named_children:
            if child.type not in _TYPE_KIND_BY_NODE:
                self._collect_types(child, source, output, parent_name=parent_name)
                continue
            item = self._type(child, source, parent_name=parent_name)
            output.append(item)
            body = child.child_by_field_name("body")
            if body is not None:
                self._collect_types(body, source, output, parent_name=item["qualified_name"])

    def _type(self, node: Any, source: bytes, *, parent_name: str | None) -> dict:
        name_node = node.child_by_field_name("name")
        name = _text(name_node, source)
        qualified_name = f"{parent_name}.{name}" if parent_name else name
        body = node.child_by_field_name("body")
        methods: list[dict] = []
        properties: list[dict] = []
        enum_values: list[str] = []
        if node.type == "record_declaration":
            properties.extend(self._parameters(node.child_by_field_name("parameters"), source, property_mode=True))
        if body is not None:
            for member in body.named_children:
                if member.type == "field_declaration":
                    properties.extend(self._fields(member, source))
                elif member.type in {"method_declaration", "constructor_declaration"}:
                    methods.append(self._method(member, source))
                elif member.type == "enum_constant":
                    constant_name = member.child_by_field_name("name")
                    enum_values.append(_text(constant_name or member.named_children[0], source))
        unsupported: list[dict] = []
        declaration_text = _text(node, source)
        if re.search(r"\b(break|continue|goto|synchronized)\b", declaration_text):
            unsupported.append(
                {
                    "code": "unsupported_control_flow",
                    "reason": "break_continue_synchronized_or_goto_detected",
                }
            )
        if re.search(r"\bnative\b", declaration_text):
            unsupported.append({"code": "unsupported_construct", "reason": "native_member"})
        return {
            "name": name,
            "qualified_name": qualified_name,
            "parent_type": parent_name,
            "nested": parent_name is not None,
            "kind": _TYPE_KIND_BY_NODE[node.type],
            "line_start": node.start_point.row + 1,
            "column_start": node.start_point.column + 1,
            "line_end": node.end_point.row + 1,
            "type_parameters": _text(node.child_by_field_name("type_parameters"), source),
            "extends": self._heritage(node.child_by_field_name("superclass"), source, "extends"),
            "implements": self._heritage(node.child_by_field_name("interfaces"), source, "implements"),
            "properties": properties,
            "methods": methods,
            "enum_values": enum_values,
            "annotations": self._annotations(node, source),
            "unsupported": unsupported,
        }

    def _fields(self, node: Any, source: bytes) -> list[dict]:
        type_name = _text(node.child_by_field_name("type"), source)
        annotations = self._annotations(node, source)
        nullability = infer_java_nullability(type_name, annotations)
        results = []
        for child in node.named_children:
            if child.type != "variable_declarator":
                continue
            name = _text(child.child_by_field_name("name"), source)
            results.append(
                {
                    "name": name,
                    "type": type_name,
                    "order": len(results),
                    "annotations": annotations,
                    "nullability": nullability.state,
                    "warnings": list(nullability.warnings),
                    "line_start": child.start_point.row + 1,
                    "column_start": child.start_point.column + 1,
                }
            )
        return results

    def _method(self, node: Any, source: bytes) -> dict:
        constructor = node.type == "constructor_declaration"
        name = _text(node.child_by_field_name("name"), source)
        return_type = name if constructor else _text(node.child_by_field_name("type"), source)
        modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
        modifier_text = _text(modifiers, source)
        throws_node = next((child for child in node.named_children if child.type == "throws"), None)
        throws = [
            _text(child, source)
            for child in (throws_node.named_children if throws_node is not None else [])
            if child.type not in {"throws"}
        ]
        return {
            "name": name,
            "kind": "constructor" if constructor else "method",
            "return_type": return_type,
            "parameters": self._parameters(node.child_by_field_name("parameters"), source),
            "throws": throws,
            "throws_classified": [_classify_throw(value) for value in throws],
            "visibility": next(
                (value for value in ("public", "protected", "private") if value in modifier_text.split()),
                "package",
            ),
            "static": "static" in modifier_text.split(),
            "final": "final" in modifier_text.split(),
            "annotations": self._annotations(node, source),
            "type_parameters": _text(node.child_by_field_name("type_parameters"), source),
            "side_effects": ["unknown_side_effect"],
            "contracts": {"preconditions": [], "postconditions": [], "invariants": []},
            "line_start": node.start_point.row + 1,
            "column_start": node.start_point.column + 1,
        }

    def _parameters(self, node: Any | None, source: bytes, *, property_mode: bool = False) -> list[dict]:
        if node is None:
            return []
        results = []
        for child in node.named_children:
            if child.type not in {"formal_parameter", "spread_parameter", "receiver_parameter"}:
                continue
            name = _text(child.child_by_field_name("name"), source)
            type_name = _text(child.child_by_field_name("type"), source)
            annotations = self._annotations(child, source)
            item = {
                "name": name,
                "type": type_name,
                "order": len(results),
                "annotations": annotations,
                "line_start": child.start_point.row + 1,
                "column_start": child.start_point.column + 1,
            }
            if property_mode:
                nullability = infer_java_nullability(type_name, annotations)
                item.update({"nullability": nullability.state, "warnings": list(nullability.warnings)})
            results.append(item)
        return results

    def _annotations(self, node: Any, source: bytes) -> list[str]:
        modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
        if modifiers is None:
            return []
        return [
            _text(child, source)
            for child in modifiers.named_children
            if "annotation" in child.type
        ]

    def _heritage(self, node: Any | None, source: bytes, prefix: str) -> list[str]:
        if node is None:
            return []
        raw = _text(node, source).strip()
        raw = raw.removeprefix(prefix).strip()
        return [item.strip() for item in raw.split(",") if item.strip()]


def _text(node: Any | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _classify_throw(exception_name: str) -> dict:
    simple = exception_name.rsplit(".", 1)[-1]
    unchecked = simple.endswith("RuntimeException") or simple.endswith("Error") or simple in {
        "NullPointerException",
        "IllegalArgumentException",
        "IllegalStateException",
        "IndexOutOfBoundsException",
    }
    return {
        "name": exception_name,
        "kind": "unchecked_exception" if unchecked else "checked_exception",
    }
