from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.codecompass.semantic_translation.models import Provenance, SemanticEdge, SemanticNode, diagnostic
from agent.codecompass.semantic_translation.python_type_model import (
    TypeAnnotation,
    infer_type_from_default,
    parse_python_type,
)

_DYNAMIC_IMPORT_RE = None  # lazy, detected via ast visitor


class PythonSemanticAdapter:
    """Python AST-based semantic adapter. Uses stdlib `ast` — no external deps."""

    language = "python"
    supported_extensions = (".py",)
    parser_strategy = "ast-python-v1"
    known_limits = (
        "method bodies are not fully analysed",
        "nested functions and lambdas are marked needs_review",
        "dynamic imports produce diagnostic records",
        "complex MRO and metaclass patterns require review",
        "unannotated code produces unknown type confidence",
    )

    def detect(self, path: str, content: str) -> bool:
        return Path(path).suffix == ".py"

    def parse(self, path: str, content: str) -> dict:
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            return {
                "path": path,
                "content": content,
                "types": [],
                "functions": [],
                "imports": [],
                "diagnostics": [diagnostic("python_syntax_error", str(exc), path=path)],
            }
        except Exception as exc:
            return {
                "path": path,
                "content": content,
                "types": [],
                "functions": [],
                "imports": [],
                "diagnostics": [diagnostic("python_parse_error", str(exc), path=path)],
            }
        visitor = _PythonExtractor(path, content)
        visitor.visit(tree)
        return {
            "path": path,
            "content": content,
            "types": visitor.types,
            "functions": visitor.functions,
            "imports": visitor.imports,
            "diagnostics": visitor.diagnostics,
        }

    def extract_symbols(self, parsed: dict) -> list[dict]:
        symbols = []
        for item in parsed.get("types") or []:
            symbols.append({"symbol": item["name"], "kind": item["kind"], "line_start": item["line_start"]})
            for field in item.get("fields") or []:
                symbols.append({"symbol": f"{item['name']}.{field['name']}", "kind": "field", "line_start": field.get("line_start", item["line_start"])})
            for method in item.get("methods") or []:
                symbols.append({"symbol": f"{item['name']}.{method['name']}", "kind": "method", "line_start": method["line_start"]})
        for fn in parsed.get("functions") or []:
            symbols.append({"symbol": fn["name"], "kind": "function", "line_start": fn["line_start"]})
        return symbols

    def extract_types(self, parsed: dict) -> list[dict]:
        return list(parsed.get("types") or [])

    def extract_semantics(self, parsed: dict) -> list[dict]:
        semantics = []
        for item in parsed.get("types") or []:
            kind_map = {"dataclass": "data_record", "frozen_dataclass": "data_record", "enum": "data_record", "typed_dict": "data_record", "class": "data_record"}
            semantics.append({"symbol": item["name"], "semantic_kind": kind_map.get(item["kind"], "data_record")})
        for fn in parsed.get("functions") or []:
            semantics.append({"symbol": fn["name"], "semantic_kind": "function_signature"})
        return semantics

    def emit_graph_records(self, path: str, content: str) -> dict:
        parsed = self.parse(path, content)
        nodes: list[dict] = []
        edges: list[dict] = []

        for item in parsed.get("types") or []:
            type_id = f"semantic:python:{item['kind']}:{item['name']}"
            semantic_kind = "data_record" if item["kind"] in {"dataclass", "frozen_dataclass", "typed_dict", "class"} else "data_record"
            if item["kind"] == "enum":
                semantic_kind = "data_record"
            type_node = SemanticNode(
                id=type_id,
                kind="semantic_node",
                semantic_kind=semantic_kind,
                language="python",
                symbol=item["name"],
                attributes=item,
                provenance=Provenance(
                    file=path,
                    language="python",
                    symbol=item["name"],
                    line_start=item["line_start"],
                    line_end=item["line_end"],
                    parser=self.parser_strategy,
                    confidence=0.85,
                ),
            ).as_record()
            nodes.append(type_node)

            for field in item.get("fields") or []:
                field_id = f"{type_id}:field:{field['name']}"
                null_kind = "nullable_value" if field.get("type_annotation", {}).get("is_optional") else "property"
                nodes.append(SemanticNode(
                    id=field_id,
                    kind="semantic_node",
                    semantic_kind=null_kind,
                    language="python",
                    symbol=f"{item['name']}.{field['name']}",
                    attributes=field,
                    provenance=Provenance(file=path, language="python", symbol=f"{item['name']}.{field['name']}", line_start=field.get("line_start", item["line_start"]), line_end=field.get("line_start", item["line_start"]), parser=self.parser_strategy, confidence=0.82),
                ).as_record())
                edges.append(SemanticEdge(source_id=type_id, target_id=field_id, edge_type="declares").as_record())

            for value in item.get("enum_values") or []:
                value_id = f"{type_id}:enum:{value}"
                nodes.append(SemanticNode(
                    id=value_id,
                    kind="semantic_node",
                    semantic_kind="enum_value",
                    language="python",
                    symbol=f"{item['name']}.{value}",
                    attributes={"name": value},
                    provenance=Provenance(file=path, language="python", symbol=f"{item['name']}.{value}", line_start=item["line_start"], line_end=item["line_end"], parser=self.parser_strategy, confidence=0.9),
                ).as_record())
                edges.append(SemanticEdge(source_id=type_id, target_id=value_id, edge_type="declares").as_record())

            for method in item.get("methods") or []:
                method_id = f"{type_id}:method:{method['name']}"
                nodes.append(SemanticNode(
                    id=method_id,
                    kind="semantic_node",
                    semantic_kind="function_signature",
                    language="python",
                    symbol=f"{item['name']}.{method['name']}",
                    attributes=method,
                    provenance=Provenance(file=path, language="python", symbol=f"{item['name']}.{method['name']}", line_start=method["line_start"], line_end=method["line_start"], parser=self.parser_strategy, confidence=0.8),
                ).as_record())
                edges.append(SemanticEdge(source_id=type_id, target_id=method_id, edge_type="declares").as_record())

        for fn in parsed.get("functions") or []:
            fn_id = f"semantic:python:function:{fn['name']}"
            nodes.append(SemanticNode(
                id=fn_id,
                kind="semantic_node",
                semantic_kind="function_signature",
                language="python",
                symbol=fn["name"],
                attributes=fn,
                provenance=Provenance(file=path, language="python", symbol=fn["name"], line_start=fn["line_start"], line_end=fn["line_end"], parser=self.parser_strategy, confidence=0.83),
            ).as_record())

        return {"nodes": nodes, "edges": edges, "diagnostics": list(parsed.get("diagnostics") or [])}


class _PythonExtractor(ast.NodeVisitor):
    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.lines = content.splitlines()
        self.types: list[dict] = []
        self.functions: list[dict] = []
        self.imports: list[dict] = []
        self.diagnostics: list[dict] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({"name": alias.name, "alias": alias.asname, "line_start": node.lineno, "kind": "import"})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level > 0:
            kind = "relative_import"
        else:
            kind = "import_from"
        # Detect __future__ imports early
        for alias in node.names:
            if alias.name == "*":
                self.diagnostics.append(diagnostic("dynamic_import", f"star import from {module}", path=self.path, line=node.lineno))
            self.imports.append({"name": f"{module}.{alias.name}", "alias": alias.asname, "line_start": node.lineno, "kind": kind, "module": module})

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        decorators = [ast.unparse(d) for d in node.decorator_list]
        bases = [ast.unparse(b) for b in node.bases]
        kind = _classify_class_kind(node, decorators, bases)
        line_start = node.lineno
        line_end = node.end_lineno or line_start

        item: dict[str, Any] = {
            "name": node.name,
            "kind": kind,
            "line_start": line_start,
            "line_end": line_end,
            "decorators": decorators,
            "bases": bases,
            "fields": [],
            "enum_values": [],
            "methods": [],
            "unsupported": [],
            "warnings": [],
        }

        if kind in ("dataclass", "frozen_dataclass"):
            item["fields"] = _extract_dataclass_fields(node)
        elif kind == "typed_dict":
            item["fields"] = _extract_typed_dict_fields(node)
        elif kind == "enum":
            item["enum_values"] = _extract_enum_values(node)
        elif kind == "class":
            item["fields"] = _extract_class_fields(node)
            inheritance = [b for b in bases if b not in ("object", "ABC")]
            if len(inheritance) > 1:
                item["warnings"].append("multiple_inheritance_requires_review")
                item["unsupported"].append({"code": "unsupported_construct", "reason": "multiple_inheritance", "path": self.path})
            for decorator in decorators:
                if "property" in decorator:
                    item["warnings"].append("property_decorator_detected")

        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef) and child is not node:
                method = _extract_function_info(child)
                method["is_method"] = True
                method["is_property"] = any("property" in d for d in [ast.unparse(d) for d in child.decorator_list])
                method["is_classmethod"] = any("classmethod" in d for d in [ast.unparse(d) for d in child.decorator_list])
                method["is_staticmethod"] = any("staticmethod" in d for d in [ast.unparse(d) for d in child.decorator_list])
                # Check for dynamic attribute injection outside __init__
                if child.name != "__init__":
                    for stmt in ast.walk(child):
                        if isinstance(stmt, ast.Assign):
                            for t in stmt.targets:
                                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                                    if "dynamic_attribute_injection" not in item["warnings"]:
                                        item["warnings"].append("dynamic_attribute_injection_outside_init")
                                        item["unsupported"].append({"code": "dynamic_attribute_injection", "reason": f"self.{t.attr} set outside __init__", "path": self.path})
                item_methods = item.setdefault("methods", [])
                if not any(m["name"] == child.name for m in item_methods):
                    item_methods.append(method)

        self.types.append(item)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Only capture module-level functions (not inside classes)
        fn = _extract_function_info(node)
        self.functions.append(fn)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        fn = _extract_function_info(node)
        fn["is_async"] = True
        self.functions.append(fn)


def _classify_class_kind(node: ast.ClassDef, decorators: list[str], bases: list[str]) -> str:
    for d in decorators:
        if "dataclass" in d:
            frozen = "frozen=True" in d
            return "frozen_dataclass" if frozen else "dataclass"
    for b in bases:
        bare = b.split(".")[-1]
        if bare in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"):
            return "enum"
        if bare in ("TypedDict",):
            return "typed_dict"
    return "class"


def _extract_dataclass_fields(node: ast.ClassDef) -> list[dict]:
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
            ann = parse_python_type(stmt.annotation, source="annotation")
            default_ann: TypeAnnotation | None = None
            default_str: str | None = None
            has_default = False
            if stmt.value is not None:
                has_default = True
                raw_default = ast.unparse(stmt.value)
                # Detect field() with default_factory
                if isinstance(stmt.value, ast.Call) and ast.unparse(stmt.value.func) in ("field", "dataclasses.field"):
                    for kw in stmt.value.keywords:
                        if kw.arg == "default":
                            default_str = ast.unparse(kw.value)
                            default_ann = infer_type_from_default(kw.value)
                        elif kw.arg == "default_factory":
                            default_str = f"factory:{ast.unparse(kw.value)}"
                            default_ann = TypeAnnotation(raw="factory", confidence="dynamic", source="default_factory", warnings=("default_factory_requires_review",))
                else:
                    default_str = raw_default
                    default_ann = infer_type_from_default(stmt.value)

            warnings = []
            if ann.confidence == "unknown":
                warnings.append("no_type_annotation")
            if default_ann and default_ann.confidence == "dynamic":
                warnings.extend(list(default_ann.warnings))

            fields.append({
                "name": name,
                "type": ann.raw,
                "type_annotation": ann.as_dict(),
                "has_default": has_default,
                "default": default_str,
                "line_start": stmt.lineno,
                "warnings": warnings,
            })
    return fields


def _extract_typed_dict_fields(node: ast.ClassDef) -> list[dict]:
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann = parse_python_type(stmt.annotation, source="annotation")
            fields.append({
                "name": stmt.target.id,
                "type": ann.raw,
                "type_annotation": ann.as_dict(),
                "has_default": stmt.value is not None,
                "default": ast.unparse(stmt.value) if stmt.value else None,
                "line_start": stmt.lineno,
                "warnings": ["no_type_annotation"] if ann.confidence == "unknown" else [],
            })
    return fields


def _extract_enum_values(node: ast.ClassDef) -> list[str]:
    values = []
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    values.append(t.id)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            values.append(stmt.target.id)
    return values


def _extract_class_fields(node: ast.ClassDef) -> list[dict]:
    fields = []
    for stmt in node.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "__init__":
            for s in ast.walk(stmt):
                if isinstance(s, ast.Assign):
                    for t in s.targets:
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                            fields.append({
                                "name": t.attr,
                                "type": "",
                                "type_annotation": TypeAnnotation(raw="", confidence="unknown", source="inferred", warnings=("instance_field_no_annotation",)).as_dict(),
                                "has_default": True,
                                "default": ast.unparse(s.value) if s.value else None,
                                "line_start": s.lineno,
                                "warnings": ["instance_field_no_annotation"],
                            })
                elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Attribute) and isinstance(s.target.value, ast.Name) and s.target.value.id == "self":
                    ann = parse_python_type(s.annotation, source="annotation")
                    fields.append({
                        "name": s.target.attr,
                        "type": ann.raw,
                        "type_annotation": ann.as_dict(),
                        "has_default": s.value is not None,
                        "default": ast.unparse(s.value) if s.value else None,
                        "line_start": s.lineno,
                        "warnings": [],
                    })
    seen = set()
    deduped = []
    for f in fields:
        if f["name"] not in seen:
            seen.add(f["name"])
            deduped.append(f)
    return deduped


def _extract_function_info(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    decorators = [ast.unparse(d) for d in node.decorator_list]
    return_ann = parse_python_type(node.returns, source="return_annotation")
    params = _extract_parameters(node.args)
    warnings = []
    has_args = node.args.vararg is not None
    has_kwargs = node.args.kwarg is not None
    if has_args or has_kwargs:
        warnings.append("varargs_kwargs_block_auto_transform")

    # Nested function / lambda detection
    has_nested = False
    for child in ast.walk(node):
        if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            has_nested = True
            break
    if has_nested:
        warnings.append("nested_function_or_lambda_needs_review")

    # Dynamic import detection in body
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn_name = ast.unparse(child.func)
            if fn_name == "__import__" or (hasattr(child.func, "id") and child.func.id == "__import__"):
                warnings.append("dynamic_import_detected")
                break
            if fn_name in ("importlib.import_module", "import_module"):
                warnings.append("dynamic_import_detected")
                break

    return {
        "name": node.name,
        "line_start": node.lineno,
        "line_end": node.end_lineno or node.lineno,
        "decorators": decorators,
        "parameters": params,
        "return_type": return_ann.raw,
        "return_type_annotation": return_ann.as_dict(),
        "has_varargs": has_args,
        "has_kwargs": has_kwargs,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "is_method": False,
        "warnings": warnings,
    }


def _extract_parameters(args: ast.arguments) -> list[dict]:
    params: list[dict] = []
    n_args = len(args.args)
    n_defaults = len(args.defaults)
    defaults_offset = n_args - n_defaults

    for i, arg in enumerate(args.args):
        ann = parse_python_type(arg.annotation, source="parameter_annotation")
        default_idx = i - defaults_offset
        has_default = default_idx >= 0
        default_node = args.defaults[default_idx] if has_default else None
        if has_default and ann.confidence == "unknown" and default_node is not None:
            default_ann = infer_type_from_default(default_node)
            if default_ann.confidence != "unknown":
                ann = TypeAnnotation(
                    raw=default_ann.raw,
                    confidence="inferred_from_default",
                    none_model=default_ann.none_model,
                    is_optional=default_ann.is_optional,
                    source="inferred_from_default",
                    warnings=default_ann.warnings,
                )
        is_self = arg.arg == "self" and i == 0
        params.append({
            "name": arg.arg,
            "kind": "self" if is_self else "positional",
            "type": ann.raw,
            "type_annotation": ann.as_dict(),
            "has_default": has_default,
            "default": ast.unparse(default_node) if default_node else None,
        })

    for arg in args.kwonlyargs:
        ann = parse_python_type(arg.annotation, source="parameter_annotation")
        params.append({"name": arg.arg, "kind": "keyword_only", "type": ann.raw, "type_annotation": ann.as_dict(), "has_default": False, "default": None})

    if args.vararg:
        ann = parse_python_type(args.vararg.annotation, source="parameter_annotation")
        params.append({"name": f"*{args.vararg.arg}", "kind": "varargs", "type": ann.raw, "type_annotation": ann.as_dict(), "has_default": False, "default": None})

    if args.kwarg:
        ann = parse_python_type(args.kwarg.annotation, source="parameter_annotation")
        params.append({"name": f"**{args.kwarg.arg}", "kind": "kwargs", "type": ann.raw, "type_annotation": ann.as_dict(), "has_default": False, "default": None})

    return params
