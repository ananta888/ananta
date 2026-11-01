#!/usr/bin/env python3
"""Generate the env-var reference from code (CLN-065).

The 50 KB hand-maintained ``.env.example`` drifted against the code (723
undocumented, 242 documented-but-unused names at audit time). This generator
sweeps the two real sources of truth — ``os.getenv``/``os.environ`` lookups
and pydantic-settings ``validation_alias`` declarations in agent/config*.py —
and emits a deterministic reference.

Usage:
    python scripts/generate_env_reference.py > docs/env-reference.generated.md

Exit codes:
- 0: reference written to stdout (and orphan report on stderr)
- 2: scan failure
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SCAN_ROOTS = ("agent", "worker", "scripts", "src", "ananta_codecompass", "client_surfaces")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def _env_literals(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in {"getenv", "environ.get", "get"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and ENV_KEY.match(arg.value):
                    found.add(arg.value)
        if isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "environ" and isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str) and ENV_KEY.match(node.slice.value):
                    found.add(node.slice.value)
    return found


def sweep() -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for root in SCAN_ROOTS:
        for path in Path(root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            names = _env_literals(tree)
            # validation_alias Strings ueber rohen Text (pydantic nutzt verschachtelte Calls)
            raw = path.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"validation_alias\s*=\s*[\"']([A-Z][A-Z0-9_]+)[\"']", raw):
                names.add(m.group(1))
            for m in re.finditer(r"validation_alias\s*=\s*AliasChoices\(([^)]*)\)", raw):
                names.update(re.findall(r"[\"']([A-Z][A-Z0-9_]+)[\"']", m.group(1)))
            for n in names:
                used.setdefault(n, set()).add(str(path))
    return used


def main() -> int:
    try:
        used = sweep()
    except Exception as exc:  # pragma: no cover
        print(f"generate_env_reference: {exc}", file=sys.stderr)
        return 2

    lines = [
        "# Umgebungsvariablen — generierte Referenz (CLN-065)",
        "",
        "GENERIERT via `python scripts/generate_env_reference.py`; nicht handpflegen.",
        "Quellen: `os.getenv`/`os.environ`-Sweeps + `validation_alias`-Deklarationen.",
        "",
        "| Variable | Quelldateien |",
        "|---|---|",
    ]
    for name in sorted(used):
        files = ", ".join(f"`{f}`" for f in sorted(used[name])[:3])
        more = "" if len(used[name]) <= 3 else f" (+{len(used[name]) - 3})"
        lines.append(f"| `{name}` | {files}{more} |")
    lines.append("")
    lines.append(f"Gesamt: {len(used)} im Code gelesene Env-Variablen.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
