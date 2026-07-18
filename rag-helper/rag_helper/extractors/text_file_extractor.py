from __future__ import annotations

import ast
import re

from rag_helper.extractors.structured_support import normalize_extraction_records
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


class TextFileExtractor:
    SUPPORTED_EXTENSIONS = {"properties", "yaml", "yml", "sql", "md", "py", "ts", "tsx", "gradle", "kts"}

    def __init__(self, embedding_text_mode: str = "verbose") -> None:
        self.embedding_text_mode = embedding_text_mode

    def parse(self, rel_path: str, text: str):
        ext = rel_path.rsplit(".", 1)[-1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported text extension: {ext}")

        if ext in {"yaml", "yml"}:
            result = self._parse_keyed_file(rel_path, text, kind_prefix="yaml", separator=":")
        elif ext == "properties":
            result = self._parse_keyed_file(rel_path, text, kind_prefix="properties", separator="=")
        elif ext == "md":
            result = self._parse_markdown(rel_path, text)
        elif ext in {"gradle", "kts"} or rel_path.endswith(
            ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
        ):
            result = self._parse_gradle(rel_path, text)
        elif ext == "sql":
            result = self._parse_sql(rel_path, text)
        elif ext in {"py", "ts", "tsx"}:
            result = self._parse_code_outline(
                rel_path,
                text,
                language="python" if ext == "py" else "typescript",
            )
        else:
            result = self._parse_file_only(rel_path, text, kind_prefix=ext)
        index, details, relations, stats = result
        normalize_extraction_records(
            (index, details, relations),
            rel_path=rel_path,
            source_text=text,
            extractor=type(self).__name__,
        )
        return index, details, relations, stats

    def _parse_file_only(self, rel_path: str, text: str, kind_prefix: str):
        file_id = f"{kind_prefix}_file:{safe_id(rel_path)}"
        index_record = {
            "kind": f"{kind_prefix}_file",
            "file": rel_path,
            "id": file_id,
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{kind_prefix.upper()} file {rel_path}. Content length {len(text)} characters.",
                f"{kind_prefix.upper()} file {rel_path}.",
            ),
            "summary": {"char_count": len(text)},
        }
        return [index_record], [], [], {"kind": kind_prefix, "file": rel_path, "record_count": 1}

    def _parse_markdown(self, rel_path: str, text: str):
        file_id = f"md_file:{safe_id(rel_path)}"
        headings = []
        detail_records = []
        relation_records = []
        current_parent_id = file_id
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            if not heading:
                continue
            section_id = f"md_section:{safe_id(rel_path)}:{index}"
            headings.append(heading)
            detail_records.append({
                "kind": "md_section",
                "file": rel_path,
                "id": section_id,
                "parent_id": current_parent_id,
                "heading": heading,
                "level": level,
                "line": index,
            })
            relation_records.append({"from": current_parent_id, "to": section_id, "type": "contains_section"})
            current_parent_id = section_id

        index_record = {
            "kind": "md_file",
            "file": rel_path,
            "id": file_id,
            "heading_count": len(headings),
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"Markdown file {rel_path}. Headings: {', '.join(headings[:20]) or 'none'}.",
                f"Markdown {rel_path}. Headings {compact_list(headings, limit=6)}.",
            ),
            "summary": {"heading_count": len(headings)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": "md",
            "file": rel_path,
            "heading_count": len(headings),
        }

    def _parse_keyed_file(self, rel_path: str, text: str, kind_prefix: str, separator: str):
        file_id = f"{kind_prefix}_file:{safe_id(rel_path)}"
        keys = []
        detail_records = []
        relation_records = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if kind_prefix == "properties" and stripped.startswith(("!", ";")):
                continue
            key = self._extract_key(stripped, separator)
            if not key:
                continue
            detail_id = f"{kind_prefix}_entry:{safe_id(rel_path)}:{index}"
            keys.append(key)
            detail_records.append({
                "kind": f"{kind_prefix}_entry",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "key": key,
                "line": index,
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_entry"})

        index_record = {
            "kind": f"{kind_prefix}_file",
            "file": rel_path,
            "id": file_id,
            "keys": keys[:50],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{kind_prefix.upper()} file {rel_path}. Keys: {', '.join(keys[:30]) or 'none'}.",
                f"{kind_prefix.upper()} {rel_path}. Keys {compact_list(keys, limit=6)}.",
            ),
            "summary": {"entry_count": len(keys)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": kind_prefix,
            "file": rel_path,
            "entry_count": len(keys),
        }

    def _parse_sql(self, rel_path: str, text: str):
        file_id = f"sql_file:{safe_id(rel_path)}"
        statements = [stmt.strip() for stmt in text.split(";") if stmt.strip()]
        detail_records = []
        relation_records = []
        titles = []
        for index, statement in enumerate(statements[:50], start=1):
            title = self._sql_statement_title(statement)
            titles.append(title)
            detail_id = f"sql_statement:{safe_id(rel_path)}:{index}"
            detail_records.append({
                "kind": "sql_statement",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "title": title,
                "statement": statement[:400],
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_statement"})

        index_record = {
            "kind": "sql_file",
            "file": rel_path,
            "id": file_id,
            "statement_count": len(statements),
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"SQL file {rel_path}. Statements: {', '.join(titles[:20]) or 'none'}.",
                f"SQL {rel_path}. Statements {compact_list(titles, limit=6)}.",
            ),
            "summary": {"statement_count": len(statements)},
        }
        return [index_record], detail_records, relation_records, {
            "kind": "sql",
            "file": rel_path,
            "statement_count": len(statements),
        }

    def _parse_gradle(self, rel_path: str, text: str):
        file_id = f"gradle_file:{safe_id(rel_path)}"
        lines = text.splitlines()
        declarations: list[str] = []
        include_entries: list[str] = []
        plugins: list[str] = []
        detail_records = []
        relation_records = []

        for index, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*")):
                continue
            if stripped.startswith("include "):
                include_entries.extend(re.findall(r'["\']([^"\']+)["\']', stripped))
            if stripped.startswith("plugins") or stripped.startswith("pluginManagement"):
                declarations.append(stripped[:120])
            if "id " in stripped:
                plugins.extend(re.findall(r'id\s+[("\']?([^"\')\s]+)', stripped))
            structural_tokens = (
                "dependencies",
                "sourceSets",
                "repositories",
                "include ",
                "project(",
                "plugins",
                "pluginManagement",
            )
            if any(token in stripped for token in structural_tokens):
                detail_id = f"gradle_entry:{safe_id(rel_path)}:{index}"
                detail_records.append({
                    "kind": "gradle_entry",
                    "file": rel_path,
                    "id": detail_id,
                    "parent_id": file_id,
                    "line": index,
                    "content": stripped[:240],
                })
                relation_records.append({"from": file_id, "to": detail_id, "type": "contains_entry"})

        index_record = {
            "kind": "gradle_file",
            "file": rel_path,
            "id": file_id,
            "includes": include_entries[:50],
            "plugins": plugins[:30],
            "declarations": declarations[:30],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"Gradle file {rel_path}. "
                    f"Includes: {', '.join(include_entries[:20]) or 'none'}. "
                    f"Plugins: {', '.join(plugins[:20]) or 'none'}. "
                    f"Declarations: {', '.join(declarations[:10]) or 'none'}."
                ),
                (
                    f"Gradle {rel_path}. "
                    f"Includes {compact_list(include_entries, limit=6)}. "
                    f"Plugins {compact_list(plugins, limit=6)}."
                ),
            ),
            "summary": {
                "include_count": len(include_entries),
                "plugin_count": len(plugins),
                "declaration_count": len(declarations),
            },
        }
        return [index_record], detail_records, relation_records, {
            "kind": "gradle",
            "file": rel_path,
            "include_count": len(include_entries),
            "plugin_count": len(plugins),
            "declaration_count": len(declarations),
        }

    def _parse_code_outline(self, rel_path: str, text: str, language: str):
        if language == "python":
            return self._parse_python_module(rel_path, text)
        return self._parse_typescript_module(rel_path, text)

    def _parse_python_module(self, rel_path: str, text: str):
        file_id = f"python_file:{safe_id(rel_path)}"
        try:
            parsed = ast.parse(text)
        except SyntaxError:
            return self._parse_code_outline_fallback(rel_path, text, language="python", parse_mode="outline_fallback")

        imports: list[str] = []
        classes: list[dict] = []
        functions: list[dict] = []
        detail_records = []
        relation_records = []
        symbols: list[dict] = []
        local_fixtures: dict[str, str] = {}
        is_test_file = self._is_python_test_path(rel_path)

        for node in parsed.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [self._ast_to_text(decorator) for decorator in node.decorator_list]
            if self._is_pytest_fixture(decorators):
                local_fixtures[node.name] = f"python_function:{safe_id(rel_path)}:{node.lineno}"

        for node in parsed.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_name = alias.name
                    imports.append(import_name)
                    detail_id = f"python_import:{safe_id(rel_path)}:{node.lineno}:{safe_id(import_name)}"
                    detail_records.append({
                        "kind": "python_import",
                        "file": rel_path,
                        "id": detail_id,
                        "parent_id": file_id,
                        "module": import_name,
                        "alias": alias.asname,
                        "line": node.lineno,
                    })
                    relation_records.append({"from": file_id, "to": detail_id, "type": "imports_module"})
            elif isinstance(node, ast.ImportFrom):
                module_name = "." * node.level + (node.module or "")
                imports.append(module_name or ".")
                detail_id = f"python_import:{safe_id(rel_path)}:{node.lineno}:{safe_id(module_name or '.')}"
                detail_records.append({
                    "kind": "python_import",
                    "file": rel_path,
                    "id": detail_id,
                    "parent_id": file_id,
                    "module": module_name or ".",
                    "names": [alias.name for alias in node.names],
                    "line": node.lineno,
                })
                relation_records.append({"from": file_id, "to": detail_id, "type": "imports_module"})
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_info = self._python_callable_info(node)
                functions.append(function_info)
                symbols.append(
                    {
                        "kind": "function",
                        "name": function_info["name"],
                        "line": function_info["line"],
                    }
                )
                detail_id = f"python_function:{safe_id(rel_path)}:{node.lineno}"
                is_fixture = self._is_pytest_fixture(function_info["decorators"])
                is_test_case = is_test_file and function_info["name"].startswith("test_")
                detail_records.append({
                    "kind": "python_function",
                    "record_kind": (
                        "fixture"
                        if is_fixture
                        else "test_case"
                        if is_test_case
                        else "python_function"
                    ),
                    "file": rel_path,
                    "id": detail_id,
                    "parent_id": file_id,
                    **function_info,
                })
                relation_records.append({"from": file_id, "to": detail_id, "type": "contains_symbol"})
            elif isinstance(node, ast.ClassDef):
                class_info = self._python_class_info(node)
                classes.append(class_info)
                symbols.append({"kind": "class", "name": class_info["name"], "line": class_info["line"]})
                class_id = f"python_class:{safe_id(rel_path)}:{node.lineno}"
                detail_records.append({
                    "kind": "python_class",
                    "file": rel_path,
                    "id": class_id,
                    "parent_id": file_id,
                    **class_info,
                })
                relation_records.append({"from": file_id, "to": class_id, "type": "contains_symbol"})
                for method in class_info["methods"]:
                    method_id = f"python_method:{safe_id(rel_path)}:{method['line']}:{safe_id(method['name'])}"
                    is_test_case = (
                        is_test_file
                        and method["name"].startswith("test_")
                        and (
                            class_info["name"].startswith("Test")
                            or class_info["name"].endswith(("Test", "Tests", "TestCase"))
                        )
                    )
                    detail_records.append({
                        "kind": "python_method",
                        "record_kind": "test_case" if is_test_case else "python_method",
                        "file": rel_path,
                        "id": method_id,
                        "parent_id": class_id,
                        **method,
                        "class_name": class_info["name"],
                    })
                    relation_records.append({"from": class_id, "to": method_id, "type": "contains_method"})

        test_records = [record for record in detail_records if record.get("record_kind") == "test_case"]
        for test_record in test_records:
            parametrized = self._python_parametrized_names(test_record.get("decorators", []))
            for fixture_name in test_record.get("parameters", []):
                if fixture_name in {"self", "cls"} or fixture_name in parametrized:
                    continue
                target_id = local_fixtures.get(fixture_name)
                relation_records.append(
                    {
                        "kind": "relation",
                        "record_kind": "fixture_relation",
                        "file": rel_path,
                        "id": (
                            f"fixture_relation:"
                            f"{safe_id(rel_path, test_record['id'], fixture_name)}"
                        ),
                        "record_id": (
                            f"fixture_relation:"
                            f"{safe_id(rel_path, test_record['name'], fixture_name)}"
                        ),
                        "from": test_record["id"],
                        "to": target_id or fixture_name,
                        "type": "uses_fixture",
                        "source_id": test_record["id"],
                        "source_kind": test_record["kind"],
                        "source_name": test_record["name"],
                        "relation": "uses_fixture",
                        "target": fixture_name,
                        "target_resolved": target_id,
                        "resolution_status": "resolved" if target_id else "unresolved",
                        "weight": 1,
                        "line": test_record["line"],
                    }
                )

        module_docstring = ast.get_docstring(parsed) or ""
        _doc_prefix = f"{module_docstring[:200]} " if module_docstring else ""
        index_record = {
            "kind": "python_file",
            "file": rel_path,
            "id": file_id,
            "imports": imports[:50],
            "classes": classes[:50],
            "functions": functions[:50],
            "symbols": symbols[:50],
            "module_docstring": module_docstring[:500] if module_docstring else None,
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                (
                    f"{_doc_prefix}"
                    f"Python file {rel_path}. "
                    f"Imports: {', '.join(imports[:20]) or 'none'}. "
                    f"Classes: {', '.join(item['name'] for item in classes[:20]) or 'none'}. "
                    f"Functions: {', '.join(item['name'] for item in functions[:20]) or 'none'}. "
                    f"Methods {sum(len(item['methods']) for item in classes)}."
                ),
                (
                    f"{_doc_prefix}"
                    f"Python {rel_path}. "
                    f"Classes {compact_list([item['name'] for item in classes], limit=6)}. "
                    f"Functions {compact_list([item['name'] for item in functions], limit=6)}."
                ),
            ),
            "summary": {
                "import_count": len(imports),
                "class_count": len(classes),
                "function_count": len(functions),
                "method_count": sum(len(item["methods"]) for item in classes),
                "symbol_count": len(symbols),
                "parse_mode": "ast",
            },
        }
        return [index_record], detail_records, relation_records, {
            "kind": "python",
            "file": rel_path,
            "import_count": len(imports),
            "class_count": len(classes),
            "function_count": len(functions),
            "method_count": sum(len(item["methods"]) for item in classes),
            "symbol_count": len(symbols),
            "parse_mode": "ast",
        }

    def _parse_typescript_module(self, rel_path: str, text: str):
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

        test_case_count = self._append_typescript_test_records(
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
        framework_info = self._annotate_typescript_frameworks(
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

    def _append_typescript_test_records(
        self,
        *,
        rel_path: str,
        lines: list[str],
        file_id: str,
        details: list[dict],
        relations: list[dict],
    ) -> int:
        if not self._is_typescript_test_path(rel_path):
            return 0
        count = 0
        for index, raw in enumerate(lines):
            match = TYPESCRIPT_TEST_CASE_PATTERN.match(raw)
            if match is None:
                continue
            count += 1
            line_start = index + 1
            line_end = self._typescript_call_end_line(lines, index)
            test_name = match.group("name").strip()
            test_id = (
                f"typescript_call:"
                f"{safe_id(rel_path, match.group('call_kind'), test_name, str(count))}"
            )
            record = {
                "kind": "typescript_call",
                "record_kind": "test_case",
                "file": rel_path,
                "id": test_id,
                "parent_id": file_id,
                "name": test_name,
                "call_kind": match.group("call_kind"),
                "line": line_start,
                "end_line": line_end,
            }
            details.append(record)
            relations.append(
                {
                    "kind": "relation",
                    "file": rel_path,
                    "id": f"relation:{safe_id(rel_path, file_id, 'contains_test_case', test_name)}",
                    "from": file_id,
                    "to": test_id,
                    "type": "contains_test_case",
                    "source_id": file_id,
                    "source_kind": "typescript_file",
                    "source_name": rel_path,
                    "relation": "contains_test_case",
                    "target": test_name,
                    "target_resolved": test_id,
                    "resolution_status": "resolved",
                    "weight": 1,
                    "line": line_start,
                }
            )

            statement = "\n".join(lines[index:line_end])
            fixture_match = TYPESCRIPT_FIXTURE_ARGUMENT_PATTERN.search(statement)
            if fixture_match is None:
                continue
            for fixture_name in self._typescript_fixture_names(fixture_match.group("fixtures")):
                relations.append(
                    {
                        "kind": "relation",
                        "record_kind": "fixture_relation",
                        "file": rel_path,
                        "id": f"fixture_relation:{safe_id(rel_path, test_id, fixture_name)}",
                        "record_id": (
                            f"fixture_relation:{safe_id(rel_path, test_name, fixture_name)}"
                        ),
                        "from": test_id,
                        "to": fixture_name,
                        "type": "uses_fixture",
                        "source_id": test_id,
                        "source_kind": "typescript_call",
                        "source_name": test_name,
                        "relation": "uses_fixture",
                        "target": fixture_name,
                        "target_resolved": None,
                        "resolution_status": "unresolved",
                        "weight": 1,
                        "line": line_start,
                    }
                )
        return count

    @staticmethod
    def _is_typescript_test_path(rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/").lower()
        basename = normalized.rsplit("/", 1)[-1]
        return (
            basename.endswith((".spec.ts", ".spec.tsx", ".test.ts", ".test.tsx"))
            or "/tests/" in f"/{normalized}"
        )

    @staticmethod
    def _typescript_call_end_line(lines: list[str], start_index: int) -> int:
        balance = 0
        seen_open = False
        for index in range(start_index, min(len(lines), start_index + 200)):
            raw = lines[index]
            balance += raw.count("(") - raw.count(")")
            seen_open = seen_open or "(" in raw
            if seen_open and balance <= 0:
                return index + 1
        return start_index + 1

    @staticmethod
    def _typescript_fixture_names(raw: str) -> list[str]:
        fixtures: list[str] = []
        for item in raw.split(","):
            candidate = item.strip().split(":", 1)[0].split("=", 1)[0].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", candidate):
                fixtures.append(candidate)
        return list(dict.fromkeys(fixtures))

    def _parse_code_outline_fallback(self, rel_path: str, text: str, language: str, parse_mode: str):
        file_id = f"{language}_file:{safe_id(rel_path)}"
        symbols = self._extract_symbols(text, language)
        detail_records = []
        relation_records = []
        for index, symbol in enumerate(symbols[:100], start=1):
            detail_id = f"{language}_symbol:{safe_id(rel_path)}:{index}"
            detail_records.append({
                "kind": f"{language}_symbol",
                "file": rel_path,
                "id": detail_id,
                "parent_id": file_id,
                "symbol_kind": symbol["kind"],
                "name": symbol["name"],
                "line": symbol["line"],
            })
            relation_records.append({"from": file_id, "to": detail_id, "type": "contains_symbol"})

        names = [symbol["name"] for symbol in symbols]
        index_record = {
            "kind": f"{language}_file",
            "file": rel_path,
            "id": file_id,
            "symbols": symbols[:50],
            "embedding_text": build_embedding_text(
                self.embedding_text_mode,
                f"{language.title()} file {rel_path}. Symbols: {', '.join(names[:30]) or 'none'}.",
                f"{language.title()} {rel_path}. Symbols {compact_list(names, limit=6)}.",
            ),
            "summary": {"symbol_count": len(symbols), "parse_mode": parse_mode},
        }
        return [index_record], detail_records, relation_records, {
            "kind": language,
            "file": rel_path,
            "symbol_count": len(symbols),
            "parse_mode": parse_mode,
        }

    def _extract_key(self, line: str, separator: str) -> str | None:
        if separator in line:
            return line.split(separator, 1)[0].strip()
        if separator == ":" and ":" in line:
            return line.split(":", 1)[0].strip()
        return None

    def _sql_statement_title(self, statement: str) -> str:
        compact = re.sub(r"\s+", " ", statement).strip()
        words = compact.split(" ")
        return " ".join(words[:6])

    def _extract_symbols(self, text: str, language: str) -> list[dict]:
        patterns = PYTHON_SYMBOL_PATTERNS if language == "python" else TYPESCRIPT_SYMBOL_PATTERNS
        symbols: list[dict] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            for kind, pattern in patterns:
                match = pattern.match(stripped)
                if not match:
                    continue
                symbols.append({"kind": kind, "name": match.group(1), "line": index})
                break
        return symbols

    def _python_class_info(self, node: ast.ClassDef) -> dict:
        methods = [
            self._python_callable_info(child)
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return {
            "name": node.name,
            "line": node.lineno,
            "bases": [self._ast_to_text(base) for base in node.bases],
            "decorators": [self._ast_to_text(decorator) for decorator in node.decorator_list],
            "methods": methods,
        }

    def _python_callable_info(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
        return {
            "name": node.name,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "async": isinstance(node, ast.AsyncFunctionDef),
            "decorators": [self._ast_to_text(decorator) for decorator in node.decorator_list],
            "parameters": [
                argument.arg
                for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            ],
        }

    @staticmethod
    def _is_python_test_path(rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/").lower()
        basename = normalized.rsplit("/", 1)[-1]
        return basename.startswith("test_") or basename.endswith("_test.py") or "/tests/" in f"/{normalized}"

    @staticmethod
    def _is_pytest_fixture(decorators: list[str]) -> bool:
        return any(
            decorator == "fixture"
            or decorator.startswith("fixture(")
            or decorator == "pytest.fixture"
            or decorator.startswith("pytest.fixture(")
            for decorator in decorators
        )

    @staticmethod
    def _python_parametrized_names(decorators: list[str]) -> set[str]:
        result: set[str] = set()
        for decorator in decorators:
            if "parametrize" not in decorator:
                continue
            match = re.search(r"parametrize\(\s*['\"]([^'\"]+)['\"]", decorator)
            if match:
                result.update(item.strip() for item in match.group(1).split(",") if item.strip())
        return result

    def _ast_to_text(self, node: ast.AST) -> str:
        if hasattr(ast, "unparse"):
            return ast.unparse(node)
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._ast_to_text(node.value)}.{node.attr}"
        return node.__class__.__name__

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

    def _annotate_typescript_frameworks(
        self,
        *,
        rel_path: str,
        text: str,
        imports: list[str],
        top_level_symbols: list[dict],
        detail_records: list[dict],
    ) -> dict[str, object]:
        has_angular_import = any(module.startswith("@angular/") for module in imports)
        has_react_import = any(
            module in {"react", "react-dom", "react/jsx-runtime"}
            or module.startswith("react/")
            for module in imports
        )
        has_jsx = rel_path.endswith(".tsx") or bool(JSX_TAG_PATTERN.search(text))
        hook_calls = sorted({match.group(1) for match in REACT_HOOK_CALL_PATTERN.finditer(text)})
        frameworks: set[str] = set()
        framework_roles: list[str] = []
        frontend_artifacts: list[str] = []
        symbol_roles: dict[str, tuple[str | None, str | None]] = {}

        for record in top_level_symbols:
            framework, framework_role = self._classify_typescript_framework_symbol(
                record=record,
                rel_path=rel_path,
                has_angular_import=has_angular_import,
                has_react_import=has_react_import,
                has_jsx=has_jsx,
            )
            if framework is not None:
                record["framework"] = framework
                frameworks.add(framework)
            if framework_role is not None:
                record["framework_role"] = framework_role
                framework_roles.append(framework_role)
                frontend_artifacts.append(record["name"])
            if framework == "angular":
                angular_metadata = self._extract_angular_metadata(record.get("decorator_texts", []))
                if angular_metadata:
                    record["angular_metadata"] = angular_metadata
            if framework == "react":
                record["hook_calls"] = [call for call in hook_calls if call != record.get("name")]
                record["jsx_usage"] = has_jsx
            symbol_roles[str(record.get("id"))] = (framework, framework_role)

        for record in detail_records:
            parent_id = str(record.get("parent_id") or "")
            framework, framework_role = symbol_roles.get(parent_id, (None, None))
            if record.get("kind") in {"typescript_method", "typescript_constructor"} and framework is not None:
                record["framework"] = framework
                method_role = self._classify_typescript_method_role(
                    name=str(record.get("name") or ""),
                    framework=framework,
                    parent_role=framework_role,
                )
                if method_role is not None:
                    record["framework_role"] = method_role

        ordered_frameworks = sorted(frameworks)
        ordered_roles = list(dict.fromkeys(role for role in framework_roles if role))
        angular_artifact_count = sum(1 for record in top_level_symbols if record.get("framework") == "angular")
        react_artifact_count = sum(1 for record in top_level_symbols if record.get("framework") == "react")
        component_count = sum(
            1
            for record in top_level_symbols
            if record.get("framework_role")
            in {"angular_component", "react_component"}
        )
        hook_count = sum(1 for record in top_level_symbols if record.get("framework_role") == "react_hook")
        return {
            "frameworks": ordered_frameworks,
            "framework_roles": ordered_roles,
            "frontend_artifacts": frontend_artifacts,
            "hook_calls": hook_calls,
            "angular_artifact_count": angular_artifact_count,
            "react_artifact_count": react_artifact_count,
            "component_count": component_count,
            "hook_count": hook_count,
        }

    def _classify_typescript_framework_symbol(
        self,
        *,
        record: dict,
        rel_path: str,
        has_angular_import: bool,
        has_react_import: bool,
        has_jsx: bool,
    ) -> tuple[str | None, str | None]:
        decorators = set(record.get("decorators", []) or [])
        name = str(record.get("name") or "")
        kind = str(record.get("kind") or "")
        extends_value = str(record.get("extends") or "")

        angular_roles = {
            "@Component": "angular_component",
            "@Injectable": "angular_service",
            "@Directive": "angular_directive",
            "@Pipe": "angular_pipe",
            "@NgModule": "angular_module",
        }
        for decorator_name, framework_role in angular_roles.items():
            if decorator_name in decorators:
                return "angular", framework_role
        if (
            has_angular_import
            and kind == "typescript_class"
            and name.endswith(("Component", "Service", "Directive", "Pipe", "Module"))
        ):
            suffix_map = {
                "Component": "angular_component",
                "Service": "angular_service",
                "Directive": "angular_directive",
                "Pipe": "angular_pipe",
                "Module": "angular_module",
            }
            for suffix, framework_role in suffix_map.items():
                if name.endswith(suffix):
                    return "angular", framework_role

        if kind == "typescript_class" and (
            "React.Component" in extends_value
            or extends_value in {"Component", "PureComponent"}
        ):
            return "react", "react_component"

        if kind in {"typescript_function", "typescript_const"} and REACT_HOOK_NAME_PATTERN.match(name):
            if has_react_import or rel_path.endswith((".ts", ".tsx")):
                return "react", "react_hook"

        if kind in {"typescript_function", "typescript_const"} and REACT_COMPONENT_NAME_PATTERN.match(name):
            if has_jsx or has_react_import or rel_path.endswith(".tsx"):
                return "react", "react_component"

        return None, None

    def _classify_typescript_method_role(self, *, name: str, framework: str, parent_role: str | None) -> str | None:
        if framework == "angular" and name in ANGULAR_LIFECYCLE_METHODS:
            return "angular_lifecycle"
        if framework == "react" and name in REACT_CLASS_LIFECYCLE_METHODS:
            return "react_lifecycle"
        if framework == "react" and name == "render" and parent_role == "react_component":
            return "react_render"
        return None

    def _extract_angular_metadata(self, decorator_texts: list[str]) -> dict[str, object]:
        metadata: dict[str, object] = {}
        for decorator_text in decorator_texts:
            if not decorator_text.startswith("@Component"):
                continue
            selector_match = ANGULAR_SELECTOR_PATTERN.search(decorator_text)
            template_match = ANGULAR_TEMPLATE_URL_PATTERN.search(decorator_text)
            standalone_match = ANGULAR_STANDALONE_PATTERN.search(decorator_text)
            imports_match = ANGULAR_IMPORTS_PATTERN.search(decorator_text)
            if selector_match:
                metadata["selector"] = selector_match.group(1)
            if template_match:
                metadata["template_url"] = template_match.group(1)
            if standalone_match:
                metadata["standalone"] = standalone_match.group(1) == "true"
            if imports_match:
                metadata["imports"] = [
                    item.strip()
                    for item in imports_match.group(1).split(",")
                    if item.strip()
                ][:20]
        return metadata


PYTHON_SYMBOL_PATTERNS = [
    ("class", re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("function", re.compile(r"(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
]

TYPESCRIPT_SYMBOL_PATTERNS = [
    ("class", re.compile(r"(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("interface", re.compile(r"(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("type", re.compile(r"(?:export\s+)?type\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("enum", re.compile(r"(?:export\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)\b")),
    ("function", re.compile(r"(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
    ("const", re.compile(r"(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")),
]

TYPESCRIPT_IMPORT_PATTERN = re.compile(
    r"^import\s+(?P<clause>.+?)\s+from\s+[\"'](?P<module>[^\"']+)[\"'];?$"
)
TYPESCRIPT_TEST_CASE_PATTERN = re.compile(
    r"^\s*(?P<call_kind>it|test)(?:\.(?:only|skip|todo))?\s*\(\s*"
    r"(?P<quote>['\"])(?P<name>.*?)(?P=quote)\s*,"
)
TYPESCRIPT_FIXTURE_ARGUMENT_PATTERN = re.compile(
    r"(?:async\s*)?\(\s*\{(?P<fixtures>[^}]+)\}\s*\)\s*=>"
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

REACT_COMPONENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
REACT_HOOK_NAME_PATTERN = re.compile(r"^use[A-Z][A-Za-z0-9_]*$")
REACT_HOOK_CALL_PATTERN = re.compile(r"\b(use[A-Z][A-Za-z0-9_]*)\s*\(")
JSX_TAG_PATTERN = re.compile(r"<[A-Za-z][^>]*>")

ANGULAR_SELECTOR_PATTERN = re.compile(r"selector\s*:\s*['\"]([^'\"]+)['\"]")
ANGULAR_TEMPLATE_URL_PATTERN = re.compile(r"templateUrl\s*:\s*['\"]([^'\"]+)['\"]")
ANGULAR_STANDALONE_PATTERN = re.compile(r"standalone\s*:\s*(true|false)")
ANGULAR_IMPORTS_PATTERN = re.compile(r"imports\s*:\s*\[([^\]]*)\]")

ANGULAR_LIFECYCLE_METHODS = {
    "ngOnInit",
    "ngOnDestroy",
    "ngOnChanges",
    "ngDoCheck",
    "ngAfterViewInit",
    "ngAfterViewChecked",
    "ngAfterContentInit",
    "ngAfterContentChecked",
}

REACT_CLASS_LIFECYCLE_METHODS = {
    "componentDidMount",
    "componentDidUpdate",
    "componentWillUnmount",
    "shouldComponentUpdate",
    "getDerivedStateFromProps",
    "getSnapshotBeforeUpdate",
    "render",
}
