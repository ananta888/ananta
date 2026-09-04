#!/usr/bin/env python3
"""Reject DSPy/provider bypass imports and executable serializers outside the adapter."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "agent", ROOT / "ananta_contracts", ROOT / "worker")
ALLOWED_DSPY_ROOT = (ROOT / "worker" / "optimization" / "dspy").resolve()


def violations() -> list[str]:
    findings: list[str] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                findings.append(f"{path.relative_to(ROOT)}:parse_error:{type(exc).__name__}")
                continue
            inside_adapter = ALLOWED_DSPY_ROOT in path.resolve().parents or path.resolve() == ALLOWED_DSPY_ROOT
            optimization_scope = "dspy" in path.parts or "optimization" in path.parts
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".", 1)[0] for alias in node.names}
                    if (not inside_adapter and "dspy" in names) or (
                        optimization_scope and names & {"litellm", "cloudpickle", "mcp"}
                    ):
                        findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:forbidden_import")
                elif isinstance(node, ast.ImportFrom):
                    root_name = str(node.module or "").split(".", 1)[0]
                    if (not inside_adapter and root_name == "dspy") or (
                        optimization_scope and root_name in {"litellm", "cloudpickle", "mcp"}
                    ):
                        findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:forbidden_import")
                elif optimization_scope and isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (
                        node.func.attr in {"load", "loads"}
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in {"pickle", "cloudpickle"}
                    ):
                        findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:executable_deserialization")
                    if node.func.attr in {"LM", "ReAct", "CodeAct", "RLM", "Avatar"}:
                        findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:unsafe_dspy_capability")
                elif optimization_scope and isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in {"eval", "exec"}:
                        findings.append(f"{path.relative_to(ROOT)}:{node.lineno}:dynamic_execution")
    return sorted(findings)


def main() -> int:
    findings = violations()
    if findings:
        print("\n".join(findings))
        return 1
    print("dspy-import-boundaries-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
