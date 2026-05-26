from __future__ import annotations

import difflib
from typing import Any


class DiffEngine:
    def build_document(
        self,
        *,
        left: dict[str, Any],
        right: dict[str, Any] | None = None,
        render_mode: str = "unified",
        max_lines: int = 2000,
    ) -> dict[str, Any]:
        if left.get("content_type") == "patch":
            return self._from_patch(left.get("patch") or "", max_lines=max_lines)
        if right is not None and left.get("content_type") == "text" and right.get("content_type") == "text":
            return self._from_pair_text(
                left_text=str(left.get("text") or ""),
                right_text=str(right.get("text") or ""),
                path=str(right.get("path") or left.get("path") or "artifact.txt"),
                render_mode=render_mode,
                max_lines=max_lines,
            )
        if left.get("content_type") == "pair":
            return self._from_pair_text(
                left_text=str(left.get("left_text") or ""),
                right_text=str(left.get("right_text") or ""),
                path=str(left.get("path") or "unknown.txt"),
                render_mode=render_mode,
                max_lines=max_lines,
            )
        return {
            "schema": "diff_document.v1",
            "files": [],
            "stats": {"files": 0, "hunks": 0, "truncated": False},
            "reason_code": "unsupported_inputs",
        }

    def _from_patch(self, patch_text: str, *, max_lines: int) -> dict[str, Any]:
        lines = patch_text.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            lines = lines[:max_lines]
        files: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        current_hunk: dict[str, Any] | None = None
        for line in lines:
            if line.startswith("diff --git "):
                parts = line.split()
                old_path = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else "unknown"
                new_path = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else old_path
                current = {
                    "path": new_path,
                    "old_path": old_path,
                    "status": "modified",
                    "binary": False,
                    "unsupported": False,
                    "hunks": [],
                }
                files.append(current)
                current_hunk = None
                continue
            if current is None:
                continue
            if line.startswith("Binary files "):
                current["binary"] = True
                current["unsupported"] = True
                continue
            if line.startswith("@@"):
                current_hunk = {"header": line, "old_lines": [], "new_lines": [], "rows": []}
                current["hunks"].append(current_hunk)
                continue
            if current_hunk is None:
                continue
            if line.startswith("-"):
                current_hunk["old_lines"].append(line[1:])
            elif line.startswith("+"):
                current_hunk["new_lines"].append(line[1:])
            elif line.startswith(" "):
                text = line[1:]
                current_hunk["old_lines"].append(text)
                current_hunk["new_lines"].append(text)
        return {
            "schema": "diff_document.v1",
            "files": files,
            "stats": {
                "files": len(files),
                "hunks": sum(len(file.get("hunks") or []) for file in files),
                "truncated": truncated,
            },
        }

    def _from_pair_text(
        self,
        *,
        left_text: str,
        right_text: str,
        path: str,
        render_mode: str,
        max_lines: int,
    ) -> dict[str, Any]:
        if "\x00" in left_text or "\x00" in right_text:
            return {
                "schema": "diff_document.v1",
                "files": [
                    {
                        "path": path,
                        "status": "binary",
                        "binary": True,
                        "unsupported": True,
                        "hunks": [],
                    }
                ],
                "stats": {"files": 1, "hunks": 0, "truncated": False},
            }
        left_lines = left_text.splitlines()
        right_lines = right_text.splitlines()
        if render_mode == "side_by_side":
            rows = []
            for entry in difflib.ndiff(left_lines, right_lines):
                tag = entry[:2]
                value = entry[2:]
                if tag == "- ":
                    rows.append({"old": value, "new": "", "status": "removed"})
                elif tag == "+ ":
                    rows.append({"old": "", "new": value, "status": "added"})
                elif tag == "  ":
                    rows.append({"old": value, "new": value, "status": "unchanged"})
            truncated = len(rows) > max_lines
            if truncated:
                rows = rows[:max_lines]
            return {
                "schema": "diff_document.v1",
                "files": [
                    {
                        "path": path,
                        "status": "modified",
                        "binary": False,
                        "unsupported": False,
                        "hunks": [{"header": "@@ pair @@", "old_lines": left_lines, "new_lines": right_lines, "rows": rows}],
                    }
                ],
                "stats": {"files": 1, "hunks": 1, "truncated": truncated},
            }
        diff = list(
            difflib.unified_diff(
                left_lines,
                right_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        truncated = len(diff) > max_lines
        if truncated:
            diff = diff[:max_lines]
        hunk = {"header": "@@ unified @@", "old_lines": [], "new_lines": [], "rows": []}
        for line in diff:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                hunk["header"] = line
                continue
            if line.startswith("-"):
                hunk["old_lines"].append(line[1:])
            elif line.startswith("+"):
                hunk["new_lines"].append(line[1:])
            elif line.startswith(" "):
                text = line[1:]
                hunk["old_lines"].append(text)
                hunk["new_lines"].append(text)
        return {
            "schema": "diff_document.v1",
            "files": [
                {
                    "path": path,
                    "status": "modified",
                    "binary": False,
                    "unsupported": False,
                    "hunks": [hunk],
                }
            ],
            "stats": {"files": 1, "hunks": 1, "truncated": truncated},
        }

