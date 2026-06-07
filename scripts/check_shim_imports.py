#!/usr/bin/env python3
"""Detect remaining imports from the team_blueprint_service compatibility shim.

The 765-line team_blueprint_service.py was split in WFG-029 into 5
SRP modules. A 12-month re-export shim stays at the old import path
to avoid breaking 8 consumer sites. This script scans agent/ and
tests/ for any import of a deprecated symbol so we know when the shim
is safe to remove.

Exit codes:
  0  No shim imports found — safe to remove the shim in a cleanup PR.
  1  N shim imports found — cleanup not yet possible.
  2  Script error (bad CLI args, IO error).

Usage:
  python scripts/check_shim_imports.py            # human-readable output
  python scripts/check_shim_imports.py --ci       # CI mode: prints GH-Actions
                                                   summary + non-zero exit
                                                   on remaining imports
  python scripts/check_shim_imports.py --json     # JSON output
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_PATH = "agent/services/team_blueprint_service.py"

# Public symbols re-exported by the shim. The CI gate fires while
# ANY of these are still imported from the shim path.
SHIM_SYMBOLS = {
    "BlueprintSaveResult",
    "PersistBlueprintChildrenResult",
    "RoleLinkSpec",
    "TemplateBootstrapSpec",
    "_ensure_default_templates_once",
    "_ensure_role_for_blueprint_role_in_session",
    "_materialize_blueprint_artifacts_in_session",
    "_reconcile_seed_blueprints_once",
    "_reconcile_seed_templates_once",
    "_reconcile_system_prompts_once",
    "_serialize_blueprint_snapshot",
    "ensure_default_templates",
    "instantiate_blueprint",
    "persist_blueprint_children",
    "persist_blueprint_children_in_session",
    "reconcile_seed_blueprints",
    "reconcile_seed_templates",
    "reconcile_system_prompts",
    "save_blueprint",
    "serialize_blueprint_snapshot",
}

# Regex: from agent.services.team_blueprint_service import X
#   or:  from .team_blueprint_service import X
#   or:  from agent.services.team_blueprint_service import (X, Y, Z)
#   or:  import agent.services.team_blueprint_service
FROM_SHIM_RE = re.compile(
    r"from\s+(?:agent\.services|\.)\s*team_blueprint_service\s+import\s+"
    r"(?P<symbols>[A-Za-z0-9_,\s*()]+)"
)
# Plain `import team_blueprint_service` (rare) — treat the whole module
# as a shim hit.
IMPORT_SHIM_RE = re.compile(
    r"(?<![\w.])import\s+agent\.services\.team_blueprint_service\b"
)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for sub in ("agent", "tests", "scripts"):
        root = REPO_ROOT / sub
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            # Skip the shim itself (it obviously imports from its own
            # submodules to build the re-export surface).
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                continue
            if str(rel) == SHIM_PATH:
                continue
            # Skip venv / node_modules / project-workspaces (per
            # AGENTS.md, project-workspaces is gitignored runtime).
            parts = rel.parts
            if any(p in {"venv", "node_modules", "project-workspaces", ".venv"} for p in parts):
                continue
            files.append(path)
    return files


def _extract_symbols(group: str) -> list[str]:
    """Parse `X, Y as Z, (A, B), *` from the import group."""
    cleaned = group.replace("\n", " ").replace("(", " ").replace(")", " ")
    out: list[str] = []
    for tok in cleaned.split(","):
        tok = tok.strip()
        if not tok or tok == "*":
            if tok == "*":
                out.append("*")
            continue
        # Strip `as Alias` suffix
        head = tok.split()[0]
        if head:
            out.append(head)
    return out


def scan() -> list[dict]:
    hits: list[dict] = []
    for path in _iter_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        # The detector script itself contains the literal string
        # `agent.services.team_blueprint_service` inside its regex. Skip it.
        if rel == "scripts/check_shim_imports.py":
            continue
        for match in FROM_SHIM_RE.finditer(text):
            syms = _extract_symbols(match.group("symbols"))
            recognized = [s for s in syms if s in SHIM_SYMBOLS or s == "*"]
            if not recognized:
                continue
            # Compute line number
            line_no = text[: match.start()].count("\n") + 1
            hits.append({
                "file": rel,
                "line": line_no,
                "symbols": recognized,
                "kind": "from_import",
            })
        for match in IMPORT_SHIM_RE.finditer(text):
            # The detector script itself contains the literal module
            # path inside its regex; the earlier path-skip already
            # handled it, so this loop won't see it.
            line_no = text[: match.start()].count("\n") + 1
            hits.append({
                "file": rel,
                "line": line_no,
                "symbols": ["<module>"],
                "kind": "import_module",
            })
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ci", action="store_true", help="CI mode: non-zero exit when shim imports remain.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    try:
        hits = scan()
    except Exception as exc:  # noqa: BLE001
        print(f"check_shim_imports: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"shim_path": SHIM_PATH, "hit_count": len(hits), "hits": hits}, indent=2))
        return 0 if (not args.ci or not hits) else 1

    if not hits:
        print(f"OK: 0 imports from {SHIM_PATH}. The shim is safe to remove in a cleanup PR.")
        return 0

    print(f"FOUND {len(hits)} import(s) from deprecated shim {SHIM_PATH}:")
    for h in hits:
        syms = ", ".join(h["symbols"])
        print(f"  {h['file']}:{h['line']}  {h['kind']:13}  {syms}")
    print()
    print("Action: migrate these imports to one of the new submodules:")
    print("  - agent.services.team_template_bootstrap_service")
    print("  - agent.services.team_blueprint_persistence_service")
    print("  - agent.services.team_blueprint_reconciliation_service")
    print("  - agent.services.team_blueprint_instantiation_service")
    print("  - agent.services.team_system_prompt_reconciliation_service")
    print("Then re-run this script. Exit 0 means the shim can be removed.")
    return 1 if args.ci else 0


if __name__ == "__main__":
    sys.exit(main())
