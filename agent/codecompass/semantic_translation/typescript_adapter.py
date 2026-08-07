from __future__ import annotations

import re
from pathlib import Path

from agent.codecompass.semantic_translation.models import Provenance, SemanticEdge, SemanticNode, diagnostic
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    DeterministicSemanticSymbolIdentityFactory,
    SemanticSymbolIdentityPort,
    semantic_identity_attributes,
)

_DECLARATION_RE = re.compile(
    r"(?P<export>\bexport\s+(?:default\s+)?)?"
    r"(?P<abstract>\babstract\s+)?"
    r"(?P<kind>class|interface|type|enum)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)"
    r"(?P<generics>\s*<[^>{};]+>)?"
    r"(?P<tail>[^\n{;=]*)",
    re.MULTILINE,
)
_FUNCTION_RE = re.compile(
    r"(?P<export>\bexport\s+(?:default\s+)?)?"
    r"(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?P<generics><[^>{};]+>)?\s*\((?P<params>[^)]*)\)"
    r"\s*(?::\s*(?P<return>[^\n{;=]+))?",
    re.MULTILINE,
)
_ARROW_RE = re.compile(
    r"(?P<export>\bexport\s+)?(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"(?::\s*[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(
    r"\bimport\s+(?:type\s+)?(?:(?P<binding>[^;\n]+?)\s+from\s+)?"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)",
    re.MULTILINE,
)
_EXPORT_FROM_RE = re.compile(
    r"\bexport\s+(?:type\s+)?(?:\*|\{[^}]*\})\s+from\s+"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)",
    re.MULTILINE,
)
_JSX_TAG_RE = re.compile(r"<(?P<name>[A-Z][\w$.]*|[a-z][\w-]*-[\w-]+)(?:\s|/?>)")
_COMPONENT_SELECTOR_RE = re.compile(
    r"@Component\s*\(\s*\{(?P<body>.*?)\}\s*\)\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)",
    re.DOTALL,
)
_SELECTOR_RE = re.compile(r"\bselector\s*:\s*(['\"])(?P<selector>[^'\"]+?)\1")


class TypeScriptSemanticAdapter:
    """Non-executing structural adapter for TypeScript, JavaScript and JSX.

    The adapter intentionally does not load tsconfig files or execute a compiler.
    It provides deterministic baseline semantics and marks constructs that need a
    full compiler/type-checker as diagnostics rather than claiming resolution.
    """

    language = "typescript"
    supported_extensions = (".ts", ".tsx", ".js", ".jsx")
    parser_strategy = "structural-typescript-v1"
    semantic_kinds = (
        "data_record",
        "interface_contract",
        "type_alias",
        "function_signature",
        "component",
        "module",
    )
    known_limits = (
        "type resolution and overload resolution require a TypeScript compiler",
        "decorator arguments are extracted structurally and never executed",
        "dynamic import expressions and computed exports remain unresolved",
        "method bodies and control flow are not analysed",
    )

    def __init__(
        self,
        *,
        symbol_identity: SemanticSymbolIdentityPort | None = None,
    ) -> None:
        self._symbol_identity = (
            symbol_identity or DeterministicSemanticSymbolIdentityFactory()
        )

    def detect(self, path: str, content: str) -> bool:
        del content
        return Path(path).suffix.lower() in self.supported_extensions

    def parse(self, path: str, content: str) -> dict:
        masked = _mask_comments(content)
        diagnostics: list[dict] = []
        if not _balanced(masked):
            diagnostics.append(
                diagnostic(
                    "typescript_unbalanced_syntax",
                    "Unbalanced delimiters; partial structural results were retained.",
                    path=path,
                )
            )
        declarations = [self._declaration(masked, match) for match in _DECLARATION_RE.finditer(masked)]
        functions = [self._function(masked, match, "function") for match in _FUNCTION_RE.finditer(masked)]
        functions.extend(self._function(masked, match, "arrow_function") for match in _ARROW_RE.finditer(masked))
        imports = [
            {
                "module": match.group("module"),
                "binding": str(match.group("binding") or "").strip(),
                "kind": "import",
                "line_start": _line(masked, match.start()),
            }
            for match in _IMPORT_RE.finditer(masked)
        ]
        exports = [
            {
                "module": match.group("module"),
                "kind": "export_from",
                "line_start": _line(masked, match.start()),
            }
            for match in _EXPORT_FROM_RE.finditer(masked)
        ]
        selectors: list[dict] = []
        for match in _COMPONENT_SELECTOR_RE.finditer(masked):
            selector = _SELECTOR_RE.search(match.group("body"))
            if selector:
                selectors.append(
                    {
                        "class_name": match.group("name"),
                        "selector": selector.group("selector"),
                        "line_start": _line(masked, match.start()),
                        "framework": "angular",
                    }
                )
        jsx_tags = [
            {"name": match.group("name"), "line_start": _line(masked, match.start())}
            for match in _JSX_TAG_RE.finditer(masked)
        ]
        dynamic_import_lines = [
            _line(masked, match.start())
            for match in re.finditer(r"\bimport\s*\(", masked)
        ]
        diagnostics.extend(
            diagnostic(
                "typescript_dynamic_import_unresolved",
                "Dynamic import is indexed but its target is not resolved.",
                path=path,
                line=line,
            )
            for line in dynamic_import_lines
        )
        return {
            "path": path,
            "content": content,
            "types": declarations,
            "functions": _dedupe(functions, keys=("name", "line_start")),
            "imports": imports,
            "exports": exports,
            "jsx_tags": _dedupe(jsx_tags, keys=("name", "line_start")),
            "component_selectors": selectors,
            "diagnostics": diagnostics,
            "confidence": 0.72,
        }

    def extract_symbols(self, parsed: dict) -> list[dict]:
        symbols = [
            {
                "symbol": item["name"],
                "kind": item["kind"],
                "line_start": item["line_start"],
                "confidence": 0.72,
            }
            for group in ("types", "functions")
            for item in parsed.get(group, [])
        ]
        symbols.extend(
            {
                "symbol": item["selector"],
                "kind": "angular_selector",
                "line_start": item["line_start"],
                "confidence": 0.8,
            }
            for item in parsed.get("component_selectors", [])
        )
        return symbols

    def extract_types(self, parsed: dict) -> list[dict]:
        return list(parsed.get("types") or [])

    def extract_semantics(self, parsed: dict) -> list[dict]:
        selector_by_class = {
            item["class_name"]: item["selector"]
            for item in parsed.get("component_selectors", [])
        }
        semantics = []
        for item in parsed.get("types", []):
            semantic_kind = _semantic_kind(item["kind"])
            if item["name"] in selector_by_class:
                semantic_kind = "component"
            semantics.append(
                {
                    "symbol": item["name"],
                    "semantic_kind": semantic_kind,
                    "selector": selector_by_class.get(item["name"]),
                }
            )
        semantics.extend(
            {"symbol": item["name"], "semantic_kind": "function_signature"}
            for item in parsed.get("functions", [])
        )
        return semantics

    def emit_graph_records(self, path: str, content: str) -> dict:
        parsed = self.parse(path, content)
        file_key = path.replace("\\", "/")
        nodes: list[dict] = []
        edges: list[dict] = []
        node_by_symbol: dict[str, str] = {}
        module_node_ids: set[str] = set()
        selector_by_class = {
            item["class_name"]: item["selector"]
            for item in parsed["component_selectors"]
        }

        for item in [*parsed["types"], *parsed["functions"]]:
            kind = item["kind"]
            semantic_kind = "function_signature" if "function" in kind else _semantic_kind(kind)
            if item["name"] in selector_by_class:
                semantic_kind = "component"
            node_id = f"semantic:typescript:{kind}:{file_key}:{item['name']}"
            node_by_symbol[item["name"]] = node_id
            attributes = dict(item)
            if item["name"] in selector_by_class:
                attributes.update({"framework": "angular", "selector": selector_by_class[item["name"]]})
            if item["name"][:1].isupper() and any(tag["name"] for tag in parsed["jsx_tags"]):
                attributes.setdefault("framework_hint", "react_or_jsx")
            nodes.append(self._node(path, node_id, item["name"], semantic_kind, attributes))

        for item in parsed["imports"]:
            module_id, canonical_module_id = self._module_identity(
                path=path,
                module_name=item["module"],
            )
            if module_id not in module_node_ids:
                nodes.append(
                    self._node(
                        path,
                        module_id,
                        item["module"],
                        "module",
                        {
                            **item,
                            **semantic_identity_attributes(
                                canonical_module_id
                            ),
                        },
                    )
                )
                module_node_ids.add(module_id)
            edges.append(
                SemanticEdge(
                    source_id=f"semantic:typescript:file:{file_key}",
                    target_id=module_id,
                    edge_type="imports",
                    attributes={"binding": item["binding"]},
                ).as_record()
            )

        for item in parsed["exports"]:
            module_id, canonical_module_id = self._module_identity(
                path=path,
                module_name=item["module"],
            )
            if module_id not in module_node_ids:
                nodes.append(
                    self._node(
                        path,
                        module_id,
                        item["module"],
                        "module",
                        {
                            **item,
                            **semantic_identity_attributes(
                                canonical_module_id
                            ),
                        },
                    )
                )
                module_node_ids.add(module_id)
            edges.append(
                SemanticEdge(
                    source_id=f"semantic:typescript:file:{file_key}",
                    target_id=module_id,
                    edge_type="exports",
                ).as_record()
            )
        for item in [*parsed["types"], *parsed["functions"]]:
            source_id = node_by_symbol[item["name"]]
            if item.get("exported"):
                edges.append(
                    SemanticEdge(
                        source_id=f"semantic:typescript:file:{file_key}",
                        target_id=source_id,
                        edge_type="exports",
                    ).as_record()
                )
            for base in item.get("extends", []):
                edges.append(
                    SemanticEdge(
                        source_id=source_id,
                        target_id=_type_target(base),
                        edge_type="extends",
                    ).as_record()
                )
            for interface in item.get("implements", []):
                edges.append(
                    SemanticEdge(
                        source_id=source_id,
                        target_id=_type_target(interface),
                        edge_type="implements",
                    ).as_record()
                )

        for tag in parsed["jsx_tags"]:
            edges.append(
                SemanticEdge(
                    source_id=f"semantic:typescript:file:{file_key}",
                    target_id=node_by_symbol.get(tag["name"], f"semantic:typescript:component:{tag['name']}"),
                    edge_type="references",
                    attributes={"syntax": "jsx", "line_start": tag["line_start"]},
                ).as_record()
            )
        return {"nodes": nodes, "edges": edges, "diagnostics": parsed["diagnostics"]}

    def _module_identity(
        self,
        *,
        path: str,
        module_name: str,
    ) -> tuple[str, str]:
        canonical_id = f"semantic:typescript:module:{module_name}"
        return (
            self._symbol_identity.symbol_id(
                language=self.language,
                path=path,
                symbol_kind="module",
                canonical_id=canonical_id,
                local_qualifier=module_name,
            ),
            canonical_id,
        )

    def _declaration(self, content: str, match: re.Match[str]) -> dict:
        tail = str(match.group("tail") or "")
        return {
            "name": match.group("name"),
            "kind": match.group("kind"),
            "line_start": _line(content, match.start()),
            "generics": str(match.group("generics") or "").strip(),
            "extends": _heritage(tail, "extends"),
            "implements": _heritage(tail, "implements"),
            "exported": bool(match.group("export")),
            "abstract": bool(match.group("abstract")),
        }

    def _function(self, content: str, match: re.Match[str], kind: str) -> dict:
        groups = match.groupdict()
        return {
            "name": match.group("name"),
            "kind": kind,
            "line_start": _line(content, match.start()),
            "generics": str(groups.get("generics") or "").strip(),
            "parameters": str(groups.get("params") or "").strip(),
            "return_type": str(groups.get("return") or "").strip(),
            "exported": bool(groups.get("export")),
            "extends": [],
            "implements": [],
        }

    def _node(
        self,
        path: str,
        node_id: str,
        symbol: str,
        semantic_kind: str,
        attributes: dict,
    ) -> dict:
        line = int(attributes.get("line_start") or 1)
        return SemanticNode(
            id=node_id,
            kind="semantic_node",
            semantic_kind=semantic_kind,
            language=self.language,
            symbol=symbol,
            attributes={**attributes, "confidence": 0.72},
            provenance=Provenance(
                file=path,
                language=self.language,
                symbol=symbol,
                line_start=line,
                line_end=line,
                parser=self.parser_strategy,
                confidence=0.72,
            ),
        ).as_record()


def _semantic_kind(kind: str) -> str:
    return {
        "interface": "interface_contract",
        "type": "type_alias",
        "class": "data_record",
        "enum": "data_record",
    }.get(kind, "data_record")


def _type_target(name: str) -> str:
    compact_name = re.sub(r"\s+", "", name)
    return f"semantic:typescript:type:{compact_name}"


def _heritage(tail: str, keyword: str) -> list[str]:
    match = re.search(
        rf"\b{keyword}\s+(.+?)(?=\s+extends\b|\s+implements\b|$)",
        tail,
    )
    if not match:
        return []
    return [value.strip() for value in match.group(1).split(",") if value.strip()]


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _balanced(content: str) -> bool:
    pairs = {"}": "{", "]": "[", ")": "("}
    stack: list[str] = []
    quote = ""
    escaped = False
    for char in content:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "{[(":
            stack.append(char)
        elif char in "}])" and (not stack or stack.pop() != pairs[char]):
            return False
    return not stack and not quote


def _mask_comments(content: str) -> str:
    """Replace comments with spaces while preserving offsets and newlines."""

    result = list(content)
    index = 0
    state = "code"
    quote = ""
    while index < len(content):
        char = content[index]
        nxt = content[index + 1] if index + 1 < len(content) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and nxt == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "code"
            else:
                if char != "\n":
                    result[index] = " "
                index += 1
            continue
        if state == "string":
            if char == "\\":
                index += 2
                continue
            if char == quote:
                state = "code"
            index += 1
            continue
        if char in {"'", '"', "`"}:
            state = "string"
            quote = char
            index += 1
        elif char == "/" and nxt == "/":
            result[index] = result[index + 1] = " "
            index += 2
            state = "line_comment"
        elif char == "/" and nxt == "*":
            result[index] = result[index + 1] = " "
            index += 2
            state = "block_comment"
        else:
            index += 1
    return "".join(result)


def _dedupe(items: list[dict], *, keys: tuple[str, ...]) -> list[dict]:
    seen: set[tuple[object, ...]] = set()
    result = []
    for item in items:
        identity = tuple(item.get(key) for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result
