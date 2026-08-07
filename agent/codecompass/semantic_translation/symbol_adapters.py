from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agent.codecompass.semantic_translation.models import Provenance, SemanticEdge, SemanticNode, diagnostic
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    DeterministicSemanticSymbolIdentityFactory,
    LegacySemanticSymbolIdentityPort,
    SemanticSymbolIdentityPort,
    semantic_identity_attributes,
    semantic_occurrence_symbol_id,
)


@dataclass(frozen=True)
class RegexSymbolLanguageAdapter:
    """Safe, non-executing symbol fallback for languages without a verified parser.

    These adapters deliberately promise symbol extraction only. They emit graph
    records so consumers share one record contract, but their diagnostics and
    confidence make the reduced support explicit.
    """

    language: str
    supported_extensions: tuple[str, ...]
    type_pattern: str
    function_pattern: str
    import_pattern: str
    known_limits: tuple[str, ...]
    parser_strategy: str = "regex-symbol-v1"
    semantic_kinds: tuple[str, ...] = ("data_record", "function_signature", "module")
    symbol_identity: (
        SemanticSymbolIdentityPort | LegacySemanticSymbolIdentityPort
    ) = field(
        default_factory=DeterministicSemanticSymbolIdentityFactory,
        repr=False,
        compare=False,
    )

    def detect(self, path: str, content: str) -> bool:
        del content
        return Path(path).suffix.lower() in self.supported_extensions

    def parse(self, path: str, content: str) -> dict:
        types = _matches(content, self.type_pattern, default_kind="type")
        functions = _matches(content, self.function_pattern, default_kind="function")
        imports = _matches(content, self.import_pattern, default_kind="import")
        return {
            "path": path,
            "content": content,
            "types": types,
            "functions": functions,
            "imports": imports,
            "diagnostics": [
                diagnostic(
                    "semantic_symbol_fallback",
                    f"{self.language} uses a regex symbol fallback; semantic relationships are limited.",
                    path=path,
                )
            ],
            "confidence": 0.55,
        }

    def extract_symbols(self, parsed: dict) -> list[dict]:
        return [
            {
                "symbol": item["name"],
                "kind": item["kind"],
                "line_start": item["line_start"],
                "confidence": 0.55,
            }
            for group in ("types", "functions")
            for item in parsed.get(group, [])
        ]

    def extract_types(self, parsed: dict) -> list[dict]:
        return list(parsed.get("types") or [])

    def extract_semantics(self, parsed: dict) -> list[dict]:
        return [
            {"symbol": item["name"], "semantic_kind": "data_record", "confidence": 0.55}
            for item in parsed.get("types") or []
        ]

    def emit_graph_records(self, path: str, content: str) -> dict:
        parsed = self.parse(path, content)
        nodes: list[dict] = []
        edges: list[dict] = []
        file_key = path.replace("\\", "/")
        module_node_ids: set[str] = set()
        for item in parsed["types"]:
            canonical_id = (
                f"semantic:{self.language}:type:"
                f"{item['kind']}:{item['name']}"
            )
            node_id = semantic_occurrence_symbol_id(
                self.symbol_identity,
                language=self.language,
                path=path,
                symbol_kind="type",
                canonical_id=canonical_id,
                local_qualifier=f"{item['kind']}:{item['name']}",
                provenance_line_start=int(item["line_start"]),
                provenance_column_start=int(item["column_start"]),
            )
            nodes.append(
                self._node(
                    path,
                    node_id,
                    {
                        **item,
                        **semantic_identity_attributes(canonical_id),
                    },
                    "data_record",
                )
            )
        for item in parsed["functions"]:
            canonical_id = (
                f"semantic:{self.language}:function:{item['name']}"
            )
            node_id = semantic_occurrence_symbol_id(
                self.symbol_identity,
                language=self.language,
                path=path,
                symbol_kind="function",
                canonical_id=canonical_id,
                local_qualifier=item["name"],
                provenance_line_start=int(item["line_start"]),
                provenance_column_start=int(item["column_start"]),
            )
            nodes.append(
                self._node(
                    path,
                    node_id,
                    {
                        **item,
                        **semantic_identity_attributes(canonical_id),
                    },
                    "function_signature",
                )
            )
        for item in parsed["imports"]:
            canonical_module_id = (
                f"semantic:{self.language}:module:{item['name']}"
            )
            module_id = semantic_occurrence_symbol_id(
                self.symbol_identity,
                language=self.language,
                path=path,
                symbol_kind="module",
                canonical_id=canonical_module_id,
                local_qualifier=item["name"],
                provenance_line_start=int(item["line_start"]),
                provenance_column_start=int(item["column_start"]),
            )
            if module_id not in module_node_ids:
                nodes.append(
                    self._node(
                        path,
                        module_id,
                        {
                            **item,
                            **semantic_identity_attributes(
                                canonical_module_id
                            ),
                        },
                        "module",
                    )
                )
                module_node_ids.add(module_id)
            edges.append(
                SemanticEdge(
                    source_id=f"semantic:{self.language}:file:{file_key}",
                    target_id=module_id,
                    edge_type="imports",
                    attributes={"confidence": 0.55},
                ).as_record()
            )
        return {"nodes": nodes, "edges": edges, "diagnostics": parsed["diagnostics"]}

    def _node(self, path: str, node_id: str, item: dict, semantic_kind: str) -> dict:
        return SemanticNode(
            id=node_id,
            kind="symbol_node",
            semantic_kind=semantic_kind,
            language=self.language,
            symbol=item["name"],
            attributes={**item, "support_level": "symbol_index", "confidence": 0.55},
            provenance=Provenance(
                file=path,
                language=self.language,
                symbol=item["name"],
                line_start=item["line_start"],
                line_end=item["line_start"],
                parser=self.parser_strategy,
                confidence=0.55,
            ),
        ).as_record()


def _matches(content: str, pattern: str, *, default_kind: str) -> list[dict]:
    if not pattern:
        return []
    results: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for match in re.finditer(pattern, content, re.MULTILINE):
        groups = match.groupdict()
        name = str(groups.get("name") or groups.get("module") or "").strip()
        if not name:
            continue
        line = content.count("\n", 0, match.start()) + 1
        column = match.start() - content.rfind("\n", 0, match.start())
        identity = (name, line, column)
        if identity in seen:
            continue
        seen.add(identity)
        results.append(
            {
                "name": name,
                "kind": str(groups.get("kind") or default_kind),
                "line_start": line,
                "column_start": column,
            }
        )
    return results


def default_symbol_adapters() -> tuple[RegexSymbolLanguageAdapter, ...]:
    limitation = (
        "regex fallback provides symbol_index only",
        "complex nesting, macros and generated syntax are not resolved",
        "cross-file type resolution is not performed",
    )
    return (
        RegexSymbolLanguageAdapter(
            "go", (".go",),
            r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface|\w+)",
            r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(",
            r"^\s*import\s+(?:\w+\s+)?[\"`](?P<module>[^\"`]+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "rust", (".rs",),
            r"^\s*(?:pub\s+)?(?P<kind>struct|enum|trait|type)\s+(?P<name>[A-Za-z_]\w*)",
            r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*(?:<[^>]+>)?\s*\(",
            r"^\s*use\s+(?P<module>[^;]+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "c", (".c", ".h"),
            r"^\s*(?P<kind>struct|enum|union)\s+(?P<name>[A-Za-z_]\w*)",
            r"^\s*(?:[A-Za-z_]\w*[\s*]+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{",
            r"^\s*#\s*include\s*[<\"](?P<module>[^>\"]+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "cpp", (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
            r"^\s*(?P<kind>class|struct|enum|union|concept)\s+(?P<name>[A-Za-z_]\w*)",
            r"^\s*(?:[A-Za-z_:<>~]\w*[\s:*&<>]+)+(?P<name>[A-Za-z_~]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{",
            r"^\s*#\s*include\s*[<\"](?P<module>[^>\"]+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "csharp", (".cs",),
            r"^\s*(?:public|internal|private|protected|static|sealed|abstract|partial|record|\s)*\s*(?P<kind>class|interface|record|struct|enum)\s+(?P<name>[A-Za-z_]\w*)",
            r"^\s*(?:public|internal|private|protected|static|virtual|override|async|\s)+[\w<>,?\[\].]+\s+(?P<name>[A-Za-z_]\w*)\s*\(",
            r"^\s*using\s+(?P<module>[^;=]+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "ruby", (".rb",),
            r"^\s*(?P<kind>class|module)\s+(?P<name>[A-Za-z_:]\w*)",
            r"^\s*def\s+(?P<name>[A-Za-z_?!]\w*[?!]?)",
            r"^\s*(?:require|require_relative)\s*[\"'](?P<module>[^\"']+)", limitation,
        ),
        RegexSymbolLanguageAdapter(
            "php", (".php",),
            r"^\s*(?:(?:final|abstract|readonly)\s+)?(?P<kind>class|interface|trait|enum)\s+(?P<name>[A-Za-z_]\w*)",
            r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+(?P<name>[A-Za-z_]\w*)\s*\(",
            r"^\s*(?:require|require_once|include|include_once|use)\s*\(?[\"']?(?P<module>[^\"';)]+)", limitation,
        ),
    )
