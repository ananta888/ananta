from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


DYNAMIC_BLOCKERS = {
    "eval": "eval_usage",
    "exec": "exec_usage",
    "__import__": "dynamic_import",
}

DYNAMIC_ATTR_FUNCS = {"getattr", "setattr", "delattr", "hasattr"}


@dataclass
class DynamicFeature:
    code: str
    reason: str
    path: str
    line: int
    severity: str = "blocker"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "path": self.path, "line": self.line, "severity": self.severity}


@dataclass
class DynamicDetectionResult:
    features: list[DynamicFeature] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(f.severity == "blocker" for f in self.features)

    @property
    def blocker_codes(self) -> list[str]:
        return [f.code for f in self.features if f.severity == "blocker"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_blockers": self.has_blockers,
            "blocker_codes": self.blocker_codes,
            "features": [f.as_dict() for f in self.features],
        }


def detect_dynamic_features(source: str, path: str = "<string>") -> DynamicDetectionResult:
    result = DynamicDetectionResult()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        result.features.append(DynamicFeature("syntax_error", "Cannot parse — syntax error", path, 0, "blocker"))
        return result
    visitor = _DynamicVisitor(path, result)
    visitor.visit(tree)
    return result


class _DynamicVisitor(ast.NodeVisitor):
    def __init__(self, path: str, result: DynamicDetectionResult) -> None:
        self.path = path
        self.result = result

    def visit_Call(self, node: ast.Call) -> None:
        fn_name = _call_name(node)
        if fn_name in DYNAMIC_BLOCKERS:
            self.result.features.append(DynamicFeature(
                DYNAMIC_BLOCKERS[fn_name], f"{fn_name}() call detected", self.path, node.lineno, "blocker"
            ))
        elif fn_name in DYNAMIC_ATTR_FUNCS:
            # getattr/setattr with dynamic name (second arg not a string constant) is a blocker
            if len(node.args) >= 2 and not isinstance(node.args[1], ast.Constant):
                self.result.features.append(DynamicFeature(
                    "dynamic_attribute_access", f"{fn_name}() with dynamic attribute name", self.path, node.lineno, "blocker"
                ))
            else:
                self.result.features.append(DynamicFeature(
                    "attribute_introspection", f"{fn_name}() call — may be safe if attribute is constant", self.path, node.lineno, "warning"
                ))
        elif fn_name in ("importlib.import_module", "import_module"):
            self.result.features.append(DynamicFeature(
                "dynamic_import", "importlib.import_module() — dynamic import", self.path, node.lineno, "blocker"
            ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Metaclass detection
        for kw in node.keywords:
            if kw.arg == "metaclass":
                metaclass = ast.unparse(kw.value)
                if metaclass not in ("ABCMeta", "abc.ABCMeta", "type"):
                    self.result.features.append(DynamicFeature(
                        "custom_metaclass", f"metaclass={metaclass} — custom metaclass not supported", self.path, node.lineno, "blocker"
                    ))
        # Monkey patching detection: assigning to class attributes after class definition
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Detect monkey patching: SomeClass.method = something
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                if target.value.id[0].isupper():
                    self.result.features.append(DynamicFeature(
                        "monkey_patching", f"Possible monkey patch: {target.value.id}.{target.attr} = ...", self.path, node.lineno, "warning"
                    ))
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.result.features.append(DynamicFeature(
                "bare_except", "bare except: without exception type — blocks auto-transform", self.path, node.lineno, "blocker"
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.result.features.append(DynamicFeature(
                    "star_import", f"star import from {node.module or '?'}", self.path, node.lineno, "warning"
                ))
        self.generic_visit(node)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return ast.unparse(node.func)
    return ""
