from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ARCHIVED_TRACK_FILES = {
    "todo_last.json",
    "todo.security.json",
    "todo.domain.json",
    "todo.ananta-worker.json",
}
NON_TRACK_FILES = {"todo.schema.json", "todo.track.schema.json"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate cross-track todo dependencies.")
    parser.add_argument("--root", default=".", help="Repository root containing todo*.json files.")
    parser.add_argument(
        "--tracks",
        nargs="*",
        help="Optional explicit track file list (e.g. todo.json todo.kritis.json).",
    )
    return parser.parse_args()


def _looks_like_task_entry(node: dict[str, Any]) -> bool:
    if "id" not in node or "title" not in node:
        return False
    if "task_ids" in node:
        return False
    return "status" in node or "acceptance_criteria" in node


def _iter_dict_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dict_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_dict_nodes(value)


def _collect_task_dependencies(payload: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in _iter_dict_nodes(payload):
        if not isinstance(node, dict) or not _looks_like_task_entry(node):
            continue
        task_id = str(node.get("id") or "").strip()
        if not task_id:
            continue
        depends_on = [str(dep).strip() for dep in list(node.get("depends_on") or []) if str(dep).strip()]
        result[task_id] = depends_on
    return result


def _split_ref(ref: str) -> tuple[str, str] | None:
    text = str(ref or "").strip()
    if ":" not in text:
        return None
    file_name, task_id = text.split(":", 1)
    file_name = file_name.strip()
    task_id = task_id.strip()
    if not file_name or not task_id:
        return None
    return file_name, task_id


def _detect_cycles(edges: dict[str, set[str]]) -> list[str]:
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    emitted: set[tuple[str, ...]] = set()

    def dfs(node: str) -> None:
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for nxt in sorted(edges.get(node, set())):
            if nxt in visiting:
                idx = stack.index(nxt)
                cycle_nodes = tuple(stack[idx:] + [nxt])
                if cycle_nodes not in emitted:
                    emitted.add(cycle_nodes)
                    errors.append(
                        "circular_cross_track_dependency: " + " -> ".join(cycle_nodes)
                    )
                continue
            dfs(nxt)
        stack.pop()
        visiting.discard(node)
        visited.add(node)

    for node in sorted(edges):
        if node not in visited:
            dfs(node)
    return errors


def _discover_track_files(root_path: Path, explicit_tracks: list[str] | None = None) -> list[str]:
    if explicit_tracks:
        return [str(item).strip() for item in explicit_tracks if str(item).strip()]

    names: list[str] = []
    for path in sorted(root_path.glob("todo*.json")):
        name = path.name
        if name in ARCHIVED_TRACK_FILES or name in NON_TRACK_FILES:
            continue
        names.append(name)
    return names


def validate_cross_track_dependencies(*, root_path: Path, track_files: list[str]) -> list[str]:
    errors: list[str] = []
    task_index: dict[str, dict[str, list[str]]] = {}

    for track_file in track_files:
        path = root_path / track_file
        if not path.exists():
            errors.append(f"missing_track_file: {track_file}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        task_index[track_file] = _collect_task_dependencies(payload)

    cross_edges: dict[str, set[str]] = {}
    for source_file, tasks in task_index.items():
        for source_id, depends_on in tasks.items():
            for dep in depends_on:
                parsed = _split_ref(dep)
                if not parsed:
                    continue
                target_file, target_id = parsed
                source_ref = f"{source_file}:{source_id}"
                target_ref = f"{target_file}:{target_id}"
                if target_file in ARCHIVED_TRACK_FILES:
                    errors.append(
                        f"archived_track_reference: {source_ref} -> {target_ref} ({target_file} is archived/inactive)"
                    )
                    continue
                if target_file in NON_TRACK_FILES:
                    errors.append(
                        f"invalid_track_reference: {source_ref} -> {target_ref} ({target_file} is a schema file, not a task track)"
                    )
                    continue
                if target_file not in task_index:
                    target_path = root_path / target_file
                    if target_path.exists():
                        errors.append(
                            f"unknown_or_inactive_track_reference: {source_ref} -> {target_ref} "
                            f"(add {target_file} to active validation scope)"
                        )
                    else:
                        errors.append(
                            f"missing_track_file_reference: {source_ref} -> {target_ref} ({target_file} not found)"
                        )
                    continue
                if target_id not in task_index[target_file]:
                    errors.append(
                        f"missing_target_task_id: {source_ref} -> {target_ref} "
                        f"({target_id} not found in {target_file})"
                    )
                    continue
                cross_edges.setdefault(source_ref, set()).add(target_ref)

    errors.extend(_detect_cycles(cross_edges))
    return errors


def main() -> int:
    args = _parse_args()
    root_path = Path(args.root).resolve()
    track_files = _discover_track_files(root_path, args.tracks)
    errors = validate_cross_track_dependencies(root_path=root_path, track_files=track_files)
    if errors:
        print("cross-track-dependencies-invalid")
        for item in errors:
            print(f"- {item}")
        return 2
    print("cross-track-dependencies-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

