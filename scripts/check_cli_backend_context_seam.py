#!/usr/bin/env python3
"""Detector: cli_backends must reach services only through the DI context.

CLN-052: agent/cli_backends/* may import ``agent.services`` solely in
``context.py`` (the CliBackendContext service-locator seam). Every other
module must resolve service handles via ``default_context`` so tests can
monkeypatch one seam instead of import sites.

Exit codes:
- 0: clean
- 1: direct agent.services imports outside context.py
- 2: detector failure

Usage:
    python scripts/check_cli_backend_context_seam.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path("agent/cli_backends")
ALLOWED_MODULE = "agent/cli_backends/context.py"


def _module_level_and_lazy_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def main() -> int:
    if not ROOT.exists():
        print("check_cli_backend_context_seam: cli_backends missing", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.as_posix().endswith(ALLOWED_MODULE) or "__pycache__" in path.parts:
            continue
        for imported in _module_level_and_lazy_imports(path):
            if imported == "agent.services" or imported.startswith("agent.services."):
                violations.append(f"{path}: imports {imported}")

    if violations:
        print("check_cli_backend_context_seam FAILED — use default_context instead:")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("check_cli_backend_context_seam OK — services reached via default_context only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
