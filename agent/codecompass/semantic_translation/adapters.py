from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent.codecompass.semantic_translation.models import Provenance, SemanticEdge, SemanticNode, diagnostic
from agent.codecompass.semantic_translation.nullability import infer_java_nullability
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    DeterministicSemanticSymbolIdentityFactory,
    LegacySemanticSymbolIdentityPort,
    SemanticSymbolIdentityPort,
    semantic_identity_attributes,
    semantic_occurrence_symbol_id,
)


class SemanticLanguageAdapter(Protocol):
    language: str
    supported_extensions: tuple[str, ...]
    parser_strategy: str
    known_limits: tuple[str, ...]

    def detect(self, path: str, content: str) -> bool: ...

    def parse(self, path: str, content: str) -> dict: ...

    def extract_symbols(self, parsed: dict) -> list[dict]: ...

    def extract_types(self, parsed: dict) -> list[dict]: ...

    def extract_semantics(self, parsed: dict) -> list[dict]: ...

    def emit_graph_records(self, path: str, content: str) -> dict: ...


@dataclass(frozen=True)
class DummySemanticAdapter:
    language: str = "dummy"
    supported_extensions: tuple[str, ...] = (".dummy",)
    parser_strategy: str = "deterministic-dummy"
    semantic_kinds: tuple[str, ...] = ()
    known_limits: tuple[str, ...] = ("test-only adapter",)

    def detect(self, path: str, content: str) -> bool:
        return path.endswith(".dummy") or "dummy" in content

    def parse(self, path: str, content: str) -> dict:
        return {"path": path, "content": content, "types": []}

    def extract_symbols(self, parsed: dict) -> list[dict]:
        return []

    def extract_types(self, parsed: dict) -> list[dict]:
        return []

    def extract_semantics(self, parsed: dict) -> list[dict]:
        return []

    def emit_graph_records(self, path: str, content: str) -> dict:
        return {"nodes": [], "edges": [], "diagnostics": []}


class JavaSemanticAdapter:
    language = "java"
    supported_extensions = (".java",)
    parser_strategy = "tree-sitter-java-v1"
    fallback_strategy = "regex-java-v1"
    semantic_kinds = ("data_record", "interface_contract", "enum_value", "function_signature")
    known_limits = (
        "method bodies are not fully parsed",
        "cross-file generic type resolution is not performed",
        "framework-specific nullability semantics are not inferred",
        "regex-java-v1 remains a reduced-confidence runtime fallback",
    )

    def __init__(
        self,
        *,
        symbol_identity: (
            SemanticSymbolIdentityPort
            | LegacySemanticSymbolIdentityPort
            | None
        ) = None,
    ) -> None:
        self._symbol_identity = (
            symbol_identity or DeterministicSemanticSymbolIdentityFactory()
        )

    def detect(self, path: str, content: str) -> bool:
        del content
        return Path(path).suffix.lower() == ".java"

    def parse(self, path: str, content: str) -> dict:
        from agent.codecompass.semantic_translation.java_tree_sitter import JavaTreeSitterExtractor

        parser_result = JavaTreeSitterExtractor().parse(path, content)
        if parser_result.available:
            return {
                "path": path,
                "content": content,
                "types": list(parser_result.types),
                "imports": list(parser_result.imports),
                "diagnostics": list(parser_result.diagnostics),
                "parser_strategy": parser_result.parser_strategy,
                "confidence": parser_result.confidence,
                "fallback_reason": None,
            }
        try:
            return {
                "path": path,
                "content": content,
                "types": self._parse_types(path, content),
                "imports": self._parse_imports(content),
                "diagnostics": [
                    *parser_result.diagnostics,
                    diagnostic(
                        "java_regex_fallback",
                        "Java tree-sitter parsing was unavailable; reduced-confidence regex extraction was used.",
                        path=path,
                    ),
                ],
                "parser_strategy": self.fallback_strategy,
                "confidence": 0.58,
                "fallback_reason": "tree_sitter_unavailable_or_invalid",
            }
        except Exception as exc:
            return {
                "path": path,
                "content": content,
                "types": [],
                "imports": [],
                "diagnostics": [
                    *parser_result.diagnostics,
                    diagnostic("java_parse_error", str(exc), path=path),
                ],
                "parser_strategy": self.fallback_strategy,
                "confidence": 0.0,
                "fallback_reason": "all_java_parsers_failed",
            }

    def extract_symbols(self, parsed: dict) -> list[dict]:
        symbols = []
        for item in parsed.get("types") or []:
            symbols.append(
                {
                    "symbol": item["name"],
                    "kind": item["kind"],
                    "line_start": item["line_start"],
                }
            )
            for prop in item.get("properties") or []:
                symbols.append(
                    {
                        "symbol": f"{item['name']}.{prop['name']}",
                        "kind": "property",
                        "line_start": prop["line_start"],
                    }
                )
            for method in item.get("methods") or []:
                symbols.append(
                    {
                        "symbol": f"{item['name']}.{method['name']}",
                        "kind": "method",
                        "line_start": method["line_start"],
                    }
                )
        return symbols

    def extract_types(self, parsed: dict) -> list[dict]:
        return list(parsed.get("types") or [])

    def extract_semantics(self, parsed: dict) -> list[dict]:
        semantics = []
        for item in parsed.get("types") or []:
            semantic_kind = (
                "data_record"
                if item["kind"] in {"record", "class"}
                else "interface_contract"
                if item["kind"] == "interface"
                else "enum_value"
            )
            semantics.append({"symbol": item["name"], "semantic_kind": semantic_kind})
        return semantics

    def emit_graph_records(self, path: str, content: str) -> dict:
        parsed = self.parse(path, content)
        nodes: list[dict] = []
        edges: list[dict] = []
        parser_strategy = str(parsed.get("parser_strategy") or self.fallback_strategy)
        confidence = float(parsed.get("confidence") or 0.58)
        normalized_path = path.replace("\\", "/")
        file_id = f"semantic:java:file:{normalized_path}"
        module_node_ids: set[str] = set()
        for imported in parsed.get("imports") or []:
            module_name = str(imported.get("name") or "").strip()
            if not module_name:
                continue
            canonical_module_id = f"semantic:java:module:{module_name}"
            module_id = self._local_id(
                path=path,
                symbol_kind="module",
                canonical_id=canonical_module_id,
                local_qualifier=module_name,
                provenance_line_start=int(
                    imported.get("line_start") or 1
                ),
                provenance_column_start=int(
                    imported.get("column_start") or 1
                ),
            )
            if module_id not in module_node_ids:
                nodes.append(
                    SemanticNode(
                        id=module_id,
                        kind="semantic_node",
                        semantic_kind="module",
                        language="java",
                        symbol=module_name,
                        attributes={
                            **imported,
                            **semantic_identity_attributes(
                                canonical_module_id
                            ),
                        },
                        provenance=Provenance(
                            file=path,
                            language="java",
                            symbol=module_name,
                            line_start=imported.get("line_start"),
                            line_end=imported.get("line_start"),
                            parser=parser_strategy,
                            confidence=confidence,
                        ),
                    ).as_record()
                )
                module_node_ids.add(module_id)
            edges.append(
                SemanticEdge(
                    source_id=file_id,
                    target_id=module_id,
                    edge_type="imports",
                    attributes={"static": imported.get("kind") == "static_import"},
                ).as_record()
            )

        type_entries: list[tuple[dict, str, str, str]] = []
        local_type_candidates: dict[str, set[str]] = {}
        for item in parsed.get("types") or []:
            type_name = str(item.get("qualified_name") or item["name"])
            canonical_type_id = (
                f"semantic:java:{item['kind']}:{type_name}"
            )
            type_id = self._local_id(
                path=path,
                symbol_kind="type",
                canonical_id=canonical_type_id,
                local_qualifier=f"{item['kind']}:{type_name}",
                provenance_line_start=int(item["line_start"]),
                provenance_column_start=int(
                    item.get("column_start") or 1
                ),
            )
            type_entries.append(
                (item, type_name, canonical_type_id, type_id)
            )
            for alias in {str(item["name"]), type_name}:
                local_type_candidates.setdefault(alias, set()).add(type_id)

        local_type_by_name = {
            alias: next(iter(candidates))
            for alias, candidates in local_type_candidates.items()
            if len(candidates) == 1
        }

        for item, type_name, canonical_type_id, type_id in type_entries:
            semantic_kind = (
                "data_record"
                if item["kind"] in {"record", "class"}
                else "interface_contract"
                if item["kind"] == "interface"
                else "data_record"
            )
            type_node = SemanticNode(
                id=type_id,
                kind="semantic_node",
                semantic_kind=semantic_kind,
                language="java",
                symbol=item["name"],
                attributes={
                    **item,
                    **semantic_identity_attributes(canonical_type_id),
                },
                provenance=Provenance(
                    file=path,
                    language="java",
                    symbol=item["name"],
                    line_start=item["line_start"],
                    line_end=item["line_end"],
                    parser=parser_strategy,
                    confidence=confidence,
                ),
            ).as_record()
            nodes.append(type_node)
            for prop in item.get("properties") or []:
                canonical_prop_id = (
                    f"{canonical_type_id}:property:{prop['name']}"
                )
                prop_id = self._local_id(
                    path=path,
                    symbol_kind="property",
                    canonical_id=canonical_prop_id,
                    local_qualifier=(
                        f"{type_name}.property:{prop['name']}"
                    ),
                    provenance_line_start=int(prop["line_start"]),
                    provenance_column_start=int(
                        prop.get("column_start") or 1
                    ),
                )
                nodes.append(
                    SemanticNode(
                        id=prop_id,
                        kind="semantic_node",
                        semantic_kind=(
                            "optional_absence"
                            if prop.get("nullability") == "optional_absence"
                            else "property"
                        ),
                        language="java",
                        symbol=f"{item['name']}.{prop['name']}",
                        attributes={
                            **prop,
                            **semantic_identity_attributes(
                                canonical_prop_id
                            ),
                        },
                        provenance=Provenance(
                            file=path,
                            language="java",
                            symbol=f"{item['name']}.{prop['name']}",
                            line_start=prop["line_start"],
                            line_end=prop["line_start"],
                            parser=parser_strategy,
                            confidence=max(0.5, confidence - 0.05),
                        ),
                    ).as_record()
                )
                edges.append(SemanticEdge(source_id=type_id, target_id=prop_id, edge_type="declares").as_record())
            for enum_value in item.get("enum_values") or []:
                canonical_value_id = (
                    f"{canonical_type_id}:enum:{enum_value}"
                )
                value_id = self._local_id(
                    path=path,
                    symbol_kind="enum_value",
                    canonical_id=canonical_value_id,
                    local_qualifier=(
                        f"{type_name}.enum:{enum_value}"
                    ),
                    provenance_line_start=int(item["line_start"]),
                    provenance_column_start=int(
                        item.get("column_start") or 1
                    ),
                )
                nodes.append(
                    SemanticNode(
                        id=value_id,
                        kind="semantic_node",
                        semantic_kind="enum_value",
                        language="java",
                        symbol=f"{item['name']}.{enum_value}",
                        attributes={
                            "name": enum_value,
                            **semantic_identity_attributes(
                                canonical_value_id
                            ),
                        },
                        provenance=Provenance(
                            file=path,
                            language="java",
                            symbol=f"{item['name']}.{enum_value}",
                            line_start=item["line_start"],
                            line_end=item["line_end"],
                            parser=parser_strategy,
                            confidence=max(0.5, confidence - 0.08),
                        ),
                    ).as_record()
                )
                edges.append(SemanticEdge(source_id=type_id, target_id=value_id, edge_type="declares").as_record())
            for method in item.get("methods") or []:
                canonical_method_id = (
                    f"{canonical_type_id}:method:{method['name']}"
                )
                method_qualifier = self._method_qualifier(
                    type_name=type_name,
                    method=method,
                )
                method_id = self._local_id(
                    path=path,
                    symbol_kind="method",
                    canonical_id=canonical_method_id,
                    local_qualifier=method_qualifier,
                    provenance_line_start=int(method["line_start"]),
                    provenance_column_start=int(
                        method.get("column_start") or 1
                    ),
                )
                nodes.append(
                    SemanticNode(
                        id=method_id,
                        kind="semantic_node",
                        semantic_kind="function_signature",
                        language="java",
                        symbol=f"{item['name']}.{method['name']}",
                        attributes={
                            **method,
                            **semantic_identity_attributes(
                                canonical_method_id
                            ),
                        },
                        provenance=Provenance(
                            file=path,
                            language="java",
                            symbol=f"{item['name']}.{method['name']}",
                            line_start=method["line_start"],
                            line_end=method["line_start"],
                            parser=parser_strategy,
                            confidence=max(0.5, confidence - 0.1),
                        ),
                    ).as_record()
                )
                edges.append(SemanticEdge(source_id=type_id, target_id=method_id, edge_type="declares").as_record())
                for thrown in method.get("throws") or []:
                    canonical_thrown_id = (
                        f"{canonical_method_id}:throws:{thrown}"
                    )
                    thrown_id = self._local_id(
                        path=path,
                        symbol_kind="exception",
                        canonical_id=canonical_thrown_id,
                        local_qualifier=(
                            f"{method_qualifier}:throws:{thrown}"
                        ),
                        provenance_line_start=int(method["line_start"]),
                        provenance_column_start=int(
                            method.get("column_start") or 1
                        ),
                    )
                    nodes.append(
                        SemanticNode(
                            id=thrown_id,
                            kind="effect_node",
                            semantic_kind="exception_flow",
                            language="java",
                            symbol=thrown,
                            attributes={
                                "throws": thrown,
                                **semantic_identity_attributes(
                                    canonical_thrown_id
                                ),
                            },
                            provenance=Provenance(
                                file=path,
                                language="java",
                                symbol=thrown,
                                line_start=method["line_start"],
                                line_end=method["line_start"],
                                parser=parser_strategy,
                                confidence=max(0.5, confidence - 0.12),
                            ),
                        ).as_record()
                    )
                    edges.append(SemanticEdge(source_id=method_id, target_id=thrown_id, edge_type="throws").as_record())
            if item.get("parent_type"):
                parent_name = str(item["parent_type"])
                parent_id = local_type_by_name.get(parent_name) or (
                    f"semantic:java:class:{parent_name}"
                )
                edges.append(SemanticEdge(source_id=parent_id, target_id=type_id, edge_type="declares").as_record())
            for base_type in item.get("extends") or []:
                target_id = local_type_by_name.get(str(base_type)) or (
                    f"semantic:java:type:{base_type}"
                )
                edges.append(
                    SemanticEdge(
                        source_id=type_id,
                        target_id=target_id,
                        edge_type="extends",
                    ).as_record()
                )
            for interface_type in item.get("implements") or []:
                target_id = local_type_by_name.get(
                    str(interface_type)
                ) or f"semantic:java:type:{interface_type}"
                edges.append(
                    SemanticEdge(
                        source_id=type_id,
                        target_id=target_id,
                        edge_type="implements",
                    ).as_record()
                )
        return {
            "nodes": nodes,
            "edges": edges,
            "diagnostics": list(parsed.get("diagnostics") or []),
            "parser_strategy": parsed.get("parser_strategy"),
            "fallback_reason": parsed.get("fallback_reason"),
        }

    def _local_id(
        self,
        *,
        path: str,
        symbol_kind: str,
        canonical_id: str,
        local_qualifier: str,
        provenance_line_start: int,
        provenance_column_start: int,
    ) -> str:
        return semantic_occurrence_symbol_id(
            self._symbol_identity,
            language=self.language,
            path=path,
            symbol_kind=symbol_kind,
            canonical_id=canonical_id,
            local_qualifier=local_qualifier,
            provenance_line_start=provenance_line_start,
            provenance_column_start=provenance_column_start,
        )

    @staticmethod
    def _method_qualifier(
        *,
        type_name: str,
        method: dict,
    ) -> str:
        parameter_types = ",".join(
            str(parameter.get("type") or "").strip()
            for parameter in list(method.get("parameters") or [])
            if isinstance(parameter, dict)
        )
        method_kind = str(method.get("kind") or "method")
        type_parameters = str(method.get("type_parameters") or "").strip()
        return (
            f"{type_name}.{method_kind}:{method['name']}"
            f"{type_parameters}({parameter_types})"
        )

    def _parse_imports(self, content: str) -> list[dict]:
        imports = []
        for match in re.finditer(r"^\s*import\s+(?P<static>static\s+)?(?P<name>[\w.*]+)\s*;", content, re.MULTILINE):
            imports.append(
                {
                    "name": match.group("name"),
                    "kind": "static_import" if match.group("static") else "import",
                    "line_start": content.count("\n", 0, match.start()) + 1,
                    "column_start": (
                        match.start()
                        - content.rfind("\n", 0, match.start())
                    ),
                }
            )
        return imports

    def _parse_types(self, path: str, content: str) -> list[dict]:
        lines = content.splitlines()
        types = []
        type_pattern = (
            r"\b(public\s+)?(record|class|enum|interface)\s+(\w+)"
            r"(\s*\((?P<components>[^)]*)\))?([^{;]*)\{"
        )
        for match in re.finditer(type_pattern, content, re.MULTILINE):
            kind = match.group(2)
            name = match.group(3)
            start_line = content[: match.start()].count("\n") + 1
            end_line = self._find_block_end_line(content, match.end())
            block = "\n".join(lines[start_line - 1 : end_line])
            item = {
                "name": name,
                "kind": kind,
                "line_start": start_line,
                "column_start": (
                    match.start()
                    - content.rfind("\n", 0, match.start())
                ),
                "line_end": end_line,
                "properties": [],
                "methods": [],
                "enum_values": [],
                "annotations": self._preceding_annotations(lines, start_line),
                "unsupported": [],
            }
            if kind == "record":
                components = match.group("components") or ""
                item["properties"] = self._parse_record_components(
                    components,
                    content=content,
                    start_offset=match.start("components"),
                )
            elif kind == "class":
                item["properties"] = self._parse_fields(block, start_line)
            elif kind == "enum":
                item["enum_values"] = self._parse_enum_values(block)
            if kind in {"class", "interface"}:
                item["methods"] = self._parse_methods(block, start_line)
            if re.search(r"\b(synchronized|native)\b", block):
                item["unsupported"].append(
                    {
                        "code": "unsupported_construct",
                        "reason": "synchronized_or_native_member",
                        "path": path,
                    }
                )
            if _UNSUPPORTED_CONTROL_FLOW_RE.search(block):
                item["unsupported"].append(
                    {
                        "code": "unsupported_control_flow",
                        "reason": "break_continue_synchronized_or_goto_detected",
                        "path": path,
                    }
                )
            types.append(item)
        return types

    def _parse_record_components(
        self,
        components: str,
        *,
        content: str,
        start_offset: int,
    ) -> list[dict]:
        props = []
        for order, (part, relative_offset) in enumerate(
            _split_top_level_commas_with_offsets(components)
        ):
            tokens = part.strip().split()
            annotations = [token for token in tokens if token.startswith("@")]
            tokens = [token for token in tokens if not token.startswith("@")]
            if len(tokens) < 2:
                continue
            type_name = " ".join(tokens[:-1])
            name = tokens[-1]
            nullability = infer_java_nullability(type_name, annotations)
            absolute_offset = start_offset + relative_offset
            props.append(
                {
                    "name": name,
                    "type": type_name,
                    "order": order,
                    "annotations": annotations,
                    "nullability": nullability.state,
                    "warnings": list(nullability.warnings),
                    "line_start": (
                        content.count("\n", 0, absolute_offset) + 1
                    ),
                    "column_start": (
                        absolute_offset
                        - content.rfind("\n", 0, absolute_offset)
                    ),
                }
            )
        return props

    def _parse_fields(self, block: str, line_offset: int) -> list[dict]:
        props = []
        body_start = block.find("{") + 1
        body = block[body_start:]
        field_re = re.compile(
            r"(?P<annotations>(?:@\w+\s+)*)\b(?:private|public|protected)?\s*"
            r"(?:final\s+)?(?P<type>[\w<>?, ]+)\s+(?P<name>\w+)\s*"
            r"(?:=[^;]*)?;",
            re.MULTILINE,
        )
        body_line_offset = line_offset + block.split("{", 1)[0].count("\n")
        for order, match in enumerate(field_re.finditer(body)):
            block_offset = body_start + match.start()
            type_name = " ".join(match.group("type").split())
            if "(" in type_name or "{" in type_name or type_name in {"return", "new"}:
                continue
            annotations = re.findall(r"@\w+", match.group("annotations") or "")
            nullability = infer_java_nullability(type_name, annotations)
            props.append(
                {
                    "name": match.group("name"),
                    "type": type_name,
                    "order": order,
                    "annotations": annotations,
                    "nullability": nullability.state,
                    "warnings": list(nullability.warnings),
                    "line_start": (
                        body_line_offset + body[: match.start()].count("\n")
                    ),
                    "column_start": (
                        block_offset
                        - block.rfind("\n", 0, block_offset)
                    ),
                }
            )
        return props

    def _parse_methods(self, block: str, line_offset: int) -> list[dict]:
        methods = []
        body_start = block.find("{") + 1
        body = block[body_start:]
        body_line_offset = line_offset + block.split("{", 1)[0].count("\n")
        method_re = re.compile(
            r"(?P<annotations>(?:@\w+\s+)*)\b"
            r"(?P<visibility>public|protected|private)?\s*"
            r"(?P<static>static\s+)?(?P<final>final\s+)?"
            r"(?P<return>[\w<>?, ]+)\s+(?P<name>\w+)\s*"
            r"\((?P<params>[^)]*)\)\s*"
            r"(?:throws\s+(?P<throws>[^{;]+))?[{;]",
            re.MULTILINE,
        )
        for match in method_re.finditer(body):
            block_offset = body_start + match.start()
            return_type = " ".join((match.group("return") or "").split())
            if return_type in {"if", "for", "while", "switch", "catch", "new"}:
                continue
            throws_raw = [item.strip() for item in (match.group("throws") or "").split(",") if item.strip()]
            classified_throws = [_classify_throw(exc) for exc in throws_raw]
            methods.append({
                "name": match.group("name"),
                "return_type": return_type,
                "parameters": self._parse_parameters(match.group("params") or ""),
                "throws": throws_raw,
                "throws_classified": classified_throws,
                "visibility": match.group("visibility") or "package",
                "static": bool(match.group("static")),
                "final": bool(match.group("final")),
                "annotations": re.findall(r"@\w+", match.group("annotations") or ""),
                "side_effects": ["unknown_side_effect"],
                "contracts": {"preconditions": [], "postconditions": [], "invariants": []},
                "line_start": body_line_offset + body[: match.start()].count("\n"),
                "column_start": (
                    block_offset
                    - block.rfind("\n", 0, block_offset)
                ),
            })
        return methods

    def _parse_parameters(self, params: str) -> list[dict]:
        result = []
        for order, part in enumerate(_split_top_level_commas(params)):
            tokens = part.strip().split()
            annotations = [token for token in tokens if token.startswith("@")]
            tokens = [token for token in tokens if not token.startswith("@")]
            if len(tokens) < 2:
                continue
            result.append(
                {
                    "name": tokens[-1],
                    "type": " ".join(tokens[:-1]),
                    "order": order,
                    "annotations": annotations,
                }
            )
        return result

    def _parse_enum_values(self, block: str) -> list[str]:
        body = block.split("{", 1)[-1].split(";", 1)[0]
        result = []
        for item in body.replace("\n", " ").split(","):
            cleaned = item.strip().rstrip("}").strip().split("(")[0].strip()
            if cleaned and re.match(r"^[A-Z][A-Z0-9_]*$", cleaned):
                result.append(cleaned)
        return result

    def _find_block_end_line(self, content: str, start: int) -> int:
        depth = 1
        for index in range(start, len(content)):
            if content[index] == "{":
                depth += 1
            elif content[index] == "}":
                depth -= 1
                if depth == 0:
                    return content[: index].count("\n") + 1
        return content.count("\n") + 1

    def _preceding_annotations(self, lines: list[str], start_line: int) -> list[str]:
        result = []
        index = start_line - 2
        while index >= 0 and lines[index].strip().startswith("@"):
            result.append(lines[index].strip())
            index -= 1
        return list(reversed(result))


_CHECKED_EXCEPTIONS = {
    "Exception", "IOException", "SQLException", "ParseException", "ClassNotFoundException",
    "InterruptedException", "CloneNotSupportedException", "ReflectiveOperationException",
}

_UNCHECKED_EXCEPTIONS = {
    "RuntimeException", "NullPointerException", "IllegalArgumentException",
    "IllegalStateException", "IndexOutOfBoundsException", "UnsupportedOperationException",
    "ArithmeticException", "ClassCastException", "ConcurrentModificationException",
}

_UNSUPPORTED_CONTROL_FLOW_RE = re.compile(r"\b(break|continue|synchronized|goto)\b")


def _classify_throw(exception_name: str) -> dict:
    name = str(exception_name or "").strip().split(".")[-1]
    if name in _UNCHECKED_EXCEPTIONS or name.endswith("Error"):
        kind = "unchecked_exception"
    elif name in _CHECKED_EXCEPTIONS:
        kind = "checked_exception"
    else:
        kind = "may_throw"
    return {"name": exception_name, "kind": kind}


def _split_top_level_commas(value: str) -> list[str]:
    return [part for part, _offset in _split_top_level_commas_with_offsets(value)]


def _split_top_level_commas_with_offsets(
    value: str,
) -> list[tuple[str, int]]:
    depth = 0
    current = []
    parts: list[tuple[str, int]] = []
    segment_start = 0
    for index, ch in enumerate(value):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            raw = "".join(current)
            stripped = raw.strip()
            if stripped:
                parts.append(
                    (
                        stripped,
                        segment_start + len(raw) - len(raw.lstrip()),
                    )
                )
            current = []
            segment_start = index + 1
            continue
        current.append(ch)
    if current:
        raw = "".join(current)
        stripped = raw.strip()
        if stripped:
            parts.append(
                (
                    stripped,
                    segment_start + len(raw) - len(raw.lstrip()),
                )
            )
    return parts
