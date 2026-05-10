"""File read/write and patch path enforcement.

EW-T016: read_file requires code_read capability and workspace scope match.
          patch_propose produces PatchArtifact without modifying main tree.
          patch_apply requires patch_apply capability and approval when required.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── PatchArtifact ─────────────────────────────────────────────────────────────

@dataclass
class PatchHunk:
    path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    diff: str     # unified diff text for this hunk


@dataclass
class PatchArtifact:
    """Produced by patch_propose — main tree is NOT modified. EW-T016."""
    artifact_id: str
    task_id: str
    provenance: str              # task_id + step that produced it
    hunks: list[PatchHunk] = field(default_factory=list)
    summary: str = ""
    applied: bool = False        # True only after patch_apply succeeds

    @property
    def patch_hash(self) -> str:
        content = "\n".join(h.diff for h in self.hunks)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @property
    def paths_affected(self) -> list[str]:
        return list(dict.fromkeys(h.path for h in self.hunks))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "patch_artifact",
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "provenance": self.provenance,
            "patch_hash": self.patch_hash,
            "paths_affected": self.paths_affected,
            "hunk_count": len(self.hunks),
            "applied": self.applied,
            "summary": self.summary,
        }


# ── FilePolicyResult ──────────────────────────────────────────────────────────

@dataclass
class FilePolicyResult:
    allowed: bool
    reason_code: str
    detail: str = ""


# ── FilePolicy ────────────────────────────────────────────────────────────────

class FilePolicy:
    """Enforces read/write/patch scoping against filesystem_scope. EW-T016."""

    def check_read(
        self,
        path: str,
        *,
        read_paths: list[str],
        workspace_root: str = "",
    ) -> FilePolicyResult:
        """Verify a read_file call is within declared read scope."""
        if not path or not path.strip():
            return FilePolicyResult(False, "tool_schema_invalid", "path is empty")
        if not read_paths and not workspace_root:
            return FilePolicyResult(True, "no_scope_constraint")
        if workspace_root and self._within(path, workspace_root):
            return FilePolicyResult(True, "file_allow")
        for allowed in read_paths:
            if self._within(path, allowed):
                return FilePolicyResult(True, "file_allow")
        return FilePolicyResult(
            False, "file_scope_violation",
            f"path {path!r} is outside declared read_paths",
        )

    def check_write(
        self,
        path: str,
        *,
        write_paths: list[str],
        workspace_root: str = "",
    ) -> FilePolicyResult:
        """Verify a write operation is within declared write scope."""
        if not path or not path.strip():
            return FilePolicyResult(False, "tool_schema_invalid", "path is empty")
        if not write_paths and not workspace_root:
            return FilePolicyResult(True, "no_scope_constraint")
        if workspace_root and self._within(path, workspace_root):
            return FilePolicyResult(True, "file_allow")
        for allowed in write_paths:
            if self._within(path, allowed):
                return FilePolicyResult(True, "file_allow")
        return FilePolicyResult(
            False, "file_scope_violation",
            f"path {path!r} is outside declared write_paths",
        )

    def check_patch_paths(
        self,
        artifact: PatchArtifact,
        *,
        write_paths: list[str],
        workspace_root: str = "",
    ) -> FilePolicyResult:
        """Verify all paths in a PatchArtifact are within write scope."""
        for path in artifact.paths_affected:
            result = self.check_write(path, write_paths=write_paths, workspace_root=workspace_root)
            if not result.allowed:
                return FilePolicyResult(
                    False, "patch_scope_violation",
                    f"patch targets {path!r} which is outside write scope",
                )
        return FilePolicyResult(True, "patch_scope_ok")

    def build_patch_artifact(
        self,
        *,
        artifact_id: str,
        task_id: str,
        provenance: str,
        raw_diff: str,
        summary: str = "",
        write_paths: list[str] | None = None,
        workspace_root: str = "",
    ) -> tuple[PatchArtifact, FilePolicyResult]:
        """Parse raw_diff into a PatchArtifact and validate scope.

        Returns (artifact, policy_result). Caller must check policy_result.allowed.
        """
        hunks = _parse_unified_diff(raw_diff)
        artifact = PatchArtifact(
            artifact_id=artifact_id,
            task_id=task_id,
            provenance=provenance,
            hunks=hunks,
            summary=summary,
        )
        if write_paths is None:
            write_paths = []
        scope_result = self.check_patch_paths(
            artifact, write_paths=write_paths, workspace_root=workspace_root
        )
        return artifact, scope_result

    # ── Internals ──────────────────────────────────────────────────────────────

    def _within(self, path: str, scope: str) -> bool:
        try:
            p = Path(path)
            # Relative paths (from unified diffs) are treated as relative to scope
            if not p.is_absolute() and scope:
                p = Path(scope) / p
            resolved = p.resolve()
            scope_resolved = Path(scope).resolve()
            resolved.relative_to(scope_resolved)
            return True
        except (ValueError, OSError):
            # Fall back to string prefix check for non-existent paths
            norm_path = str(Path(path))
            norm_scope = str(Path(scope))
            if not norm_scope.endswith("/"):
                norm_scope += "/"
            return norm_path.startswith(norm_scope) or norm_path == norm_scope.rstrip("/")


# ── Unified diff parser ───────────────────────────────────────────────────────

def _parse_unified_diff(diff_text: str) -> list[PatchHunk]:
    """Minimal unified diff parser: extracts per-file hunks."""
    hunks: list[PatchHunk] = []
    current_path = ""
    current_hunk_lines: list[str] = []
    old_start = new_start = old_lines = new_lines = 0

    for line in (diff_text or "").splitlines():
        if line.startswith("--- "):
            _flush_hunk(hunks, current_path, current_hunk_lines,
                        old_start, old_lines, new_start, new_lines)
            current_hunk_lines = []
        elif line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_path = path
        elif line.startswith("@@ "):
            _flush_hunk(hunks, current_path, current_hunk_lines,
                        old_start, old_lines, new_start, new_lines)
            current_hunk_lines = [line]
            old_start, old_lines, new_start, new_lines = _parse_hunk_header(line)
        elif current_hunk_lines is not None:
            current_hunk_lines.append(line)

    _flush_hunk(hunks, current_path, current_hunk_lines,
                old_start, old_lines, new_start, new_lines)
    return hunks


def _flush_hunk(
    hunks: list[PatchHunk],
    path: str,
    lines: list[str],
    old_start: int,
    old_lines: int,
    new_start: int,
    new_lines: int,
) -> None:
    if path and lines:
        hunks.append(PatchHunk(
            path=path,
            old_start=old_start,
            old_lines=old_lines,
            new_start=new_start,
            new_lines=new_lines,
            diff="\n".join(lines),
        ))


def _parse_hunk_header(line: str) -> tuple[int, int, int, int]:
    """Parse '@@ -a,b +c,d @@' into (a, b, c, d)."""
    import re
    m = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not m:
        return 0, 0, 0, 0
    return (
        int(m.group(1)), int(m.group(2) or 1),
        int(m.group(3)), int(m.group(4) or 1),
    )
