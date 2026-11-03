"""Worker workspace filesystem port: pure path and snapshot operations.

No Flask, no task policy, no transport: this module is the filesystem seam
of the worker workspace domain (SRP). ``WorkerWorkspaceService`` composes
it with policy, context and artifact-sync concerns.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

IGNORED_WORKSPACE_SEGMENTS = {".ananta", "artifacts", "__pycache__"}
DIFF_MAX_FILE_BYTES = 512 * 1024


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def is_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except Exception:
        return False


def normalize_requested_output_dir(*, output_dir: str, workspace_root: Path) -> Path:
    raw = str(output_dir or "").strip()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return Path(os.path.abspath(str(candidate)))
    rel_parts = list(candidate.parts)
    if "project-workspaces" in rel_parts:
        anchor_index = rel_parts.index("project-workspaces")
        rel_parts = [segment for segment in rel_parts[anchor_index + 1 :] if segment]
        if rel_parts:
            candidate = Path(*rel_parts)
        else:
            candidate = Path(".")
    return Path(os.path.abspath(str(workspace_root / candidate)))


def try_remap_project_workspace_path(*, requested: Path, workspace_root: Path) -> Path | None:
    """Map host-mirrored project workspace paths into the container workspace root.

    Example: /home/user/repo/project-workspaces/foo -> /project-workspaces/foo
    """
    parts = list(requested.parts)
    try:
        anchor_index = parts.index("project-workspaces")
    except ValueError:
        return None
    rel_parts = [segment for segment in parts[anchor_index + 1 :] if segment]
    remapped = (workspace_root / Path(*rel_parts)).resolve() if rel_parts else workspace_root
    try:
        if os.path.commonpath([str(remapped), str(workspace_root)]) != str(workspace_root):
            return None
    except Exception:
        return None
    return remapped


def is_absolute_project_workspace_path(path: Path) -> bool:
    normalized = Path(os.path.abspath(str(path)))
    parts = list(normalized.parts)
    return len(parts) >= 2 and parts[1] == "project-workspaces"


def is_tracked_relative_path(rel: str, *, ignored_segments: set[str] = IGNORED_WORKSPACE_SEGMENTS) -> bool:
    parts = [part for part in Path(str(rel or "")).parts if part]
    return not any(part in ignored_segments for part in parts)


def iter_workspace_files(
    root: Path,
    *,
    tracked_only: bool = True,
    ignored_segments: set[str] = IGNORED_WORKSPACE_SEGMENTS,
) -> Iterator[tuple[Path, str]]:
    if not root.exists():
        return
    for current_root, dirnames, filenames in os.walk(root):
        if tracked_only:
            dirnames[:] = [name for name in dirnames if name not in ignored_segments]
        current_root_path = Path(current_root)
        for name in filenames:
            path = current_root_path / name
            rel = str(path.relative_to(root)).replace("\\", "/")
            if tracked_only and not is_tracked_relative_path(rel, ignored_segments=ignored_segments):
                continue
            yield path, rel


def snapshot_tree(
    root: Path,
    *,
    tracked_only: bool,
    ignored_segments: set[str] = IGNORED_WORKSPACE_SEGMENTS,
) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return snapshot
    for path, rel in iter_workspace_files(root, tracked_only=tracked_only, ignored_segments=ignored_segments):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def interactive_terminal_baseline_dir(workspace_dir: Path) -> Path:
    return workspace_dir / ".ananta" / "interactive-baseline"


def read_text_lines_for_diff(
    path: Path, *, max_bytes: int = DIFF_MAX_FILE_BYTES
) -> tuple[list[str] | None, str | None]:
    if not path.exists():
        return [], None
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return None, f"unreadable file: {exc}"
    if len(payload) > max_bytes:
        return None, f"skipped large file ({len(payload)} bytes)"
    if b"\x00" in payload:
        return None, "binary file"
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("utf-8", errors="replace")
    return text.splitlines(keepends=True), None


def detect_changed_files(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    changed: set[str] = set()
    for rel, sig in after.items():
        if before.get(rel) != sig:
            changed.add(rel)
    for rel in before.keys():
        if rel not in after:
            changed.add(rel)
    return sorted(changed)
