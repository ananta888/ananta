"""AST-based Python module outline (imports, classes, functions, pytest links)."""

from __future__ import annotations

import ast
import re

from rag_helper.extractors.code_outline_fallback import CodeOutlineFallbackParser
from rag_helper.utils.embedding_text import build_embedding_text, compact_list
from rag_helper.utils.ids import safe_id


class PythonModuleOutlineParser:
    """Parse a Python module with :mod:`ast`; fall back to a regex outline on syntax errors."""

    def __init__(
        self,
        *,
        embedding_text_mode: str = "verbose",
        fallback_parser: CodeOutlineFallbackParser | None = None,
    ) -> None:
        self.embedding_text_mode = embedding_text_mode
        self.fallback_parser = fallback_parser or CodeOutlineFallbackParser(
            embedding_text_mode=embedding_text_mode
        )

    def parse(self, rel_path: str, text: str):
        file_id = f"python_file:{safe_id(rel_path)}"
        try:
            parsed = ast.parse(text)
        except SyntaxError:
            return self.fallback_parser.parse(rel_path, text, language="python", parse_mode="outline_fallback")

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
