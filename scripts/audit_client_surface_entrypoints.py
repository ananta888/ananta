from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKIP_PATH_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
}

SURFACE_RULES: dict[str, dict[str, list[str]]] = {
    "tui_surface": {
        "runtime_patterns": [
            "client_surfaces/tui_runtime/**/__main__.py",
            "client_surfaces/tui_runtime/**/app.py",
            "client_surfaces/tui_runtime/**/main.py",
        ],
        "foundation_patterns": [
            "agent/services/editor_tui_surface_foundation_service.py",
            "agent/tui_contract.py",
            "tests/test_editor_tui_surface_foundation_service.py",
            "tests/test_tui_interaction_contract.py",
        ],
        "docs_patterns": [
            "docs/tui-user-operator-guide.md",
            "docs/editor-tui-foundation.md",
            "docs/editor-tui-smoke-checklists.md",
        ],
    },
    "eclipse_plugin": {
        "runtime_patterns": [
            "**/plugin.xml",
            "**/META-INF/MANIFEST.MF",
            "**/build.properties",
            "**/feature.xml",
        ],
        "foundation_patterns": [
            "agent/services/eclipse_plugin_adapter_foundation_service.py",
            "tests/test_eclipse_plugin_adapter_foundation_service.py",
        ],
        "docs_patterns": [
            "docs/eclipse-plugin-adapter-foundation.md",
            "docs/eclipse-plugin-views-extension-foundation.md",
        ],
    },
    "eclipse_views_extension": {
        "runtime_patterns": [
            "**/plugin.xml",
            "**/META-INF/MANIFEST.MF",
        ],
        "foundation_patterns": [
            "agent/services/eclipse_plugin_adapter_foundation_service.py",
            "tests/test_eclipse_plugin_adapter_foundation_service.py",
        ],
        "docs_patterns": [
            "docs/eclipse-plugin-views-extension-foundation.md",
        ],
    },
    "nvim_plugin": {
        "runtime_patterns": [
            "client_surfaces/nvim_runtime/lua/ananta/*.lua",
            "client_surfaces/nvim_runtime/plugin/*.vim",
        ],
        "foundation_patterns": [
            "agent/services/editor_tui_surface_foundation_service.py",
            "tests/test_editor_tui_surface_foundation_service.py",
        ],
        "docs_patterns": [
            "docs/nvim-plugin-user-guide.md",
            "docs/editor-tui-foundation.md",
        ],
    },
    "vim_plugin": {
        "runtime_patterns": [
            "client_surfaces/vim_compat/plugin/*.vim",
            "client_surfaces/vim_compat/autoload/*.vim",
        ],
        "foundation_patterns": [
            "agent/services/editor_tui_surface_foundation_service.py",
        ],
        "docs_patterns": [
            "docs/plugin-vs-tui-usage-guide.md",
            "docs/editor-tui-foundation.md",
        ],
    },
}


DONE_CLAIM_RULES: dict[str, list[tuple[str, int, int]]] = {
    "tui_surface": [("CSH-T", 5, 10), ("TVM-T", 29, 38)],
    "eclipse_plugin": [("CSH-T", 11, 17)],
    "eclipse_views_extension": [("ECL-T", 27, 50)],
    "nvim_plugin": [("TVM-T", 13, 22)],
    "vim_plugin": [("TVM-T", 23, 28)],
}


TASK_ID_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+-T)(?P<number>\d+)$")


def _normalize_rel_path(path: Path, *, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def collect_existing_paths(root: Path) -> set[str]:
    files: set[str] = set()
    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [name for name in dirnames if name not in SKIP_PATH_PARTS]
        current_root_path = Path(current_root)
        for filename in filenames:
            file_path = current_root_path / filename
            files.add(_normalize_rel_path(file_path, root=root))
    return files


def _matched_paths(paths: set[str], patterns: list[str]) -> list[str]:
    matches: set[str] = set()
    for pattern in patterns:
        for path in paths:
            if fnmatch.fnmatch(path, pattern):
                matches.add(path)
    return sorted(matches)


def classify_surface(surface_name: str, paths: set[str]) -> dict[str, Any]:
    rules = SURFACE_RULES[surface_name]
    runtime_matches = _matched_paths(paths, rules["runtime_patterns"])
    foundation_matches = _matched_paths(paths, rules["foundation_patterns"])
    docs_matches = _matched_paths(paths, rules["docs_patterns"])

    if runtime_matches:
        classification = "real_implementation"
    elif foundation_matches:
        classification = "foundation_only"
    elif docs_matches:
        classification = "docs_only"
    else:
        classification = "missing"

    return {
        "classification": classification,
        "runtime_evidence": runtime_matches,
        "foundation_evidence": foundation_matches,
        "docs_evidence": docs_matches,
    }


def _task_matches_rule(task_id: str, prefix: str, start: int, end: int) -> bool:
    match = TASK_ID_PATTERN.match(task_id)
    if not match:
        return False
    return match.group("prefix") == prefix and start <= int(match.group("number")) <= end


def collect_done_claims(todo_payload: dict[str, Any]) -> dict[str, list[str]]:
    done_task_ids = [
        str(task.get("id"))
        for task in list(todo_payload.get("tasks") or [])
        if task.get("status") == "done" and task.get("id")
    ]
    claims: dict[str, list[str]] = {}
    for surface_name, ranges in DONE_CLAIM_RULES.items():
        matching_ids: list[str] = []
        for task_id in done_task_ids:
            if any(_task_matches_rule(task_id, prefix, start, end) for prefix, start, end in ranges):
                matching_ids.append(task_id)
        claims[surface_name] = sorted(set(matching_ids))
    return claims


def build_blocking_warnings(
    surface_reports: dict[str, dict[str, Any]],
    done_claims: dict[str, list[str]],
) -> list[str]:
    warnings: list[str] = []
    for surface_name, claimed_ids in done_claims.items():
        if not claimed_ids:
            continue
        classification = str(surface_reports[surface_name]["classification"])
        if classification != "real_implementation":
            warnings.append(
                (
                    f"surface={surface_name} has done claims ({', '.join(claimed_ids)}) "
                    f"but classification={classification}"
                )
            )
    return warnings


def generate_report(root: Path, todo_payload: dict[str, Any] | None) -> dict[str, Any]:
    paths = collect_existing_paths(root)
    surfaces = {surface_name: classify_surface(surface_name, paths) for surface_name in sorted(SURFACE_RULES.keys())}
    done_claims = collect_done_claims(todo_payload or {})
    warnings = build_blocking_warnings(surfaces, done_claims)
    return {
        "schema": "client_surface_entrypoint_audit_v1",
        "repository_root": str(root),
        "surfaces": surfaces,
        "done_claims": done_claims,
        "blocking_warnings": warnings,
        "ok": not warnings,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit executable evidence for client surfaces.")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repository root path (default: current project root).",
    )
    parser.add_argument(
        "--todo",
        default="todo.json",
        help="Todo JSON path relative to --root (default: todo.json).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output file path relative to --root.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Exit non-zero if blocking warnings are present.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(args.root).resolve()
    todo_path = Path(args.todo)
    if not todo_path.is_absolute():
        todo_path = root / todo_path

    todo_payload: dict[str, Any] = {}
    if todo_path.exists():
        todo_payload = json.loads(todo_path.read_text(encoding="utf-8"))

    report = generate_report(root, todo_payload)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")

    if args.fail_on_warning and report["blocking_warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
