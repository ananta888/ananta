"""Bounded static symbol adapters for P2 languages and component files.

The adapters in this module intentionally stop at ``symbol_index``.  They do
not compile, import, render, execute, or resolve repository code, and they emit
no relationship edges.  This keeps the reduced-confidence fallback useful
without advertising compiler or semantic-graph guarantees that it cannot
provide.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from agent.codecompass.semantic_translation.models import Provenance, SemanticNode, diagnostic
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    DeterministicSemanticSymbolIdentityFactory,
    LegacySemanticSymbolIdentityPort,
    SemanticSymbolIdentityPort,
    semantic_identity_attributes,
    semantic_occurrence_symbol_id,
)


@dataclass(frozen=True, slots=True)
class StaticSymbolPattern:
    """One anchored declaration pattern and its conservative symbol kind."""

    kind: str
    expression: str


@dataclass(frozen=True, slots=True)
class StaticSymbolLanguageAdapter:
    """Non-executing, regex-based declaration index for one language family.

    Patterns must expose a named ``name`` group.  Import patterns expose either
    ``module`` or ``name``.  All matching is bounded by ``SemanticAdapterRegistry``
    parser limits before this adapter is called.
    """

    language: str
    supported_extensions: tuple[str, ...]
    symbol_patterns: tuple[StaticSymbolPattern, ...]
    import_patterns: tuple[str, ...]
    known_limits: tuple[str, ...]
    confidence: float
    parser_strategy: str = "regex-static-symbol-v1"
    script_regions_only: bool = False
    component_from_filename: bool = False
    semantic_kinds: tuple[str, ...] = ()
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
        searchable = _script_region_view(content) if self.script_regions_only else content
        filtered = _mask_comments(searchable, language=self.language)
        declarations = _extract_declarations(
            filtered,
            self.symbol_patterns,
            confidence=self.confidence,
        )
        if self.component_from_filename:
            component = _component_symbol(path, confidence=min(self.confidence, 0.4))
            if component is not None:
                declarations.append(component)
        declarations = _deduplicate_and_sort(declarations)
        imports = _extract_imports(filtered, self.import_patterns, confidence=self.confidence)
        diagnostics = [
            diagnostic(
                "semantic_static_symbol_index",
                (
                    f"{self.language} uses a reduced-confidence static declaration index; "
                    "no relationship or cross-file semantic analysis is performed."
                ),
                path=path,
            )
        ]
        if self.script_regions_only and not _has_script_region(content):
            diagnostics.append(
                diagnostic(
                    "static_symbol_script_region_missing",
                    "No closed script region was found; only the filename-derived component symbol is available.",
                    path=path,
                )
            )
        return {
            "path": path,
            "content": content,
            "symbols": declarations,
            "types": [item for item in declarations if item["kind"] in _TYPE_KINDS],
            "functions": [item for item in declarations if item["kind"] == "function"],
            "imports": imports,
            "diagnostics": diagnostics,
            "confidence": self.confidence,
            "parser_strategy": self.parser_strategy,
            "support_level": "symbol_index",
            "known_limits": list(self.known_limits),
        }

    def extract_symbols(self, parsed: dict) -> list[dict]:
        return list(parsed.get("symbols") or [])

    def extract_types(self, parsed: dict) -> list[dict]:
        return list(parsed.get("types") or [])

    def extract_semantics(self, parsed: dict) -> list[dict]:
        # Deliberately empty: syntax-shaped declarations are not semantic facts.
        return []

    def emit_graph_records(self, path: str, content: str) -> dict:
        parsed = self.parse(path, content)
        nodes: list[dict] = []
        for item in parsed["symbols"]:
            canonical_id = (
                f"semantic:{self.language}:symbol:"
                f"{item['kind']}:{item['name']}"
            )
            node_id = semantic_occurrence_symbol_id(
                self.symbol_identity,
                language=self.language,
                path=path,
                symbol_kind="symbol",
                canonical_id=canonical_id,
                local_qualifier=f"{item['kind']}:{item['name']}",
                provenance_line_start=int(item["line_start"]),
                provenance_column_start=int(item["column_start"]),
            )
            nodes.append(
                SemanticNode(
                    id=node_id,
                    kind="symbol_node",
                    semantic_kind=_semantic_kind(item["kind"]),
                    language=self.language,
                    symbol=item["name"],
                    attributes={
                        **item,
                        **semantic_identity_attributes(canonical_id),
                        "support_level": "symbol_index",
                        "confidence": item["confidence"],
                    },
                    provenance=Provenance(
                        file=path,
                        language=self.language,
                        symbol=item["name"],
                        line_start=item["line_start"],
                        line_end=item["line_start"],
                        parser=self.parser_strategy,
                        confidence=item["confidence"],
                    ),
                ).as_record()
            )
        return {
            "nodes": nodes,
            "edges": [],
            "diagnostics": parsed["diagnostics"],
            "support_level": "symbol_index",
            "confidence": self.confidence,
            "known_limits": list(self.known_limits),
        }


_TYPE_KINDS = frozenset(
    {
        "actor",
        "class",
        "component",
        "enum",
        "extension",
        "interface",
        "mixin",
        "object",
        "protocol",
        "struct",
        "trait",
        "type",
        "typealias",
        "typedef",
    }
)

_COMMON_LIMITS = (
    "symbol_index only; no relationship, reference, or cross-file type resolution",
    "declaration matching is static and regex-based rather than compiler-backed",
    "complex multiline declarations, generated syntax, and macro expansions may be omitted",
    "comment masking is conservative and does not provide a complete language lexer",
)


def _pattern(kind: str, expression: str) -> StaticSymbolPattern:
    return StaticSymbolPattern(kind=kind, expression=expression)


def default_static_symbol_adapters() -> tuple[StaticSymbolLanguageAdapter, ...]:
    """Return deterministic P2 symbol-index adapters with explicit limits."""

    ecma_types = _pattern(
        "type",
        r"^\s*(?:export\s+)?(?:default\s+)?(?P<kind>class|interface|enum|type)\s+(?P<name>[A-Za-z_$][\w$]*)",
    )
    ecma_functions = _pattern(
        "function",
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(",
    )
    ecma_variables = _pattern(
        "variable",
        r"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\b",
    )
    ecma_imports = (
        r"^\s*import\s+(?:[^\n;]*?\s+from\s+)?[\"'](?P<module>[^\"']+)[\"']",
    )
    sfc_limits = (
        *_COMMON_LIMITS,
        "only closed script regions are inspected; template and style content is not parsed",
        "the component symbol is inferred from the filename with lower confidence",
    )
    return (
        StaticSymbolLanguageAdapter(
            language="kotlin",
            supported_extensions=(".kt", ".kts"),
            symbol_patterns=(
                _pattern(
                    "type",
                    r"^\s*(?:(?:public|private|protected|internal|expect|actual|data|sealed|enum|annotation|value|open|abstract|final|inner)\s+)*(?P<kind>class|interface|object|typealias)\s+(?P<name>[A-Za-z_]\w*)",
                ),
                _pattern(
                    "function",
                    r"^\s*(?:(?:public|private|protected|internal|expect|actual|suspend|inline|operator|infix|tailrec|external|override)\s+)*fun\s+(?:<[^>\n]+>\s*)?(?:[A-Za-z_]\w*(?:[?.][A-Za-z_]\w*)*\.)?(?P<name>[A-Za-z_]\w*)\s*\(",
                ),
                _pattern(
                    "variable",
                    r"^\s*(?:(?:public|private|protected|internal|const|lateinit|override)\s+)*(?:val|var)\s+(?P<name>[A-Za-z_]\w*)\b",
                ),
            ),
            import_patterns=(r"^\s*import\s+(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_*]\w*)*)",),
            known_limits=(
                *_COMMON_LIMITS,
                "Kotlin script and Gradle DSL block semantics remain the rag-helper extractor's responsibility",
                "receiver types and local declarations are not distinguished from top-level declarations",
            ),
            confidence=0.5,
        ),
        StaticSymbolLanguageAdapter(
            language="swift",
            supported_extensions=(".swift",),
            symbol_patterns=(
                _pattern(
                    "type",
                    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|fileprivate|internal|open|final|indirect|nonisolated)\s+)*(?P<kind>class|struct|enum|protocol|actor|typealias|extension)\s+(?P<name>[A-Za-z_]\w*)",
                ),
                _pattern(
                    "function",
                    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|fileprivate|internal|open|final|static|class|override|mutating|nonmutating|async|distributed)\s+)*func\s+(?P<name>[A-Za-z_]\w*)\s*[<(]",
                ),
                _pattern(
                    "variable",
                    r"^\s*(?:(?:public|private|fileprivate|internal|open|final|static|class|lazy|weak|unowned)\s+)*(?:let|var)\s+(?P<name>[A-Za-z_]\w*)\b",
                ),
            ),
            import_patterns=(
                r"^\s*import\s+(?:(?:typealias|struct|class|enum|protocol|let|var|func)\s+)?(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
            ),
            known_limits=(*_COMMON_LIMITS, "conditional compilation and synthesized declarations are not resolved"),
            confidence=0.5,
        ),
        StaticSymbolLanguageAdapter(
            language="scala",
            supported_extensions=(".scala",),
            symbol_patterns=(
                _pattern(
                    "type",
                    r"^\s*(?:(?:private|protected)(?:\[[^\]\n]+\])?\s+)*(?:(?:sealed|abstract|final|case|implicit|lazy|open|opaque)\s+)*(?P<kind>class|trait|object|enum|type)\s+(?P<name>[A-Za-z_]\w*)",
                ),
                _pattern(
                    "function",
                    r"^\s*(?:(?:private|protected)(?:\[[^\]\n]+\])?\s+)*(?:(?:final|implicit|inline|transparent|override|given)\s+)*def\s+(?P<name>[A-Za-z_]\w*)\s*(?:\[|\()",
                ),
                _pattern(
                    "variable",
                    r"^\s*(?:(?:private|protected)(?:\[[^\]\n]+\])?\s+)*(?:(?:final|implicit|lazy|override)\s+)*(?:val|var)\s+(?P<name>[A-Za-z_]\w*)\b",
                ),
            ),
            import_patterns=(r"^\s*import\s+(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",),
            known_limits=(
                *_COMMON_LIMITS,
                "Scala 3 indentation scopes, givens, and extension ownership are not resolved",
            ),
            confidence=0.48,
        ),
        StaticSymbolLanguageAdapter(
            language="lua",
            supported_extensions=(".lua",),
            symbol_patterns=(
                _pattern(
                    "function",
                    r"^\s*(?:local\s+)?function\s+(?P<name>[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*)\s*\(",
                ),
                _pattern("variable", r"^\s*local\s+(?P<name>[A-Za-z_]\w*)\b"),
            ),
            import_patterns=(
                r"^\s*(?:local\s+[A-Za-z_]\w*\s*=\s*)?require\s*\(?\s*[\"'](?P<module>[^\"']+)[\"']",
            ),
            known_limits=(*_COMMON_LIMITS, "metatables and dynamically assigned module members are not resolved"),
            confidence=0.5,
        ),
        StaticSymbolLanguageAdapter(
            language="dart",
            supported_extensions=(".dart",),
            symbol_patterns=(
                _pattern(
                    "type",
                    r"^\s*(?:(?:abstract|base|final|interface|sealed|mixin)\s+)*(?P<kind>class|mixin|enum|extension|typedef)\s+(?:type\s+)?(?P<name>[A-Za-z_]\w*)",
                ),
                _pattern(
                    "function",
                    r"^\s*(?:(?:external|static|abstract|covariant)\s+)*(?:[A-Za-z_]\w*(?:<[^>\n]+>)?\??\s+)+(?P<name>(?!(?:if|for|while|switch|catch)\b)[A-Za-z_]\w*)\s*\(",
                ),
                _pattern(
                    "variable",
                    r"^\s*(?:(?:external|static|late|final|const)\s+)*(?:var|final|const|[A-Za-z_]\w*(?:<[^>\n]+>)?\??)\s+(?P<name>[A-Za-z_]\w*)\b",
                ),
            ),
            import_patterns=(r"^\s*(?:import|export)\s+[\"'](?P<module>[^\"']+)[\"']",),
            known_limits=(
                *_COMMON_LIMITS,
                "constructors, getters, setters, and extension ownership are only partially recognized",
            ),
            confidence=0.47,
        ),
        StaticSymbolLanguageAdapter(
            language="vue",
            supported_extensions=(".vue",),
            symbol_patterns=(ecma_types, ecma_functions, ecma_variables),
            import_patterns=ecma_imports,
            known_limits=sfc_limits,
            confidence=0.45,
            parser_strategy="regex-sfc-script-symbol-v1",
            script_regions_only=True,
            component_from_filename=True,
        ),
        StaticSymbolLanguageAdapter(
            language="svelte",
            supported_extensions=(".svelte",),
            symbol_patterns=(ecma_types, ecma_functions, ecma_variables),
            import_patterns=ecma_imports,
            known_limits=sfc_limits,
            confidence=0.45,
            parser_strategy="regex-sfc-script-symbol-v1",
            script_regions_only=True,
            component_from_filename=True,
        ),
    )


def _extract_declarations(
    content: str,
    patterns: tuple[StaticSymbolPattern, ...],
    *,
    confidence: float,
) -> list[dict]:
    declarations: list[dict] = []
    line_starts = _line_starts(content)
    for declaration_pattern in patterns:
        for match in re.finditer(declaration_pattern.expression, content, re.MULTILINE):
            name = str(match.groupdict().get("name") or "").strip()
            if not name:
                continue
            declarations.append(
                {
                    "name": name,
                    "kind": str(match.groupdict().get("kind") or declaration_pattern.kind),
                    "line_start": bisect_right(line_starts, match.start()),
                    "column_start": _column_start(
                        line_starts,
                        match.start(),
                    ),
                    "confidence": confidence,
                }
            )
    return declarations


def _extract_imports(content: str, patterns: tuple[str, ...], *, confidence: float) -> list[dict]:
    imports: list[dict] = []
    seen: set[tuple[str, int]] = set()
    line_starts = _line_starts(content)
    for expression in patterns:
        for match in re.finditer(expression, content, re.MULTILINE):
            module = str(match.groupdict().get("module") or match.groupdict().get("name") or "").strip()
            if not module:
                continue
            line = bisect_right(line_starts, match.start())
            if (module, line) in seen:
                continue
            seen.add((module, line))
            imports.append(
                {"name": module, "kind": "import", "line_start": line, "confidence": confidence}
            )
    return sorted(imports, key=lambda item: (item["line_start"], item["name"]))


def _deduplicate_and_sort(items: list[dict]) -> list[dict]:
    # A broad variable pattern can also see a typed function declaration.  The
    # patterns are ordered from specific types/functions to variables, so the
    # first declaration at a name+line wins deterministically.
    by_identity: dict[tuple[str, int, int], dict] = {}
    for item in items:
        identity = (
            str(item["name"]),
            int(item["line_start"]),
            int(item["column_start"]),
        )
        by_identity.setdefault(identity, item)
    return sorted(
        by_identity.values(),
        key=lambda item: (
            item["line_start"],
            item["column_start"],
            item["name"],
            item["kind"],
        ),
    )


def _component_symbol(path: str, *, confidence: float) -> dict | None:
    name = Path(path).stem.strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$-]*", name):
        return None
    return {
        "name": name,
        "kind": "component",
        "line_start": 1,
        "column_start": 1,
        "confidence": confidence,
    }


def _has_script_region(content: str) -> bool:
    return next(_script_region_spans(content), None) is not None


def _script_region_view(content: str) -> str:
    """Keep closed script bodies and whitespace-mask all other SFC content."""

    masked = ["\n" if character == "\n" else " " for character in content]
    for start, end in _script_region_spans(content):
        masked[start:end] = content[start:end]
    return "".join(masked)


def _script_region_spans(content: str):
    """Yield closed script-body spans using bounded forward searches only."""

    lowered = content.lower()
    cursor = 0
    while cursor < len(content):
        opening = lowered.find("<script", cursor)
        if opening < 0:
            return
        boundary = opening + len("<script")
        if boundary < len(content) and (content[boundary].isalnum() or content[boundary] in "_-:"):
            cursor = boundary
            continue
        body_start = content.find(">", boundary)
        if body_start < 0:
            return
        body_start += 1
        closing = lowered.find("</script", body_start)
        if closing < 0:
            return
        close_end = content.find(">", closing + len("</script"))
        if close_end < 0:
            return
        yield body_start, closing
        cursor = close_end + 1


def _mask_comments(content: str, *, language: str) -> str:
    """Whitespace-mask common comments while preserving offsets and line numbers."""

    masked = list(content)
    block_expression = r"--\[\[.*?(?:\]\]|$)" if language == "lua" else r"/\*.*?(?:\*/|$)"
    for match in re.finditer(block_expression, content, re.DOTALL):
        _mask_range(masked, content, match.start(), match.end())
    marker = "--" if language == "lua" else "//"
    for line_match in re.finditer(r"^.*$", content, re.MULTILINE):
        line = content[line_match.start() : line_match.end()]
        marker_index = line.find(marker)
        if marker_index >= 0:
            _mask_range(masked, content, line_match.start() + marker_index, line_match.end())
    return "".join(masked)


def _mask_range(masked: list[str], content: str, start: int, end: int) -> None:
    for index in range(start, min(end, len(masked))):
        if content[index] != "\n":
            masked[index] = " "


def _line_starts(content: str) -> tuple[int, ...]:
    return (0, *(match.end() for match in re.finditer("\n", content)))


def _column_start(line_starts: tuple[int, ...], offset: int) -> int:
    line_index = bisect_right(line_starts, offset) - 1
    return offset - line_starts[line_index] + 1


def _semantic_kind(symbol_kind: str) -> str:
    if symbol_kind == "component":
        return "component"
    if symbol_kind == "function":
        return "function_signature"
    if symbol_kind in {"type", "typealias", "typedef"}:
        return "type_alias"
    if symbol_kind == "variable":
        return "property"
    return "data_record"
