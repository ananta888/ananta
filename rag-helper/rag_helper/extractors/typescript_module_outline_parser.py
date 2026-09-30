"""Heuristic TypeScript module outline (imports, symbols, class members)."""

from __future__ import annotations

import re

from rag_helper.extractors.typescript_framework_annotator import TypeScriptFrameworkAnnotator
from rag_helper.extractors.typescript_test_case_collector import TypeScriptTestCaseCollector
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id

TYPESCRIPT_IMPORT_PATTERN = re.compile(
    r"^import\s+(?P<clause>.+?)\s+from\s+[\"'](?P<module>[^\"']+)[\"'];?$"
)

TYPESCRIPT_TOP_LEVEL_PATTERNS = [
    (
        "class",
        re.compile(
            r"^(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
            r"(?:\s+extends\s+(?P<extends>[A-Za-z0-9_<>,.\s]+))?"
            r"(?:\s+implements\s+(?P<implements>[A-Za-z0-9_<>,.\s]+))?\s*\{?"
        ),
    ),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")),
    ("type", re.compile(r"^(?:export\s+)?type\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")),
    ("enum", re.compile(r"^(?:export\s+)?enum\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")),
    (
        "function",
        re.compile(
            r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
        ),
    ),
    ("const", re.compile(r"^(?:export\s+)?const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")),
]

TYPESCRIPT_METHOD_PATTERN = re.compile(
    r"^(?P<modifiers>(?:public|private|protected|static|readonly|async|get|set)\s+)*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?::\s*(?P<return_type>[^{]+?))?\s*(?:\{\s*\}?)?$"
)


class TypeScriptModuleOutlineParser:
    """Line-oriented TypeScript outline; delegates tests and framework roles."""

    def __init__(
        self,
        *,
        embedding_text_mode: str = "verbose",
        test_case_collector: TypeScriptTestCaseCollector | None = None,
        framework_annotator: TypeScriptFrameworkAnnotator | None = None,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.test_case_collector = test_case_collector or TypeScriptTestCaseCollector()
        self.framework_annotator = framework_annotator or TypeScriptFrameworkAnnotator()

    def parse(self, rel_path: str, text: str):
        file_id = f"typescript_file:{safe_id(rel_path)}"
        detail_records = []
        relation_records = []
        symbols: list[dict] = []
        imports: list[str] = []
        pending_decorators: list[dict[str, str]] = []
        active_decorator_lines: list[str] | None = None
        active_decorator_balance = 0
        class_stack: list[dict] = []
        brace_depth = 0

        for index, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            opens = raw_line.count("{")
            closes = raw_line.count("}")

            while class_stack and brace_depth < class_stack[-1]["body_depth"]:
                class_stack.pop()

            if active_decorator_lines is not None:
                active_decorator_lines.append(stripped)
                active_decorator_balance += stripped.count("(") - stripped.count(")")
                if active_decorator_balance <= 0:
                    raw_decorator = " ".join(part for part in active_decorator_lines if part)
                    pending_decorators.append({
                        "name": active_decorator_lines[0].split("(", 1)[0],
                        "raw": raw_decorator,
                    })
                    active_decorator_lines = None
                    active_decorator_balance = 0
                brace_depth += opens - closes
                continue

            if not stripped:
                brace_depth += opens - closes
                continue

            if stripped.startswith("@"):
                if stripped.count("(") > stripped.count(")") and not stripped.rstrip().endswith(")"):
                    active_decorator_lines = [stripped]
                    active_decorator_balance = stripped.count("(") - stripped.count(")")
                else:
                    pending_decorators.append({
                        "name": stripped.split("(", 1)[0],
                        "raw": stripped,
                    })
                brace_depth += opens - closes
                continue

            import_match = TYPESCRIPT_IMPORT_PATTERN.match(stripped)
            if import_match:
                module_name = import_match.group("module")
                imports.append(module_name)
                detail_id = f"typescript_import:{safe_id(rel_path)}:{index}"
                detail_records.append({
                    "kind": "typescript_import",
                    "file": rel_path,
                    "id": detail_id,
                    "parent_id": file_id,
                    "module": module_name,
                    "clause": import_match.group("clause").strip(),
                    "line": index,
                })
                relation_records.append({"from": file_id, "to": detail_id, "type": "imports_module"})
                brace_depth += opens - closes
                continue

            top_level_symbol = None
            if brace_depth == 0:
                top_level_symbol = self._match_typescript_top_level_symbol(stripped, index, pending_decorators)
                if top_level_symbol is not None:
                    symbol_kind = top_level_symbol["kind"]
                    symbol_payload = dict(top_level_symbol)
                    detail_id = f"typescript_{symbol_kind}:{safe_id(rel_path)}:{index}"
                    detail_record = {
                        "file": rel_path,
                        "id": detail_id,
                        "parent_id": file_id,
                        **symbol_payload,
                        "kind": f"typescript_{symbol_kind}",
                    }
                    detail_records.append(detail_record)
                    relation_records.append({"from": file_id, "to": detail_id, "type": "contains_symbol"})
                    symbols.append({
                        "kind": symbol_kind,
                        "name": top_level_symbol["name"],
                        "line": index,
                    })
                    if symbol_kind == "class" and opens > closes:
                        class_stack.append({
                            "id": detail_id,
                            "name": top_level_symbol["name"],
                            "body_depth": brace_depth + opens - closes,
                        })
                    pending_decorators = []
                    brace_depth += opens - closes
                    continue

            if class_stack and brace_depth >= class_stack[-1]["body_depth"]:
                method_info = self._match_typescript_method(stripped, index, pending_decorators)
                if method_info is not None:
                    detail_id = (
                        f"typescript_{method_info['kind']}:{safe_id(rel_path)}:{index}:{safe_id(method_info['name'])}"
                    )
                    detail_records.append({
                        "file": rel_path,
                        "id": detail_id,
                        "parent_id": class_stack[-1]["id"],
                        "class_name": class_stack[-1]["name"],
                        **method_info,
                        "kind": f"typescript_{method_info['kind']}",
                    })
                    relation_records.append({"from": class_stack[-1]["id"], "to": detail_id, "type": "contains_method"})
                    symbols.append({
                        "kind": method_info["kind"],
                        "name": f"{class_stack[-1]['name']}.{method_info['name']}",
                        "line": index,
                    })
                    pending_decorators = []
                    brace_depth += opens - closes
                    continue

            brace_depth += opens - closes

        test_case_count = self.test_case_collector.append_test_records(
            rel_path=rel_path,
            lines=text.splitlines(),
            file_id=file_id,
            details=detail_records,
            relations=relation_records,
        )
        names = [symbol["name"] for symbol in symbols]
        top_level_symbols = [
            record
            for record in detail_records
            if record.get("parent_id") == file_id
            and record["kind"] != "typescript_import"
        ]
        framework_info = self.framework_annotator.annotate(
            rel_path=rel_path,
            text=text,
            imports=imports,
            top_level_symbols=top_level_symbols,
            detail_records=detail_records,
        )
        method_count = sum(
            1
            for record in detail_records
            if record["kind"] in {"typescript_method", "typescript_constructor"}
        )
        index_record = {
            "kind": "typescript_file",
            "file": rel_path,
            "id": file_id,
            "imports": imports[:50],
            "symbols": symbols[:50],
            "frameworks": framework_info["frameworks"],
            "framework_roles": framework_info["framework_roles"],
            "frontend_artifacts": framework_info["frontend_artifacts"][:50],
            "hook_calls": framework_info["hook_calls"][:30],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"TypeScript file {rel_path}. "
                    f"Imports: {', '.join(imports[:20]) or 'none'}. "
                    f"Symbols: {', '.join(names[:30]) or 'none'}. "
                    f"Frameworks: {', '.join(framework_info['frameworks']) or 'none'}. "
                    f"Frontend artifacts: {', '.join(framework_info['frontend_artifacts'][:20]) or 'none'}. "
                    f"Methods {method_count}."
                ),
                (
                    f"TypeScript {rel_path}. "
                    f"Frameworks {compact_list(framework_info['frameworks'], limit=4)}. "
                    f"Imports {compact_list(imports, limit=6)}. "
                    f"Symbols {compact_list(names, limit=6)}."
                ),
            ),
            "summary": {
                "import_count": len(imports),
                "symbol_count": len(symbols),
                "class_count": sum(1 for record in top_level_symbols if record["kind"] == "typescript_class"),
                "function_count": sum(
                    1 for record in top_level_symbols if record["kind"] in {"typescript_function", "typescript_const"}
                ),
                "framework_count": len(framework_info["frameworks"]),
                "angular_artifact_count": framework_info["angular_artifact_count"],
                "react_artifact_count": framework_info["react_artifact_count"],
                "component_count": framework_info["component_count"],
                "hook_count": framework_info["hook_count"],
                "method_count": method_count,
                "test_case_count": test_case_count,
                "parse_mode": "heuristic",
            },
        }
        return [index_record], detail_records, relation_records, {
            "kind": "typescript",
            "file": rel_path,
            "import_count": len(imports),
            "symbol_count": len(symbols),
            "class_count": index_record["summary"]["class_count"],
            "function_count": index_record["summary"]["function_count"],
            "framework_count": index_record["summary"]["framework_count"],
            "component_count": index_record["summary"]["component_count"],
            "hook_count": index_record["summary"]["hook_count"],
            "method_count": index_record["summary"]["method_count"],
            "test_case_count": test_case_count,
            "parse_mode": "heuristic",
        }


    def _match_typescript_top_level_symbol(
        self,
        stripped: str,
        line: int,
        decorators: list[dict[str, str]],
    ) -> dict | None:
        for kind, pattern in TYPESCRIPT_TOP_LEVEL_PATTERNS:
            match = pattern.match(stripped)
            if not match:
                continue
            payload = {
                "kind": kind,
                "name": match.group("name"),
                "line": line,
                "decorators": [item["name"] for item in decorators],
                "decorator_texts": [item["raw"] for item in decorators],
                "framework": None,
                "framework_role": None,
            }
            if kind == "class":
                extends_value = match.groupdict().get("extends")
                implements_value = match.groupdict().get("implements")
                payload["extends"] = extends_value.strip() if extends_value else None
                payload["implements"] = [
                    item.strip()
                    for item in (implements_value or "").split(",")
                    if item.strip()
                ]
            return payload
        return None

    def _match_typescript_method(self, stripped: str, line: int, decorators: list[dict[str, str]]) -> dict | None:
        match = TYPESCRIPT_METHOD_PATTERN.match(stripped)
        if not match:
            return None
        name = match.group("name")
        return {
            "kind": "constructor" if name == "constructor" else "method",
            "name": name,
            "line": line,
            "decorators": [item["name"] for item in decorators],
            "decorator_texts": [item["raw"] for item in decorators],
            "modifiers": [item for item in (match.group("modifiers") or "").split() if item],
            "return_type": (match.group("return_type") or "").strip() or None,
        }
