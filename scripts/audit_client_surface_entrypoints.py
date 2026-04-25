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
SURFACE_STATUS_FILE = "data/client_surface_runtime_status.json"
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
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/build.properties",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/**/*.java",
            "scripts/smoke_eclipse_runtime_bootstrap.py",
        ],
        "runtime_required_patterns": [
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/build.properties",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/commands/EclipseCommandRegistry.java",
            "scripts/smoke_eclipse_runtime_bootstrap.py",
            "scripts/smoke_eclipse_runtime_headless.py",
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
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/META-INF/MANIFEST.MF",
        ],
        "runtime_required_patterns": [
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/views/EclipseViewsExtensionRegistry.java",
            "scripts/smoke_eclipse_runtime_bootstrap.py",
            "scripts/smoke_eclipse_runtime_headless.py",
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
            "scripts/smoke_nvim_runtime.py",
        ],
        "runtime_required_patterns": [
            "client_surfaces/nvim_runtime/lua/ananta/*.lua",
            "client_surfaces/nvim_runtime/plugin/*.vim",
            "scripts/smoke_nvim_runtime.py",
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
    "vscode_plugin": {
        "runtime_patterns": [
            "client_surfaces/vscode_extension/package.json",
            "client_surfaces/vscode_extension/src/**/*.ts",
            "client_surfaces/vscode_extension/test/**/*.test.ts",
            ".github/workflows/quality-and-docs.yml",
        ],
        "runtime_required_patterns": [
            "client_surfaces/vscode_extension/package.json",
            "client_surfaces/vscode_extension/src/extension.ts",
            "client_surfaces/vscode_extension/src/runtime/backendClient.ts",
            "client_surfaces/vscode_extension/test/extension.smoke.test.ts",
            ".github/workflows/quality-and-docs.yml",
        ],
        "foundation_patterns": [
            "docs/vscode-plugin-scope-boundary.md",
            "docs/vscode-extension-architecture.md",
            "tests/test_vscode_extension_bootstrap.py",
        ],
        "docs_patterns": [
            "docs/vscode-extension-build-and-package.md",
            "docs/vscode-extension-user-guide.md",
            "docs/vscode-extension-developer-smoke-checklist.md",
        ],
    },
}


DONE_CLAIM_RULES: dict[str, list[tuple[str, int, int]]] = {
    "tui_surface": [("CSH-T", 5, 10), ("TVM-T", 29, 38), ("CRT-T", 9, 13)],
    "eclipse_plugin": [("CSH-T", 11, 17), ("EAC-T", 33, 58), ("TEST-T", 17, 20)],
    "eclipse_views_extension": [("ECL-T", 27, 50), ("EAC-T", 45, 53), ("TEST-T", 19, 20)],
    "nvim_plugin": [("TVM-T", 13, 22), ("CRT-T", 14, 17), ("CRT-T", 19, 19), ("TEST-T", 13, 15)],
    "vim_plugin": [("TVM-T", 23, 28)],
    "vscode_plugin": [("VSC-T", 1, 36)],
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
    runtime_required_patterns = list(rules.get("runtime_required_patterns") or [])
    runtime_requirements_met = all(_matched_paths(paths, [pattern]) for pattern in runtime_required_patterns)
    foundation_matches = _matched_paths(paths, rules["foundation_patterns"])
    docs_matches = _matched_paths(paths, rules["docs_patterns"])

    if runtime_matches and runtime_requirements_met:
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


def collect_done_task_ids(todo_payload: dict[str, Any]) -> set[str]:
    return {
        str(task.get("id"))
        for task in list(todo_payload.get("tasks") or [])
        if task.get("status") == "done" and task.get("id")
    }


def load_surface_status(root: Path) -> dict[str, str]:
    path = root / SURFACE_STATUS_FILE
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("surface_status")
    if not isinstance(status, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in status.items():
        out[str(key)] = str(value).strip().lower()
    return out


def build_blocking_warnings(
    surface_reports: dict[str, dict[str, Any]],
    done_claims: dict[str, list[str]],
    done_task_ids: set[str],
    surface_status: dict[str, str],
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
    for surface_name, declared_status in surface_status.items():
        classification = str(surface_reports.get(surface_name, {}).get("classification", "missing"))
        if declared_status in {"runtime_mvp", "runtime_complete"} and classification != "real_implementation":
            warnings.append(
                (f"surface={surface_name} declared_status={declared_status} but classification={classification}")
            )
        if declared_status == "deferred" and done_claims.get(surface_name):
            warnings.append(
                (
                    f"surface={surface_name} is deferred but runtime claim tasks are marked done: "
                    f"{done_claims[surface_name]}"
                )
            )
    for vim_gate_task in ("CRT-T18", "TEST-T16"):
        if vim_gate_task not in done_task_ids:
            continue
        vim_status = surface_status.get("vim_plugin", "")
        if vim_status != "deferred":
            warnings.append(f"{vim_gate_task} done requires surface_status.vim_plugin=deferred")
    return warnings


def generate_report(root: Path, todo_payload: dict[str, Any] | None) -> dict[str, Any]:
    paths = collect_existing_paths(root)
    surfaces = {surface_name: classify_surface(surface_name, paths) for surface_name in sorted(SURFACE_RULES.keys())}
    parsed_todo = todo_payload or {}
    done_claims = collect_done_claims(parsed_todo)
    done_task_ids = collect_done_task_ids(parsed_todo)
    surface_status = load_surface_status(root)
    warnings = build_blocking_warnings(surfaces, done_claims, done_task_ids, surface_status)
    return {
        "schema": "client_surface_entrypoint_audit_v1",
        "repository_root": str(root),
        "surfaces": surfaces,
        "surface_status": surface_status,
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
